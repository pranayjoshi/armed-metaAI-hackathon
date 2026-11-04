// What the Card knows and which scene it is in. Pure bookkeeping over the /api/live snapshot:
// nothing here invents a value (docs/UI.md section 8, rule 1). Fields the runtime has not added yet
// (evidence, asked_by, pictogram, stop latency, incident.tool, verifier) are read when present.
//
// OPTIONAL SNAPSHOT CONTRACT (docs/UI.md 2.6, 10.1, 10.4). Every field below may be absent; the page
// then renders nothing for it. None of it is computed or guessed here.
//   s.calls: [{model, for?, started_at_ms?, elapsed_ms?, ms?|null}]
//        Meta API calls. In flight: ms is null/absent; the page shows "calling <model> · 1.2s" and counts
//        up locally from elapsed_ms (preferred) or from s.t*1000 - started_at_ms (same clock as s.t).
//        Landed: ms is the measured latency; the page shows it for 1 s from when it first sees ms. The
//        runtime should keep a landed entry in the list for at least ~1.5 s. `for` is only a tooltip.
//   s.chips: [str]
//        Names of models / Armed components that ran for the current state. Muse hue for muse-* and
//        sam-3.1, Armed accent for "Armed ..." and "EMG decoder". A chip exists only if listed.
//   s.selection.cards[i].evidence: [str | {text, age_s?, source?}]
//        Reason lines. age_s renders as "text · 4 s". `source` is a tooltip only.
//   s.selection.cards[i].asked_by: str | null      the asker; shows the "asked by X" chip + slow edge pulse.
//   s.selection.cards[i].stale: bool (optional)    this card's target is stale (else s.scene.stale applies).
//   s.selection.auto: {card_id, remaining_s, total_s, chain?: {i, n}}
//        A pre-approved routine. While remaining_s > 0 the card shows a draining veto ring and
//        "brace to cancel". chain.i / chain.n (1-based i) show "1/2 · 2/2" on the running strip; keep
//        `auto` (with chain) in the snapshot while the routine runs. Gone = the ring ended / was vetoed.
//   s.scene: {scan_age_s, stale, limit_s}
//        Age of the last SAM scan. stale true (or scan_age_s > limit_s) -> grey edge, "last seen N s
//        ago", and "waiting for the scene" instead of the reaction ring.
//   s.quiet: {text, until_ms?}
//        A routine that found nothing to do. One line for 3 s from when the page first sees the text,
//        then it hides (until_ms is accepted but the page times the 3 s itself). Never a sound.
//   s.transcript: {speaker, text}
//        What was just heard, labelled with the speaker. The arm's answer is the existing s.reply,
//        captioned under it when the card is asked-by someone.

export const words = (v, fb = "") => (v ? String(v).replaceAll("_", " ") : fb);

/** Per-page memory: things that cannot be read off one snapshot (when a stop was first seen, the
 * card that is now moving, new events since the last frame). */
export function createMemory() {
  return {
    seq: -1, // highest event seq seen
    primed: false, // the first snapshot is history: it is never replayed as live
    stopAt: null, // performance.now() when the arm was first seen STOPPED
    armState: null,
    disarmAt: null, // when the arm was first seen disarmed / disconnected
    cardLabel: null, // label of the card last seen asking "confirm?"
    cardAt: -1e9,
    runLabel: null, // the task label shown while the arm moves (that card, if one just ran)
    reply: null,
    replyAt: -1e9,
    tools: [], // incident.tool events of the incident being investigated
    incidentKey: null,
    brace: null, // latest emg.brace event {t, detect_ms, trigger}
    toast: null, // {text, until}
    ring: null, // reaction ring bookkeeping
    lastCards: 0,
    calls: new Map(), // key -> {model, for, base, baseAt, ms, landedAt, seenAt}
    order: null, // {menu, ids, hid} the card order last seen, to spot a re-rank
    rerank: null, // {at, top} a re-sort happened with the highlight unchanged
    quiet: null, // {text, at}
    noticed: null, // {key, text, sub, at}: the thinker's latest remark or held suggestion
    noticedKey: null,
    vetoId: null, // card id of the routine whose ring was last up
  };
}

