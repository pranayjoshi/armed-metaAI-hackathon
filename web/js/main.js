// App shell: sections open one at a time (no long scrolling page), joined by a portal transition.
import * as core from "./core.js";
const { $, $$, esc, bus, store, api, loop, reduced, mode } = core;

const I = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
const ALL_SECTIONS = [
  { id: "home", label: "Home", key: "1", icon: I('<circle cx="12" cy="12" r="3.2"/><circle cx="12" cy="12" r="8.5" stroke-dasharray="2 3.4"/>') },
  { id: "mind", label: "Mind", key: "2", icon: I('<circle cx="6" cy="7" r="2"/><circle cx="6" cy="17" r="2"/><circle cx="18" cy="12" r="2.6"/><path d="M8 7.6l7.6 3.4M8 16.4l7.6-3.4"/>') },
  { id: "stage", label: "Stage", key: "2", icon: I('<path d="M4 20h16M7 20l3-8 5 1 3-6"/><circle cx="18" cy="7" r="1.8"/><circle cx="10" cy="12" r="1.4"/>') },
  { id: "flow", label: "Workflow", key: "3", icon: I('<rect x="3" y="3" width="6" height="6" rx="1.5"/><rect x="15" y="9" width="6" height="6" rx="1.5"/><rect x="3" y="15" width="6" height="6" rx="1.5"/><path d="M9 6h3a3 3 0 0 1 3 3M9 18h3a3 3 0 0 0 3-3"/>') },
  { id: "signals", label: "Signals", key: "4", icon: I('<path d="M2 12h4l2.5-7 4 14 3-9 1.5 2H22"/>') },
  { id: "learn", label: "Learning", key: "5", icon: I('<path d="M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3"/><path d="M18 3v4h-4M6 21v-4h4"/>') },
  { id: "lab", label: "Lab", modes: ["live"], icon: I('<path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2.2h12.4a1.5 1.5 0 0 0 1.3-2.2L14 9V3"/><path d="M7.5 15h9"/>') },
  { id: "replay", label: "Replay", modes: ["replay"], icon: I('<circle cx="12" cy="12" r="8.5"/><path d="M10 8.5l5.5 3.5-5.5 3.5z"/>') },
  { id: "context", label: "Context", icon: I('<rect x="3" y="4" width="11" height="16" rx="1.5"/><path d="M17 8h4M17 12h4M17 16h4"/><circle cx="8.5" cy="10" r="2"/>') },
];
// Sections valid in this mode, numbered 1..n. Replay mode has no Lab (calibration, sessions, EMG drive all actuate).
export const SECTIONS = ALL_SECTIONS.filter((s) => !s.modes || s.modes.includes(mode)).map((s, i) => ({ ...s, key: String(i + 1) }));
const HOME = mode === "replay" ? "replay" : "home";

const root = $("#views");
const dock = $("#dock");
const ind = $("#dock-ind");
const views = new Map(); // id -> { el, mod, ready, ctx }
let current = null;
let busy = false;
let queued = null; // a navigation requested while a transition runs

// ctx handed to every view
export const ctx = {
  ...core,
  store,
  router: { go: (id, origin) => go(id, origin), sections: SECTIONS },
};

// ── dock ───────────────────────────────────────────────────────────────────
dock.insertAdjacentHTML(
  "beforeend",
  SECTIONS.map((s) => `<a href="#/${s.id}" data-id="${s.id}">${s.icon}<span class="tip">${s.label}<kbd>${s.key}</kbd></span></a>`).join(""),
);
dock.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-id]");
  if (!a) return;
  e.preventDefault();
  const r = a.getBoundingClientRect();
  go(a.dataset.id, [r.left + r.width / 2, r.top + r.height / 2]);
});
function moveIndicator(id) {
  const a = $(`a[data-id="${id}"]`, dock);
  if (!a) return;
  ind.style.transform = `translate(${a.offsetLeft - ind.offsetLeft}px, ${a.offsetTop - ind.offsetTop}px)`;
  $$("a", dock).forEach((x) => (x.getAttribute("data-id") === id ? x.setAttribute("aria-current", "page") : x.removeAttribute("aria-current")));
}
addEventListener("resize", () => current && moveIndicator(current));

// ── views ──────────────────────────────────────────────────────────────────
async function ensure(id) {
  if (views.has(id)) return views.get(id);
  const el = document.createElement("section");
  el.className = "view";
  el.dataset.view = id;
  el.hidden = true;
  root.append(el);
  const v = { el, mod: null, ctx, mounted: false };
  views.set(id, v);
  try {
    v.mod = (await import(`/web/views/${id}/index.js`)).default;
    await v.mod.mount(el, ctx);
    v.mounted = true;
    if (store.state) v.mod.update?.(store.state, ctx);
  } catch (err) {
    console.error(`view ${id}`, err);
    el.innerHTML = `<div class="view-error"><h1>${esc(SECTIONS.find((s) => s.id === id)?.label || id)}</h1><p>This section failed to load.</p><code>${esc(err.message)}</code></div>`;
  }
  return v;
}

