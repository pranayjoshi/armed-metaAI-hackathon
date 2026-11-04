// The Card (docs/UI.md section 2) and far mode (section 3): one page, three places.
// It renders the runtime's snapshot and forwards a person's clicks to the same endpoints a twitch uses.
// It never decides: no card is chosen, accepted or invented here (UI.md section 8).
//
// Query: ?mode=far  bedside screen    ?corner=tr|tl|br|bl  desk position    ?film=1  always visible, clean frame
//        ?hosted=1  behave as inside the desktop shell (hidden when there is nothing to say)
//        ?transparent=1  no ground colour behind the panel
import { connect, post, ensureToken, isReplayPage } from "./net.js";
import { createMemory, ingest, pickScene, toast, callChips, chipKind, autoInfo, sceneInfo } from "./model.js";
import { build, esc } from "./views.js";
import { createMeter } from "./meter.js";
import { SOUNDS, unlockOnGesture } from "./audio.js";
import { ICON } from "./glyphs.js";

const $ = (id) => document.getElementById(id);
const q = new URLSearchParams(location.search);
const far = q.get("mode") === "far";
const film = q.get("film") === "1";
const corner = ["tr", "tl", "br", "bl"].includes(q.get("corner")) ? q.get("corner") : "tr";
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const host = window.armedHost || null; // the Electron shell's bridge, when there is one
const hosted = !!host || q.get("hosted") === "1";
const always = film || !hosted; // a plain browser tab has no shell to hide it: show a quiet idle panel

const root = $("root");
const panel = $("panel");
const body = $("body");
root.classList.add(far ? "far" : "desk", `c-${corner}`);
if (film) root.classList.add("film");
if (reduced) root.classList.add("reduced");
if (!hosted && q.get("transparent") !== "1") document.documentElement.classList.add("ground");
if (far) document.title = "Armed bedside";

// ── mute (sounds are off in far mode and under reduced motion regardless) ───
let muted = false;
try {
  muted = localStorage.getItem("armed.card.muted") === "1";
} catch {
  /* storage can be blocked; the card works without it */
}
const soundOn = () => !far && !reduced && !muted;
const play = (name) => soundOn() && SOUNDS[name]();
const paintMute = () => {
  $("mute").innerHTML = muted ? ICON.muted : ICON.sound;
  $("mute").setAttribute("aria-pressed", String(muted));
  $("mute").title = muted ? "Sounds are off" : "Sounds are on";
};
paintMute();
unlockOnGesture();

// ── state ───────────────────────────────────────────────────────────────────
const mem = createMemory();
const meter = createMeter($("meter"), { reduced });
let snap = null;
let snapAt = 0;
let connected = false;
let view = null; // the last built view
let scene = "hidden";
let visible = false;
let forced = null; // "show" | "hide" from window.armed
let readyAt = 0; // performance.now() when the panel is fully drawn (ack waits for it)
let leaveKind = "";
let hideTimer = 0;
let lastDismissAt = -1e9;
let ghost = null; // {el, at}: the refused card, fading
const replayNow = () => isReplayPage() || !!snap?.label || snap?.mode === "replay";
const nowMs = () => performance.now();
let everUp = false;
const ctxNow = () => ({ connected, lostOk: everUp || nowMs() > 2500, replay: replayNow(), far, always, now: nowMs() });