/** Fold a snapshot into memory. Returns the NEW events (empty on the first snapshot). */
export function ingest(mem, s, now) {
  const arm = s.arm || {};
  const sel = s.selection || {};
  const conf = (sel.cards || []).find((c) => c.confirm);
  if (conf) {
    mem.cardLabel = conf.label;
    mem.cardAt = now;
  }
  if (arm.state !== mem.armState) {
    mem.stopAt = arm.state === "stopped" ? now : null;
    if (arm.state === "disarmed" || arm.state === "disconnected") mem.disarmAt = now;
    // The task label of a motion is the card that was just confirmed, if there was one.
    if (arm.state === "moving") mem.runLabel = now - mem.cardAt < 15000 ? mem.cardLabel : null;
    mem.armState = arm.state;
  }
  if (s.reply !== mem.reply) {
    mem.reply = s.reply;
    mem.replyAt = now;
  }
  const events = s.events || [];
  const fresh = events.filter((e) => e.seq > mem.seq);
  for (const e of fresh) mem.seq = Math.max(mem.seq, e.seq);
  const live = mem.primed ? fresh : [];
  mem.primed = true;
  const inc = s.learning?.incident;
  const key = inc && (inc.state === "capturing" || inc.state === "explaining") ? `${inc.source}:${inc.t}` : null;
  if (key !== mem.incidentKey) {
    mem.incidentKey = key;
    if (key) mem.tools = [];
  }
  // incident.tool is not in the snapshot's event list until the runtime adds it; accept it when it is.
  for (const e of fresh) {
    if (e.kind === "incident.tool" && mem.incidentKey) mem.tools.push(e);
    if (e.kind === "emg.brace") mem.brace = e;
  }
  if (mem.toast && now > mem.toast.until) mem.toast = null;
  trackCalls(mem, s, now);
  trackRerank(mem, sel, now);
  const qt = typeof s.quiet?.text === "string" ? s.quiet.text : "";
  if (!qt) mem.quiet = null;
  else if (mem.quiet?.text !== qt) mem.quiet = { text: qt, at: now };
  if (sel.auto && Number.isFinite(sel.auto.remaining_s) && sel.auto.remaining_s > 0) mem.vetoId = sel.auto.card_id;
  // The thinker (state.think): a remark it said, or a suggestion it could not ask (held,
  // disarmed). A queued suggestion is already the pending scene.
  const notes = Array.isArray(s.think?.notes) ? s.think.notes : [];
  const n = notes[notes.length - 1];
  if (n && n.t !== mem.noticedKey) {
    mem.noticedKey = n.t;
    const held = n.suggest && !n.suggest.queued ? n.suggest : null;
    if (n.said || held) {
      const armed = s.arm?.state === "ready" || s.arm?.state === "moving";
      mem.noticed = {
        text: n.said || held.ask,
        sub: held ? (held.why ? `because ${held.why}` : "") + (armed ? "" : `${held.why ? " · " : ""}say “arm yourself” first`) : n.notice || "",
        at: now,
      };
    }
  }
  return live;
}

export const toast = (mem, text, now, ms = 3600) => {
  mem.toast = { text: String(text || ""), until: now + ms };
};

/** The scene the Card should be in. `ctx`: {connected, replay, far, always, now} */
export function pickScene(s, mem, ctx) {
  if (!ctx.connected) {
    // Not an error until it has been a while (page load) or the link was up and dropped.
    if (!ctx.lostOk) return ctx.far || ctx.always ? "idle" : "hidden";
    return "lost";
  }
  if (!s) return ctx.far || ctx.always ? "idle" : "hidden";
  const arm = s.arm || {};
  const sel = s.selection || {};
  const inc = s.learning?.incident;
  const busy = inc && (inc.state === "capturing" || inc.state === "explaining");
  const sinceStop = mem.stopAt == null ? Infinity : ctx.now - mem.stopAt;
  if (arm.state === "moving") return "running";
  const au = autoInfo(s);
  const veto = au && au.card && au.remaining != null && au.total != null && au.remaining > 0 && !sel.confirming;
  if (arm.state === "stopped" && sinceStop < 1800) return "stopped";
  if (arm.state === "fault") return "stopped";
  if (busy) return "investigate";
  if (s.learning?.proposal) return "rule";
  if (arm.state === "stopped") return "stopped";
  if (veto) return "auto";
  if (s.pending) return "pending";
  if (sel.enabled && sel.cards?.length) return sel.confirming ? "confirm" : "cards";
  if (mem.noticed && ctx.now - mem.noticed.at < 8000) return "noticed";
  if (mem.quiet && ctx.now - mem.quiet.at < 3000) return "quiet";
  if (arm.state === "disarmed" || arm.state === "disconnected") {
    // quiet: say it for a few seconds, then get out of the way (the bedside screen keeps it)
    if (ctx.far || ctx.always || (mem.disarmAt != null && ctx.now - mem.disarmAt < 6000)) return "disarmed";
    return "hidden";
  }
  return ctx.far || ctx.always || ctx.replay ? "idle" : "hidden";
}