async function go(id, origin, { instant = false } = {}) {
  if (!SECTIONS.some((s) => s.id === id)) id = HOME;
  if (busy) {
    queued = [id, origin];
    return;
  }
  if (id === current) return;
  busy = true;
  const next = await ensure(id);
  const prev = current ? views.get(current) : null;
  const sec = SECTIONS.find((s) => s.id === id);
  const idx = SECTIONS.indexOf(sec);
  moveIndicator(id);
  history.replaceState(null, "", `#/${id}`);
  $("#crumb-n").textContent = String(idx + 1).padStart(2, "0");
  const crumb = $("#crumb");
  crumb.textContent = sec.label;
  crumb.classList.remove("swap");
  void crumb.offsetWidth;
  crumb.classList.add("swap");

  prev?.mod?.leave?.(ctx);
  next.el.hidden = false;
  next.el.classList.add("is-active");
  next.mod?.enter?.(ctx);
  if (next.mod && store.state) next.mod.update?.(store.state, ctx);
  current = id;

  let finished = false;
  const done = () => {
    if (finished) return;
    finished = true;
    if (prev) {
      prev.el.hidden = true;
      prev.el.classList.remove("is-active");
      prev.el.style.cssText = "";
    }
    next.el.style.cssText = "";
    busy = false;
    if (queued) {
      const [qid, qorigin] = queued;
      queued = null;
      go(qid, qorigin);
    }
  };
  if (!prev || instant || reduced || !next.el.animate) {
    next.el.animate?.([{ opacity: 0 }, { opacity: 1 }], { duration: instant ? 1 : 200 });
    done();
    return;
  }
  // Portal: the next section opens as a circle growing from where you clicked; the old one recedes.
  const box = root.getBoundingClientRect();
  const [ox, oy] = origin ? [origin[0] - box.left, origin[1] - box.top] : [box.width / 2, box.height / 2];
  const far = Math.hypot(Math.max(ox, box.width - ox), Math.max(oy, box.height - oy));
  const ripple = document.createElement("i");
  ripple.className = "view-ripple";
  ripple.style.left = `${ox}px`;
  ripple.style.top = `${oy}px`;
  root.append(ripple);
  ripple.animate([{ transform: "scale(1)", opacity: 0.9 }, { transform: `scale(${(far * 2) / 14})`, opacity: 0 }], { duration: 900, easing: "cubic-bezier(.16,1,.3,1)" }).onfinish = () => ripple.remove();
  prev.el.animate(
    [{ transform: "scale(1)", opacity: 1, filter: "blur(0)" }, { transform: "scale(.94)", opacity: 0, filter: "blur(10px)" }],
    { duration: 650, easing: "cubic-bezier(.7,0,.2,1)", fill: "forwards" },
  );
  next.el.animate(
    [
      { clipPath: `circle(0px at ${ox}px ${oy}px)`, transform: "scale(1.06)" },
      { clipPath: `circle(${far}px at ${ox}px ${oy}px)`, transform: "scale(1)" },
    ],
    { duration: 720, easing: "cubic-bezier(.7,0,.2,1)" },
  ).onfinish = done;
  setTimeout(done, 1000); // WAAPI never finishes in a background tab; never leave the router stuck
}

function fromHash() {
  const id = location.hash.replace(/^#\/?/, "");
  return SECTIONS.some((s) => s.id === id) ? id : HOME;
}
addEventListener("hashchange", () => go(fromHash()));

// ── keyboard ───────────────────────────────────────────────────────────────
addEventListener("keydown", (e) => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || document.activeElement?.isContentEditable;
  if (e.code === "Space" && !typing) {
    e.preventDefault();
    stop();
    return;
  }
  if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
  const gesture = mockOn() && { t: "twitch", d: "double_twitch", q: "squeeze", b: "brace" }[e.key.toLowerCase()];
  if (gesture) return mockGesture(gesture);
  const hit = SECTIONS.find((s) => s.key === e.key);
  if (hit) go(hit.id);
  else if (e.key === "s" || e.key === "S") go("stage");
  else if (e.key === "Escape") go(HOME);
});

// ── stop + global shock ────────────────────────────────────────────────────
function stop() {
  api("/api/stop");
  shock();
}
$("#stop").onclick = stop;
function shock() {
  const el = $("#shock");
  el.classList.remove("fire");
  void el.offsetWidth;
  el.classList.add("fire");
  if (!reduced) {
    const app = $("#app");
    app.classList.remove("shake");
    void app.offsetWidth;
    app.classList.add("shake");
  }
}
bus.on("event", (e) => {
  if (e.kind === "emg.brace" || e.kind === "arm.stopped" || e.kind === "emg.open_hand" || e.kind === "arm.fault") shock();
  if (e.kind === "incident.triggered" || e.kind === "incident.explained") dockBadge("learn", current !== "learn");
});
function dockBadge(id, on) {
  const a = $(`a[data-id="${id}"]`, dock);
  if (!a) return;
  a.querySelector(".badge")?.remove();
  if (on) a.insertAdjacentHTML("beforeend", '<i class="badge"></i>');
}