// ── desktop-shell hooks (UI.md section 7) ───────────────────────────────────
const listeners = new Set();
function setVisible(v) {
  if (v === visible) return;
  visible = v;
  clearTimeout(hideTimer);
  if (v) {
    host?.setVisible?.(true);
    listeners.forEach((fn) => safe(fn, true));
  } else {
    // the window goes away after the fade, so the exit is seen
    hideTimer = setTimeout(() => {
      if (!visible) host?.setVisible?.(false);
    }, 450);
    listeners.forEach((fn) => safe(fn, false));
  }
}
const safe = (fn, v) => {
  try {
    fn(v);
  } catch (err) {
    console.error("[card] visibility listener", err);
  }
};
let interactive = false;
function setInteractive(v) {
  v = !!v;
  if (v === interactive) return;
  interactive = v;
  host?.setInteractive?.(v);
}
window.armed = {
  /** The page owns its states; the shell owns the window. show() keeps the panel up even when idle. */
  show() {
    forced = "show";
    refresh();
  },
  /** hide() only wins over the quiet scenes: a moving, stopped, asking or investigating arm stays on screen. */
  hide() {
    forced = "hide";
    refresh();
  },
  auto() {
    forced = null;
    refresh();
  },
  onVisibility(fn) {
    listeners.add(fn);
    safe(fn, visible);
    return () => listeners.delete(fn);
  },
  setInteractive,
  get visible() {
    return visible;
  },
  get scene() {
    return scene;
  },
};

// ── rendering ───────────────────────────────────────────────────────────────
const QUIET = new Set(["idle", "disarmed", "hidden"]);
let sig = "";
function refresh() {
  const t = nowMs();
  let next = pickScene(snap, mem, ctxNow());
  if (forced === "show" && next === "hidden") next = "idle";
  if (forced === "hide" && QUIET.has(next)) next = "hidden";
  const v = next === "hidden" ? null : build(next, snap, mem, ctxNow());
  if (next === "hidden" || !v) {
    scene = "hidden";
    root.dataset.scene = "hidden";
    if (visible) {
      panel.dataset.leave = t - lastDismissAt < 1200 ? "refuse" : "";
      panel.classList.remove("on");
      setVisible(false);
    }
    return;
  }
  const changedScene = next !== scene;
  scene = next;
  root.dataset.scene = next;
  panel.dataset.edge = v.edge;
  panel.classList.toggle("wait", !!v.wait);
  const s = JSON.stringify(v.sig);
  if (s !== sig) {
    const prevKey = view?.key;
    sig = s;
    $("hdl").innerHTML = v.hdl;
    $("acts").innerHTML = v.acts;
    body.innerHTML = v.body;
    if (ghost && t - ghost.at < 1500 && scene === "cards") {
      const g = ghost.el;
      body.appendChild(g);
      setTimeout(() => g.remove(), 650);
    }
    ghost = null;
    if (changedScene) animate(body, "scene-in");
    else if (v.key && v.key !== prevKey) animate(body, "stepped");
    $("live").textContent = v.aria || "";
  }
  view = v;
  footer();
  strip(t);
  dynamic();
  if (!visible) {
    panel.dataset.leave = "";
    readyAt = t + (reduced ? 0 : 210);
    panel.classList.add("on");
    setVisible(true);
  }
  ack();
}
function animate(el, cls) {
  el.classList.remove("scene-in", "stepped");
  void el.offsetWidth; // restart the CSS animation
  el.classList.add(cls);
}

// model chips (snapshot `chips`) and live-call chips (snapshot `calls`): only what the runtime listed.
// The elapsed time is the one text that changes every frame, so it is written into its span, not rebuilt.
let stripSig = "";
function strip(t) {
  const el = $("strip");
  const names = [...new Set((Array.isArray(snap?.chips) ? snap.chips : []).filter((c) => typeof c === "string" && c))];
  const calls = snap ? callChips(mem, t) : [];
  const s = names.join("|") + "#" + calls.map((c) => `${c.key}:${c.landed}`).join("|");
  if (s !== stripSig) {
    stripSig = s;
    el.innerHTML =
      names.map((n) => `<span class="chip ${chipKind(n)}">${esc(n)}</span>`).join("") +
      calls.map((c) => `<span class="chip call ${c.landed ? "landed" : "live"} ${chipKind(c.model)}"${c.tip ? ` title="${esc(c.tip)}"` : ""}><i></i>${esc(c.label)} · <b class="num" data-ck="${esc(c.key)}"></b></span>`).join("");
    el.dataset.n = String(names.length + calls.length);
  }
  for (const c of calls) {
    const b = el.querySelector(`[data-ck="${CSS.escape(c.key)}"]`);
    if (b && b.textContent !== c.val) b.textContent = c.val;
  }
}

