// One builder per scene. Each returns {edge, hdl, acts, body, sig, aria}: HTML strings plus a
// signature of everything that is NOT animated per frame. The page only replaces the DOM when the
// signature changes, so hover, transitions and the ring survive the 10 Hz snapshots.
import { GLYPH, ICON } from "./glyphs.js";
import { words, ruleSentence, proofLines, stopLatency, freshReply, evidenceLines, reranked, sceneInfo, autoInfo, transcriptLine } from "./model.js";

const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

const btn = (act, icon, label, cls = "") =>
  `<button class="ib ${cls}" type="button" tabindex="-1" data-hit data-act="${act}" aria-label="${esc(label)}" title="${esc(label)}">${ICON[icon]}</button>`;
const tbtn = (act, text, cls = "") =>
  `<button class="tb ${cls}" type="button" tabindex="-1" data-hit data-act="${act}">${esc(text)}</button>`;

/** A pictogram the runtime sent (`card.pictogram`, a path it serves) or the glyph for the card kind. */
function picture(card) {
  const p = card?.pictogram;
  if (typeof p === "string" && p.startsWith("/") && !p.startsWith("//")) return `<img src="${esc(p)}" alt="" draggable="false" />`;
  return GLYPH[card?.kind] || GLYPH._;
}

const evHtml = (lines, lit) =>
  lines.length ? `<div class="evs${lit ? " lit" : ""}">${lines.map((l) => `<div class="ev1"${l.source ? ` title="${esc(l.source)}"` : ""}>${esc(l.text)}</div>`).join("")}</div>` : "";

const heardHtml = (h) => (h ? `<div class="heard">${h.speaker ? `<b>${esc(h.speaker)}</b>` : ""}<span>\u201C${esc(h.text)}\u201D</span></div>` : "");

const ring = () =>
  `<div class="ring" id="ring" data-state="wait" aria-hidden="true"><svg viewBox="0 0 36 36"><circle class="rb" cx="18" cy="18" r="15"/><circle class="rf" id="ringf" cx="18" cy="18" r="15" transform="rotate(-90 18 18)"/></svg><b id="ringv" class="num">…</b></div>`;

const confBars = (word) => `<span class="conf" data-w="${esc(word)}" aria-hidden="true"><i></i><i></i><i></i></span>`;

// ── cards ────────────────────────────────────────────────────────────────────
function cards(s, mem, ctx, confirming) {
  const sel = s.selection;
  const list = sel.cards;
  const i = Math.max(0, list.findIndex((c) => c.highlighted));
  const card = list[i];
  const others = list.length > 1 ? Array.from({ length: list.length - 1 }, (_, k) => list[(i + 1 + k) % list.length]) : [];
  const evl = evidenceLines(card);
  const rr = reranked(mem, ctx.now);
  const lit = !!rr && rr.top === card.id;
  const ev = evHtml(evl, lit);
  const stale = card.stale === true || sceneInfo(s)?.stale === true;
  const heard = transcriptLine(s);
  const asked = typeof card.asked_by === "string" && card.asked_by ? card.asked_by : "";
  const word = card.confidence || "";
  const live = !ctx.replay;
  let acts = "";
  if (live && !confirming) acts = btn("step", "next", "Next card (double twitch)") + btn("dismiss", "no", "Refuse this card (brace)") + btn("accept", "ok", "Select this card (twitch)", "go");
  if (live && confirming) acts = btn("step", "back", "Back to the cards (double twitch)") + btn("dismiss", "no", "Refuse (brace)") + btn("accept", "ok", "Confirm (twitch)", "go");
  let cap = "";
  // the arm's answer to someone else's request ("Waiting for Pranay") is captioned under the card
  const answer = !confirming && asked ? freshReply(s, mem, ctx.now - 15000) : "";
  if (confirming) {
    const said = s.reply && String(s.reply).startsWith(card.label) ? String(s.reply) : "";
    // The runtime speaks this exact question (h2m/app.py); it is only repeated here if the reply is not it.
    cap = said || `${card.label}? Twitch again to confirm, or double twitch to go back.`;
  }
  const body = `<div class="hero${confirming ? " big" : ""}" data-kind="${esc(card.kind)}">
      <div class="pic">${picture(card)}</div>
      <div class="txt">${asked ? `<div class="askrow">asked by ${esc(asked)}</div>` : ""}<div class="lbl">${esc(card.label)}</div>${ev}</div>
      ${stale ? `<div class="wsc">waiting for the scene</div>` : ring()}
    </div>
    ${stale ? `<div class="stale" id="stale"></div>` : ""}${heardHtml(heard)}${answer ? `<div class="cap">${esc(answer)}</div>` : ""}
    ${confirming ? `<div class="cap">${esc(cap)}</div>` : others.length ? `<div class="stack" aria-hidden="true">${others.map((c) => `<span${rr && rr.top === c.id ? ` class="rise"` : ""}><i>${GLYPH[c.kind] || GLYPH._}</i>${esc(c.label)}</span>`).join("")}</div>` : ""}`;
  const hdl = `${confBars(word)}<span class="w">${confirming ? "confirm" : esc(word || "card")}</span>${list.length > 1 ? `<span class="pos num">${i + 1}/${list.length}</span>` : ""}`;
  return {
    edge: stale ? "grey" : confirming ? "grip-hot" : "grip",
    wait: !!asked,
    hdl,
    acts,
    body,
    aria: `${confirming ? "Confirm: " : ""}${card.label}`,
    sig: ["c", confirming, sel.menu, sel.highlight, card.id, card.label, word, JSON.stringify(evl), asked, cap, answer, stale, heard && [heard.speaker, heard.text], rr && rr.at, list.map((c) => c.id).join(), live, card.pictogram || "", sel.checking],
    key: `${sel.menu}:${sel.highlight}:${card.id}`,
    pin: { card_id: card.id, menu: sel.menu }, // what was DRAWN: clicks are pinned to it, not to the latest snapshot
  };
}