/** The robot's own sentence, only if it was said after `since` (a stale reply is not shown). */
export const freshReply = (s, mem, since) => (s.reply && mem.replyAt >= since ? String(s.reply) : "");

/** Rule sentence, wording shared with the console's Learn view (web/views/learn/data.js). */
const DOES = {
  route_around: (r) => `take a route that stays clear of the ${r.avoid}`,
  lift_higher: (r) => `carry it higher, over the ${r.avoid}`,
  slow_down: (r) => `move slower near the ${r.avoid}`,
  grip_gentler: (r) => `hold it more gently near the ${r.avoid}`,
};
export function ruleSentence(r) {
  if (!r) return "";
  const does = (DOES[r.response] || (() => words(r.response)))(r);
  return `When carrying something ${(r.when_holding || []).join(" + ")}, ${does}.`;
}

/** Ticks for the rule card. ONLY what the proposal carries; nothing is ticked on the Card's own say-so.
 * Accepts `verifier` / `rehearsal` as {name: bool}, [{name|label, ok|passed}], or {ok|passed: bool, ...}. */
export function proofLines(p) {
  const out = [];
  const take = (src, lead) => {
    if (src == null) return;
    if (typeof src === "boolean") out.push({ text: lead, ok: src });
    else if (Array.isArray(src)) {
      for (const it of src) {
        if (it && typeof it === "object") {
          const ok = it.ok ?? it.passed ?? it.pass;
          const text = it.label ?? it.name ?? it.check;
          if (typeof ok === "boolean" && text) out.push({ text: words(text), ok });
        }
      }
    } else if (typeof src === "object") {
      const before = out.length;
      for (const [k, v] of Object.entries(src)) {
        if (typeof v === "boolean" && !["ok", "passed", "pass"].includes(k)) out.push({ text: words(k), ok: v });
      }
      const ok = src.ok ?? src.passed ?? src.pass;
      if (typeof ok === "boolean" && out.length === before) out.push({ text: lead, ok });
    }
  };
  take(p?.verifier, "verified");
  take(p?.rehearsal, "rehearsed");
  return out;
}

/** The measured stop latency, only if the snapshot has one. Never derived. */
export function stopLatency(s) {
  const a = s.arm || {};
  const v = a.stop_latency_ms ?? a.latency_ms;
  return Number.isFinite(v) ? { ms: Math.round(v) } : null;
}

// ── live calls (UI.md 10.1) ──────────────────────────────────────────────────
function trackCalls(mem, s, now) {
  const list = Array.isArray(s.calls) ? s.calls : [];
  const seen = new Set();
  for (const c of list) {
    if (!c || typeof c.model !== "string" || !c.model) continue;
    const key = `${c.model}|${c.started_at_ms ?? c.for ?? ""}`;
    seen.add(key);
    let m = mem.calls.get(key);
    if (!m) mem.calls.set(key, (m = { model: c.model, for: c.for || "", base: null, baseAt: now, ms: null, landedAt: 0 }));
    const landed = Number.isFinite(c.ms);
    if (landed && m.ms == null) {
      m.ms = Math.round(c.ms);
      m.landedAt = now;
    }
    if (!landed) {
      let el = Number.isFinite(c.elapsed_ms) ? c.elapsed_ms : Number.isFinite(c.started_at_ms) && Number.isFinite(s.t) ? s.t * 1000 - c.started_at_ms : null;
      if (el != null) {
        m.base = Math.max(0, el);
        m.baseAt = now;
      }
    }
  }
  for (const [k, m] of mem.calls) {
    // a call that left the list without a measurement is simply gone; nothing is made up for it
    if (!seen.has(k) && (m.ms == null || now - m.landedAt > 1000)) mem.calls.delete(k);
  }
}