let footSig = "";
function footer() {
  const emg = snap?.emg;
  const left = !snap ? "" : !emg ? "no EMG" : emg.source === "synthetic" ? "SYNTHETIC EMG" : "";
  const chips = [];
  if (replayNow() && snap?.label) chips.push(snap.label);
  else if (replayNow()) chips.push("REPLAY");
  const sel = snap?.selection;
  if (sel?.enabled && sel.degraded) chips.push("offline · scanning");
  else if (sel?.enabled && sel.mode === "scanning") chips.push("scanning");
  const s = left + "|" + chips.join();
  if (s === footSig) return;
  footSig = s;
  $("mt-l").textContent = left;
  $("mt-l").dataset.on = left ? "1" : "";
  $("mt-r").innerHTML = chips.map((c) => `<span class="${/^(SIM|REPL|RECORD|UNKNOWN)/.test(c) ? "sim" : "off"}">${esc(c)}</span>`).join("");
}

// text that follows the snapshot but must not rebuild the DOM
let expLast = "";
function dynamic() {
  const st = $("stale");
  if (st) {
    const age = sceneInfo(snap)?.age;
    const txt = age != null ? `last seen ${Math.round(age)} s ago` : "";
    if (st.textContent !== txt) st.textContent = txt;
  }
  const el = $("exp");
  if (!el || !snap) return;
  const v = scene === "pending" ? snap.pending?.expires_in_s : snap.learning?.proposal?.expires_in_s;
  const t = Number.isFinite(v) ? `${Math.ceil(v)} s` : "";
  if (t !== expLast || el.textContent !== t) {
    expLast = t;
    el.textContent = t ? `expires ${t}` : "";
  }
}