// ── pre-approved routine: the veto ring (UI.md 10.1) ────────────────────────
function auto(s, mem, ctx) {
  const au = autoInfo(s);
  const card = au.card;
  const evl = evidenceLines(card);
  const heard = transcriptLine(s);
  return {
    edge: "grip",
    hdl: `<span class="w">routine</span>${au.chain ? `<span class="pos num">${au.chain.n} steps</span>` : ""}`,
    acts: "",
    body: `<div class="veto" id="veto" data-total="${au.total}">
        <svg class="vsvg" aria-hidden="true"><rect id="vrect" class="vf" pathLength="100"/></svg>
        <div class="hero"><div class="pic">${picture(card)}</div>
          <div class="txt"><div class="lbl">${esc(card.label)}</div>${evHtml(evl, false)}</div>
          <b id="vnum" class="vnum num"></b></div>
        <div class="vsay">brace to cancel</div>
      </div>${heardHtml(heard)}`,
    aria: `${card.label}. Starting automatically. Brace to cancel.`,
    sig: ["a", au.id, card.label, JSON.stringify(evl), au.total, au.chain && au.chain.n, heard && [heard.speaker, heard.text]],
    key: `a:${au.id}`,
  };
}

// ── a routine that found nothing to do: one line, three seconds ─────────────
function quiet(s, mem) {
  return {
    edge: "line",
    hdl: `<span class="w">routine</span>`,
    acts: "",
    body: `<div class="hero quiet"><div class="pic">${GLYPH.wait}</div><div class="txt"><div class="lbl">${esc(mem.quiet.text)}</div></div></div>`,
    aria: mem.quiet.text,
    sig: ["q", mem.quiet.text],
  };
}

// ── pending request (voice, typed, click) ───────────────────────────────────
function pending(s, mem, ctx) {
  const p = s.pending;
  const c = p.contract || {};
  // A request that a card raised keeps the card's own label ("Bring wooden block"), not a rebuilt one.
  const fromCard = mem.cardLabel && c.object_name && mem.cardLabel.toLowerCase().includes(String(c.object_name).toLowerCase()) && ctx.now - mem.cardAt < 30000;
  const what = fromCard ? mem.cardLabel : `${words(c.verb)}${c.object_name ? ` ${c.object_name}` : ""}`.trim() || "A request";
  const why = p.decision?.reason || "";
  return {
    edge: "warn",
    hdl: `<span class="w">waiting for you</span>`,
    acts: "",
    body: `<div class="hero asking"><div class="pic">${GLYPH.ask}</div><div class="txt"><div class="lbl">${esc(what)}</div>${why ? `<div class="ev">${esc(why)}</div>` : ""}</div><b id="exp" class="exp num"></b></div>
      ${ctx.replay ? "" : `<div class="btns">${tbtn("cancel", "Cancel")}${tbtn("confirm", "Confirm", "go")}</div>`}`,
    aria: `Waiting for you: ${what}`,
    sig: ["p", what, why, ctx.replay],
  };
}