// ── mock EMG dock: the synthetic source's stand-in for the band ────────────
// Each button emits the gesture the decoder would (POST /api/emg/mock/<gesture>), so the
// cards, the veto ring and the freeze run unchanged. Never shown with the real band.
const mockDock = $("#emgmock");
const mockOn = () => mode !== "replay" && !!store.state?.emg && !store.state.emg.hardware;
function mockGesture(g) {
  api(`/api/emg/mock/${g}`);
  const b = $(`button[data-g="${g}"]`, mockDock);
  b?.classList.remove("hit");
  void b?.offsetWidth;
  b?.classList.add("hit");
}
mockDock.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-g]");
  if (b) mockGesture(b.dataset.g);
});

// ── EMG source toggle: real band or synthetic, switched live (POST /api/emg/source) ──
// The runtime disarms on a switch and holds motion until the new source streams.
const srcToggle = $("#emgsrc");
srcToggle.addEventListener("click", (e) => {
  const b = e.target.closest("button[data-src]");
  if (!b || b.classList.contains("on")) return;
  if (!confirm(`Switch EMG to ${b.textContent}? Armed disarms first.`)) return;
  srcToggle.classList.add("busy");
  api("/api/emg/source", { source: b.dataset.src }).finally(() => srcToggle.classList.remove("busy"));
});
function renderSrc(s) {
  srcToggle.hidden = mode === "replay" || !s.emg;
  const cur = s.emg?.source === "synthetic" ? "synthetic" : "ganglion";
  for (const b of srcToggle.querySelectorAll("button")) b.classList.toggle("on", b.dataset.src === cur);
}

// ── status chips ───────────────────────────────────────────────────────────
const chip = (label, tone = "") => `<span class="chip ${tone}"><i></i>${esc(label)}</span>`;
function renderChips(s) {
  if (!s.arm) {
    // Replay without a running simulation: only the recordings exist.
    const html = chip(s.label || "REPLAY", "warn") + chip(`${s.replay?.incidents ?? 0} incident${s.replay?.incidents === 1 ? "" : "s"} · ${s.replay?.sessions ?? 0} session${s.replay?.sessions === 1 ? "" : "s"}`);
    if (renderChips.last !== html) $("#chips").innerHTML = renderChips.last = html;
    return;
  }
  const arm = s.arm;
  const armTone = { ready: "ok", moving: "ok", disarmed: "warn", stopped: "bad", fault: "bad" }[arm.state] || "";
  const out = [];
  if (mode === "replay") out.push(chip(s.label || "REPLAY", "warn"));
  out.push(chip(`${arm.hardware ? "SO-101" : "Virtual arm"} · ${arm.state}`, armTone));
  if (s.emg) {
    const tone = s.emg.error ? "bad" : !s.emg.live ? "warn" : s.emg.hardware ? "ok" : "warn";
    out.push(chip(s.emg.hardware ? `EMG${s.emg.calibrated ? "" : " · uncalibrated"}` : "EMG mock", tone));
  }
  out.push(chip(s.vision.virtual ? "Virtual table" : `Camera ${s.vision.fps || 0} fps`, s.vision.virtual ? "warn" : s.vision.live ? "ok" : "bad"));
  if (s.speech) out.push(chip(s.speech.error ? "Mic error" : `${s.speech.asr || "ASR loading"}`, s.speech.error ? "bad" : s.speech.listening ? "ok" : "warn"));
  out.push(chip(s.llm.enabled ? `Muse ${s.llm.last_ms ? s.llm.last_ms + " ms" : "ready"}` : "Grammar only", s.llm.error ? "bad" : s.llm.enabled ? "muse" : ""));
  if (s.session.session_id) out.push(chip(`REC ${s.session.trials_done} trials`, "bad"));
  const html = out.join("");
  if (renderChips.last !== html) $("#chips").innerHTML = renderChips.last = html;
}
bus.on("conn", (ok) => {
  if (!ok) {
    $("#chips").innerHTML = renderChips.last = chip("Runtime offline — reconnecting", "bad");
  }
});

// ── state fan-out: only the active section re-renders ──────────────────────
bus.on("state", (s) => {
  renderChips(s);
  mockDock.hidden = !mockOn();
  renderSrc(s);
  const v = current && views.get(current);
  if (v?.mounted) v.mod.update?.(s, ctx);
});

// brace/pulse on the logo spike so the chrome itself reads the body
loop.add(() => {
  const e = store.state?.emg;
  document.documentElement.style.setProperty("--brace-level", e ? String(e.brace || 0) : "0");
});

core.connect();
go(fromHash(), null, { instant: true });