/** [{model, text, landed}] for the chips: counting up while in flight, the measured ms for 1 s once landed. */
export function callChips(mem, now) {
  const out = [];
  for (const m of mem.calls.values()) {
    if (m.ms != null) {
      if (now - m.landedAt <= 1000) out.push({ key: `${m.model}|${m.for}`, model: m.model, label: m.model, val: `${m.ms} ms`, landed: true, tip: m.for });
    } else if (m.base != null) {
      out.push({ key: `${m.model}|${m.for}`, model: m.model, label: `calling ${m.model}`, val: `${((m.base + (now - m.baseAt)) / 1000).toFixed(1)}s`, landed: false, tip: m.for });
    }
  }
  return out;
}

/** Hue class for a chip name: Muse for muse-* / sam-*, Armed accent for Armed ... / EMG decoder. */
export const chipKind = (name) => (/^(muse|sam)/i.test(name) ? "muse" : /^(armed\b|emg decoder)/i.test(name) ? "armed" : "plain");

// ── evidence, re-ranking, staleness ──────────────────────────────────────────
/** Evidence as display lines: str, or {text, age_s?} -> "text · 4 s". */
export function evidenceLines(card) {
  const e = card?.evidence;
  const list = Array.isArray(e) ? e : typeof e === "string" && e ? [e] : [];
  const out = [];
  for (const it of list) {
    if (typeof it === "string") it && out.push({ text: it, source: "" });
    else if (it && typeof it.text === "string" && it.text) {
      out.push({ text: Number.isFinite(it.age_s) ? `${it.text} · ${Math.round(it.age_s)} s` : it.text, source: typeof it.source === "string" ? it.source : "" });
    }
  }
  return out.slice(0, 4);
}

/** A re-rank: same menu, same highlighted card, different order. The highlight never moves. */
function trackRerank(mem, sel, now) {
  const cards = sel?.cards || [];
  if (!sel?.enabled || !cards.length) {
    mem.order = null;
    return;
  }
  const ids = cards.map((c) => c.id);
  const hid = cards.find((c) => c.highlighted)?.id ?? sel.highlighted ?? null;
  const prev = mem.order;
  if (prev && prev.menu === sel.menu && prev.hid === hid && prev.ids.join() !== ids.join() && prev.ids.length === ids.length) {
    mem.rerank = { at: now, top: ids[0] };
  }
  mem.order = { menu: sel.menu, ids, hid };
}
export const reranked = (mem, now) => (mem.rerank && now - mem.rerank.at < 1600 ? mem.rerank : null);

/** The scene state: {stale, age} or null when the runtime sent none. */
export function sceneInfo(s) {
  const sc = s?.scene;
  if (!sc || typeof sc !== "object") return null;
  const age = Number.isFinite(sc.scan_age_s) ? sc.scan_age_s : null;
  const stale = sc.stale === true || (sc.stale == null && age != null && Number.isFinite(sc.limit_s) && age > sc.limit_s);
  return { stale, age };
}

/** The live veto: {card, remaining, total, chain} or null. */
export function autoInfo(s) {
  const a = s?.selection?.auto;
  if (!a || typeof a !== "object") return null;
  const chain = a.chain && Number.isFinite(a.chain.i) && Number.isFinite(a.chain.n) && a.chain.n > 0 ? { i: a.chain.i, n: a.chain.n } : null;
  return {
    card: (s.selection.cards || []).find((c) => c.id === a.card_id) || null,
    id: a.card_id,
    remaining: Number.isFinite(a.remaining_s) ? a.remaining_s : null,
    total: Number.isFinite(a.total_s) && a.total_s > 0 ? a.total_s : null,
    chain,
  };
}

export const transcriptLine = (s) =>
  s?.transcript && typeof s.transcript.text === "string" && s.transcript.text ? { speaker: String(s.transcript.speaker || ""), text: s.transcript.text } : null;
