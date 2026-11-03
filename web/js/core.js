// Shared runtime for the H2M operator UI: state stream, API, event bus, frame loop, helpers.
// Views import from here (or receive the same things on `ctx`). Nothing here draws UI.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const words = (v, fb = "—") => (v ? String(v).replaceAll("_", " ") : fb);
export const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);
export const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
export const lerp = (a, b, t) => a + (b - a) * t;
/** Frame-rate independent smoothing: damp(current, target, lambda, dt). lambda ~ 6-20. */
export const damp = (a, b, lambda, dt) => lerp(a, b, 1 - Math.exp(-lambda * dt));
export const ease = { outExpo: (t) => (t === 1 ? 1 : 1 - 2 ** (-10 * t)), inOut: (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2) };
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
/** Escape text before putting it in innerHTML. Names and utterances come from outside. */
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

export const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
export const webgl = (() => {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
})();

// ── tiny event bus ─────────────────────────────────────────────────────────
const handlers = new Map();
export const bus = {
  on(type, fn) {
    if (!handlers.has(type)) handlers.set(type, new Set());
    handlers.get(type).add(fn);
    return () => handlers.get(type).delete(fn);
  },
  emit(type, payload) {
    handlers.get(type)?.forEach((fn) => {
      try {
        fn(payload);
      } catch (err) {
        console.error(`[bus:${type}]`, err);
      }
    });
  },
};

// ── state store ────────────────────────────────────────────────────────────
// store.state    latest /api/live snapshot (null until the first frame)
// store.history  every event seen since page load (cap 400), oldest first
// store.objectNames  {id: name}
// bus 'state' (snapshot) fires ~10 Hz; bus 'event' (event) once per new runtime event; bus 'conn' (bool).
export const store = { state: null, history: [], objectNames: {}, connected: false };
let lastSeq = -1;

function ingest(s) {
  store.state = s;
  store.objectNames = Object.fromEntries((s.objects || []).map((o) => [o.id, o.name]));
  const fresh = (s.events || []).filter((e) => e.seq > lastSeq);
  const first = lastSeq < 0;
  for (const e of fresh) {
    lastSeq = Math.max(lastSeq, e.seq);
    store.history.push(e);
  }
  if (store.history.length > 400) store.history.splice(0, store.history.length - 400);
  bus.emit("state", s);
  // The first frame is history: views may read store.history but must not animate it as live.
  if (!first) fresh.forEach((e) => bus.emit("event", e));
}