// ── running strip ────────────────────────────────────────────────────────────
function running(s, mem, ctx) {
  const a = s.arm;
  const target = (s.objects || []).find((o) => o.id === a.target)?.name;
  const label = mem.runLabel || `${words(a.skill, "moving")}${target ? ` ${target}` : ""}`;
  const virtual = a.hardware === false;
  const ch = autoInfo(s)?.chain;
  const chainHtml = ch ? `<div class="chain" aria-label="step ${ch.i} of ${ch.n}">${Array.from({ length: ch.n }, (_, k) => `<span class="${k + 1 < ch.i ? "done" : k + 1 === ch.i ? "cur" : ""}">${k + 1}/${ch.n}</span>`).join("")}</div>` : "";
  return {
    edge: "line",
    hdl: `<span class="dot go"></span><span class="w">moving</span>`,
    acts: "",
    body: `<div class="run">
      <div class="rl"><b>${esc(label)}</b>${a.stage ? `<span class="stg">${esc(words(a.stage))}</span>` : ""}</div>
      <div class="bar"><i id="bar"></i></div>
      ${chainHtml}
      <div class="rs"><span>brace to stop</span>${virtual ? `<span class="tag">VIRTUAL ARM</span>` : ""}</div></div>`,
    aria: `Moving: ${label}`,
    sig: ["r", label, a.stage, virtual, ch && [ch.i, ch.n]],
  };
}

// ── stopped ──────────────────────────────────────────────────────────────────
function stopped(s, mem, ctx) {
  const a = s.arm;
  const fault = a.state === "fault";
  const since = mem.stopAt == null ? 0 : ctx.now - mem.stopAt;
  const small = !ctx.far && since > 10000 && !fault; // desk: after a while, a one-line red strip
  const reply = freshReply(s, mem, (mem.stopAt ?? 0) - 500);
  const lat = stopLatency(s);
  const braceHit = mem.brace && mem.stopAt != null && Math.abs(s.t - mem.brace.t - since / 1000) < 3 && Number.isFinite(mem.brace.detect_ms) ? mem.brace : null;
  const why = a.reason || "";
  const title = fault ? "Fault" : "Stopped";
  const virtual = a.hardware === false;
  const body = small
    ? `<div class="stp small"><b>${title}</b><span>${esc(why)}</span></div>`
    : `<div class="stp"><div class="big">${title}</div>${why ? `<div class="why">${esc(why)}</div>` : ""}
      ${lat ? `<div class="lat"><b class="num">${lat.ms}</b> ms to stop</div>` : ""}
      ${braceHit ? `<div class="lat">brace seen <b class="num">${Math.round(braceHit.detect_ms)}</b> ms after onset</div>` : ""}
      ${reply ? `<div class="say">${esc(reply)}</div>` : ""}</div>`;
  return {
    edge: "brace",
    hdl: `${small ? "" : `<span class="w">stop</span>`}${virtual ? `<span class="tag">VIRTUAL ARM</span>` : ""}`,
    acts: "",
    body,
    aria: `${title}. ${why}`,
    sig: ["s", title, why, small, lat?.ms, braceHit?.seq, reply, virtual],
  };
}

// ── investigation (Muse hue) ─────────────────────────────────────────────────
function investigate(s, mem, ctx) {
  const inc = s.learning.incident;
  const tools = mem.tools.slice(-5);
  const phase =
    inc.state === "capturing"
      ? "Capturing the moment"
      : s.learning.muse === false
        ? "No model key: using the plain fallback"
        : "Working out what happened";
  return {
    edge: "muse",
    hdl: `<span class="w">investigating</span>${inc.source ? `<span class="tag">${esc(words(inc.source))}</span>` : ""}`,
    acts: "",
    body: `<div class="inv"><div class="big">Looking at what just happened</div><div class="ph"><span class="sweep" aria-hidden="true"></span>${esc(phase)}</div>
      ${tools.length ? `<ul class="tools">${tools.map((e) => {
        const r = e.line ?? e.result ?? e.detail ?? e.summary ?? "";
        return `<li><b>${esc(e.tool ?? e.name ?? "tool")}</b>${r !== "" ? `<span>${esc(typeof r === "string" ? r : JSON.stringify(r))}</span>` : ""}</li>`;
      }).join("")}</ul>` : ""}</div>`,
    aria: `Investigating. ${phase}`,
    sig: ["i", inc.state, inc.source, phase, tools.map((e) => e.seq).join()],
  };
}