// ── per-frame: meter, reaction ring, progress ───────────────────────────────
const ringKey = () => {
  const sel = snap?.selection;
  return sel ? `${sel.menu}:${sel.highlight}:${sel.confirming}` : "";
};
const C = 2 * Math.PI * 15;
function frame(t, dt) {
  if (visible) {
    meter.draw(dt, t / 1000);
    const el = $("ring");
    const sel = snap?.selection;
    if (el && sel && (scene === "cards" || scene === "confirm")) {
      const key = ringKey();
      let r = mem.ring;
      if (!r || r.key !== key) r = mem.ring = { key, total: null, a: null, at: 0, seen: 0 };
      const a = sel.accept_in_s;
      if (r.seen !== snapAt) {
        r.seen = snapAt;
        if (a == null) r.a = null;
        else {
          const predicted = r.a == null ? Infinity : r.a - (snapAt - r.at) / 1000;
          if (r.total == null || a > predicted + 0.2) r.total = Math.max(a, 0.05); // (re)started
          r.a = a;
          r.at = snapAt;
        }
      }
      let state = "wait";
      let p = 0;
      let txt = "…";
      if (r.a != null) {
        const rem = Math.max(0, r.a - (t - r.at) / 1000);
        p = Math.min(1, Math.max(0, 1 - rem / r.total));
        state = rem > 0 ? "run" : "ready";
        txt = rem > 0 ? rem.toFixed(1) : "";
      }
      if (el.dataset.state !== state) el.dataset.state = state;
      $("ringf").style.strokeDashoffset = String(C * (1 - p));
      const v = $("ringv");
      if (v.textContent !== txt) v.textContent = txt;
      if (state === "ready" && !v.firstChild) v.innerHTML = ICON.tick;
      else if (state !== "ready" && v.firstElementChild) v.textContent = txt;
    }
    const vt = $("veto");
    if (vt) {
      const au = autoInfo(snap);
      if (au && au.remaining != null && au.total != null) {
        if (vt._at !== snapAt) {
          vt._at = snapAt;
          vt._rem = au.remaining;
        }
        const rem = Math.min(vt._rem, Math.max(0, vt._rem - Math.max(0, t - vt._at) / 1000));
        const w = vt.clientWidth;
        const h = vt.clientHeight;
        const r = $("vrect");
        if (w && h && (vt._w !== w || vt._h !== h)) {
          vt._w = w;
          vt._h = h;
          const svg = vt.querySelector(".vsvg");
          svg.setAttribute("width", w);
          svg.setAttribute("height", h);
          const k = far ? 4 : 2;
          r.setAttribute("x", k / 2);
          r.setAttribute("y", k / 2);
          r.setAttribute("width", w - k);
          r.setAttribute("height", h - k);
          r.setAttribute("rx", Math.min(18, h / 2 - k));
          r.style.strokeWidth = k;
        }
        r.style.strokeDashoffset = String(100 * (1 - Math.min(1, rem / au.total)));
        const n = $("vnum");
        const txt = `${rem.toFixed(1)}s`;
        if (n.textContent !== txt) n.textContent = txt;
      }
    }
    const bar = $("bar");
    if (bar && snap?.arm) {
      const p = snap.arm.progress;
      bar.style.transform = `scaleX(${Number.isFinite(p) ? Math.min(1, Math.max(0, p)) : 0})`;
    }
    ack();
  }
}
{
  let last = performance.now();
  const loop = (now) => {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (!document.hidden) frame(now, dt);
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

// ── acknowledge after paint (docs/SELECTION_API.md "Render acknowledgement") ─
let acking = { key: "", tries: 0, last: 0, wasAcked: false, busy: false };
function ack() {
  const sel = snap?.selection;
  if (!sel || replayNow() || document.hidden || !visible || (scene !== "cards" && scene !== "confirm")) return;
  if (!sel.enabled || !sel.require_ack || !sel.highlighted) return;
  const key = `${sel.menu}:${sel.highlight}:${sel.highlighted}`;
  const t = nowMs();
  if (acking.key !== key) acking = { key, tries: 0, last: 0, wasAcked: false, busy: false };
  if (sel.acked) {
    acking.wasAcked = true;
    return;
  }
  if (acking.wasAcked) {
    // acked turned false for the same highlight: the server wants it again
    acking.wasAcked = false;
    acking.tries = 0;
  }
  if (acking.busy || acking.tries >= 4 || t < readyAt) return;
  if (acking.tries > 0 && t - acking.last < 600) return;
  acking.busy = true;
  acking.tries += 1;
  acking.last = t;
  // The DOM for this card was written before this call. One frame to paint it, one more to know it
  // was painted: the ack goes out on the frame after the card was drawn.
  requestAnimationFrame(() =>
    requestAnimationFrame(async () => {
      const cur = snap?.selection;
      const same = cur && `${cur.menu}:${cur.highlight}:${cur.highlighted}` === key;
      if (!same || document.hidden || !visible) {
        acking.busy = false;
        return;
      }
      await post("/api/selection/ack", { card_id: cur.highlighted, menu: cur.menu, highlight: cur.highlight });
      acking.busy = false;
    }),
  );
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) ack();
});