export function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/live`);
  ws.onopen = () => {
    store.connected = true;
    bus.emit("conn", true);
  };
  ws.onmessage = (m) => ingest(JSON.parse(m.data));
  ws.onclose = () => {
    store.connected = false;
    bus.emit("conn", false);
    setTimeout(connect, 1000);
  };
}

// ── API ────────────────────────────────────────────────────────────────────
export function toast(text, tone = "") {
  const el = $("#toast");
  el.textContent = text;
  el.dataset.tone = tone;
  el.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => el.classList.remove("show"), 2800);
}

// Per-session token the server writes into this page (docs/LOCAL_API.md); every
// POST/DELETE must carry it or the server refuses with 403.
const TOKEN = document.querySelector('meta[name="h2m-token"]')?.content || "";
/** "live" on `h2m run`; "replay" on `h2m demo` / `h2m replay` (read-only, no actuation routes exist). */
export const mode = document.querySelector('meta[name="h2m-mode"]')?.content === "replay" ? "replay" : "live";
document.documentElement.dataset.mode = mode;

async function request(method, path, body) {
  const headers = { "X-H2M-Token": TOKEN };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) toast(data.detail || res.statusText, "bad");
  return data;
}
/** api(path, body?) POSTs. api.get(path) and api.del(path) for the others. Errors toast and still return the body. */
export const api = Object.assign((path, body) => request("POST", path, body), {
  get: (path) => request("GET", path),
  del: (path) => request("DELETE", path),
});

// ── one frame loop for everything ──────────────────────────────────────────
// loop.add(fn) -> remove(). fn(dt_seconds, t_seconds). Paused while the tab is hidden.
const tickers = new Set();
let lastT = performance.now();
function frame(now) {
  const dt = Math.min(0.05, (now - lastT) / 1000);
  lastT = now;
  if (!document.hidden) tickers.forEach((fn) => fn(dt, now / 1000));
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
export const loop = {
  add(fn) {
    tickers.add(fn);
    return () => tickers.delete(fn);
  },
};

// ── lazy assets ────────────────────────────────────────────────────────────
let threeP = null;
/** Resolves to the three.js module (vendored, no network). Rejects when WebGL is unavailable. */
export function loadThree() {
  if (!webgl) return Promise.reject(new Error("WebGL unavailable"));
  return (threeP ||= import("three"));
}
/** css(import.meta.url, "style.css") links a stylesheet that sits next to the calling module. */
export function css(base, file) {
  const href = new URL(file, base).pathname;
  if ($(`link[href="${href}"]`)) return;
  const l = document.createElement("link");
  l.rel = "stylesheet";
  l.href = href;
  document.head.append(l);
}

/** Count a number element up/down to `to`. Skips animation under reduced motion. */
export function countTo(el, to, { fmt = (v) => Math.round(v), ms = 600 } = {}) {
  if (reduced || !Number.isFinite(to)) {
    el.textContent = fmt(to);
    return;
  }
  const from = Number(el.dataset.v ?? 0);
  el.dataset.v = to;
  const t0 = performance.now();
  cancelAnimationFrame(el._raf);
  const step = (now) => {
    const k = clamp((now - t0) / ms);
    el.textContent = fmt(lerp(from, to, ease.outExpo(k)));
    if (k < 1) el._raf = requestAnimationFrame(step);
  };
  el._raf = requestAnimationFrame(step);
}

// ── shared vocabulary for runtime events ───────────────────────────────────
export const EVENT_KIND = {
  "speech.utterance": ["heard", "heard"], intent: ["decision", "decision"], "arm.started": ["arm", "arm"],
  "arm.completed": ["done", "done"], "arm.stopped": ["stopped", "stop"], "arm.fault": ["fault", "fault"],
  "emg.squeeze": ["squeeze", "emg"], "emg.open_hand": ["open hand", "emg"], "emg.brace": ["brace", "brace"],
  "intent.cancelled": ["cancelled", "decision"], "intent.expired": ["expired", "decision"], "intent.blocked": ["blocked", "fault"],
  "compiler.llm": ["muse", "muse"], "trial.started": ["trial", "trial"], "trial.ended": ["trial", "done"],
  "incident.triggered": ["incident", "brace"], "incident.saved": ["incident", "muse"], "incident.explained": ["wonder", "muse"],
  "incident.error": ["incident", "fault"], "rule.approved": ["rule", "done"], "rule.rejected": ["rule", "decision"],
  "rule.expired": ["rule", "decision"], "rule.error": ["rule", "fault"],
};

/** One-line human description of an event. Returns plain text (escape it if you use innerHTML). */
export function describeEvent(e) {
  const name = (id) => store.objectNames[id] || id || "";
  switch (e.kind) {
    case "speech.utterance": return `“${e.text}”${e.asr_ms ? ` · ASR ${e.asr_ms} ms` : ""}`;
    case "intent": return `${words(e.contract.verb)} ${e.contract.object_name || ""} → ${e.decision.verdict}: ${e.decision.reason}`;
    case "arm.started": return `${words(e.skill)} ${name(e.object_id)}${e.grip ? ` · grip ${pct(e.grip.effort)}` : ""} · by ${e.confirmed_by}`;
    case "arm.completed": return `${words(e.skill)} in ${e.duration_s}s · peak load ${pct(e.peak_load)}`;
    case "arm.stopped":
    case "arm.fault": return e.reason;
    case "emg.squeeze": return `effort ${pct(e.effort)} held ${e.held_ms} ms`;
    case "emg.open_hand": return `extensor ${pct(e.extensor)}`;
    case "emg.brace": return `brace ${pct(e.brace)}${e.latency_ms != null ? ` · ${e.latency_ms} ms` : ""}`;
    case "compiler.llm": return e.ok ? `interpreted in ${e.ms} ms` : `failed: ${e.error}`;
    case "trial.started": return `#${e.trial} expect ${words(e.expected_action)} ${name(e.expected_object)} (${e.condition})`;
    case "trial.ended": return `#${e.trial} ${words(e.outcome)}`;
    case "incident.triggered": return `${e.source || "brace"} froze the arm`;
    case "incident.saved": return `clip saved${e.held ? ` · holding ${e.held}` : ""}`;
    case "incident.explained": return e.say || e.source || "explained";
    case "rule.approved": return `rule approved${e.by ? ` by ${e.by}` : ""}`;
    case "rule.rejected": return `rule rejected${e.reason ? ` · ${e.reason}` : ""}`;
    case "rule.expired": return "rule proposal expired";
    default: return e.reason || e.detail || e.error || "";
  }
}

/** Seconds since an event, as "3.2s" / "4m". */
export function ago(state, e) {
  const s = Math.max(0, state.t - e.t);
  return s < 60 ? `${s.toFixed(1)}s` : `${Math.round(s / 60)}m`;
}