// ── rule card ────────────────────────────────────────────────────────────────
function rule(s, mem, ctx) {
  const p = s.learning.proposal;
  const sentence = ruleSentence(p.rule);
  const proof = proofLines(p);
  const live = !ctx.replay;
  return {
    edge: "muse",
    hdl: `<span class="w">new rule</span>`,
    acts: "",
    body: `<div class="rule"><div class="rsent">${esc(sentence)}</div>
      ${p.say && p.say !== sentence ? `<div class="ev">${esc(p.say)}</div>` : ""}
      ${proof.length ? `<ul class="proof">${proof.map((t) => `<li class="${t.ok ? "y" : "n"}"><i>${t.ok ? ICON.tick : ICON.cross}</i>${esc(t.text)}</li>`).join("")}</ul>` : ""}
      ${live ? `<div class="btns">${tbtn("reject", "Not now")}${tbtn("approve", "Save rule", "go")}</div>` : ""}
      <div class="how">${live ? "Squeeze, say yes, or click Save. A twitch does not save a rule." : "Replay: the approval is scripted."}<b id="exp" class="exp num"></b></div></div>`,
    aria: `New rule: ${sentence}`,
    sig: ["u", p.rule?.id, sentence, p.say, JSON.stringify(proof), live],
    ruleId: p.rule?.id,
  };
}

// ── quiet states ─────────────────────────────────────────────────────────────
function disarmed(s) {
  const dis = s.arm.state === "disconnected";
  return {
    edge: "grey",
    hdl: `<span class="w">${dis ? "no arm" : "disarmed"}</span>`,
    acts: "",
    body: `<div class="hero quiet"><div class="pic">${GLYPH.off}</div><div class="txt"><div class="lbl">${dis ? "Arm not connected" : "Disarmed"}</div><div class="ev">${dis ? "start the runtime with an arm" : "arm it from the console"}</div></div></div>`,
    aria: dis ? "Arm not connected" : "Disarmed. Arm it from the console.",
    sig: ["d", dis],
  };
}

function idle(s, mem, ctx) {
  const sel = s?.selection || {};
  let line;
  if (ctx.replay) line = sel.enabled ? "No cards right now" : "No cards in this simulation";
  else if (!sel.enabled) line = "Cards are off";
  else line = sel.blocked || "No cards right now";
  const armWord = words(s?.arm?.state, "");
  return {
    edge: "line",
    hdl: `<span class="w">armed</span>${armWord ? `<span class="pos">${esc(armWord)}</span>` : ""}`,
    acts: "",
    body: `<div class="hero quiet"><div class="pic">${GLYPH.wait}</div><div class="txt"><div class="lbl">${esc(line)}</div></div></div>`,
    aria: line,
    sig: ["n", line, armWord],
  };
}

function lost() {
  return {
    edge: "warn",
    hdl: `<span class="w">no link</span>`,
    acts: "",
    body: `<div class="hero quiet"><div class="pic">${GLYPH.off}</div><div class="txt"><div class="lbl">Lost the runtime</div><div class="ev">reconnecting</div></div></div>`,
    aria: "Lost the connection to the runtime. Reconnecting.",
    sig: ["x"],
  };
}

export function build(scene, s, mem, ctx) {
  switch (scene) {
    case "cards": return cards(s, mem, ctx, false);
    case "confirm": return cards(s, mem, ctx, true);
    case "pending": return pending(s, mem, ctx);
    case "auto": return auto(s, mem, ctx);
    case "quiet": return quiet(s, mem);
    case "running": return running(s, mem, ctx);
    case "stopped": return stopped(s, mem, ctx);
    case "investigate": return investigate(s, mem, ctx);
    case "rule": return rule(s, mem, ctx);
    case "disarmed": return disarmed(s);
    case "lost": return lost();
    case "idle": return idle(s, mem, ctx);
    default: return null;
  }
}