// ── events → sounds, meter flashes ──────────────────────────────────────────
function onEvents(events) {
  const t = nowMs() / 1000;
  for (const e of events) {
    switch (e.kind) {
      case "emg.twitch": meter.twitch(1, t); break;
      case "emg.double_twitch": meter.twitch(2, t); break;
      case "emg.brace":
        meter.brace(t);
        if (scene === "auto") lastDismissAt = nowMs(); // a brace during the veto ring: the quiet fade, never red
        break;
      case "selection.shown": if (!e.upgraded) play("appear"); break;
      case "selection.select":
      case "selection.accept": if (e.ok) play("accept"); break;
      case "selection.dismiss":
        if (e.ok) {
          lastDismissAt = nowMs();
          play("refuse");
          // the braced card stays as a ghost for one fade while the next one rises into the highlight
          const h = !reduced && body.querySelector(".hero:not(.ghost)");
          if (h) {
            const g = h.cloneNode(true);
            g.querySelectorAll("[id]").forEach((n) => n.removeAttribute("id"));
            g.classList.add("ghost");
            g.setAttribute("aria-hidden", "true");
            ghost = { el: g, at: nowMs() };
          }
        }
        break;
      default: break;
    }
  }
}

// ── input: clicks go to the same endpoints a twitch uses ────────────────────
const noteEl = $("note");
let noteTimer = 0;
function note(text, bad = true) {
  if (!text) return;
  noteEl.textContent = text;
  noteEl.dataset.bad = bad ? "1" : "";
  noteEl.classList.add("on");
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => noteEl.classList.remove("on"), 3600);
}
async function act(name) {
  if (name === "stop") {
    panel.classList.add("stopping");
    setTimeout(() => panel.classList.remove("stopping"), 400);
    // Tokenless on the server: stopping is always safe. Never gated on anything the page knows.
    const r = await post("/api/stop", {});
    if (!r.ok) note(r.detail || "stop did not reach the runtime");
    return;
  }
  if (name === "mute") {
    muted = !muted;
    try {
      localStorage.setItem("armed.card.muted", muted ? "1" : "0");
    } catch {
      /* ignore */
    }
    paintMute();
    return;
  }
  if (replayNow() || !view) return; // replay has no actuation route at all
  let r = null;
  const pin = view.pin;
  switch (name) {
    case "accept": r = pin && (await post("/api/selection/accept", pin)); break;
    case "dismiss": r = pin && (await post("/api/selection/dismiss", pin)); break;
    case "step": r = pin && (await post("/api/selection/step", { menu: pin.menu })); break;
    case "confirm": r = await post("/api/confirm", {}); break;
    case "cancel": r = await post("/api/cancel", {}); break;
    case "approve": r = view.ruleId && (await post(`/api/rules/proposal/${encodeURIComponent(view.ruleId)}/approve`, {})); break;
    case "reject": r = view.ruleId && (await post(`/api/rules/proposal/${encodeURIComponent(view.ruleId)}/reject`, {})); break;
    default: break;
  }
  if (r && !r.ok) {
    toast(mem, r.detail, nowMs());
    note(r.detail || "refused");
  }
}
root.addEventListener("click", (e) => {
  const b = e.target.closest("[data-act]");
  if (b && !b.disabled) act(b.dataset.act);
});
// Never take focus: a click on the card must not move the keyboard away from the person's document.
root.addEventListener("mousedown", (e) => e.preventDefault());
// Click-through: tell the shell to take the mouse only while the pointer is over STOP or a button.
let hovering = false;
addEventListener(
  "pointermove",
  (e) => {
    if (hovering) return;
    hovering = true;
    requestAnimationFrame(() => {
      hovering = false;
    });
    setInteractive(!!document.elementFromPoint(e.clientX, e.clientY)?.closest("[data-hit]"));
  },
  { passive: true },
);
document.addEventListener("pointerleave", () => setInteractive(false));
if (!hosted) setInteractive(true);

// ── go ──────────────────────────────────────────────────────────────────────
ensureToken(); // read the session token now so the first ack does not wait for it
connect(
  (s) => {
    snap = s;
    snapAt = nowMs();
    const fresh = ingest(mem, s, snapAt);
    onEvents(fresh);
    meter.set(s.emg);
    refresh();
  },
  (up) => {
    connected = up;
    everUp ||= up;
    refresh();
  },
);
refresh();
setInterval(() => {
  if (snap) refresh(); // time-based scenes (stop dwell, disarmed notice) move on without a new event
}, 400);
