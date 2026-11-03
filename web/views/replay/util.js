// Small shared helpers for the Replay section: fetching, labels, typewriter, wording.
import { esc } from "/web/js/core.js";

export { esc };

/** GET JSON without toasting: the Replay section shows its own honest errors. */
export async function getJSON(url) {
  try {
    const r = await fetch(url, { cache: "no-store" });
    const body = await r.json().catch(() => ({}));
    return r.ok ? { ok: true, body } : { ok: false, body, status: r.status, error: body?.detail || `HTTP ${r.status}` };
  } catch (err) {
    return { ok: false, body: {}, status: 0, error: String(err?.message || err) };
  }
}

export const words = (v, fb = "—") => (v ? String(v).replaceAll("_", " ") : fb);
export const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
export const f2 = (v) => (num(v) == null ? "—" : v.toFixed(2));
export const shortId = (id) => (id && id.length > 14 ? `…${id.slice(-8)}` : id || "—");
/** Signed seconds for a time relative to the freeze: "−1.4 s", "+0.9 s", "freeze". */
export function fmtT(t, { zero = true } = {}) {
  if (!Number.isFinite(t)) return "—";
  if (zero && Math.abs(t) < 0.0005) return "freeze";
  return `${t < 0 ? "−" : "+"}${Math.abs(t).toFixed(1)} s`;
}

const ICONS = {
  sim: '<path d="M12 3l9.5 17h-19z"/><path d="M12 10v4.6M12 17.4v.1"/>',
  rec: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3" fill="currentColor"/>',
  unk: '<circle cx="12" cy="12" r="8.5"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.6 2.2c-.8.5-1.2 1-1.2 1.9M12 17v.1"/>',
};

/** Provenance badge. A SIMULATION must never look like a hardware recording. */
export function provBadge(label, { big = false } = {}) {
  const l = String(label || "UNKNOWN SOURCE").toUpperCase();
  const kind = l === "SIMULATION" ? "sim" : l === "RECORDED" ? "rec" : "unk";
  return `<span class="prov ${kind}${big ? " big" : ""}" title="${esc(l)}"><svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[kind]}</svg>${esc(l)}</span>`;
}

/** Type `text` into `el` (full text reserved first so the layout never jumps). Returns a cancel fn. */
export function typeInto(el, text, { cps = 44, reduced = false, onDone } = {}) {
  el._type?.();
  const full = String(text ?? "");
  if (reduced || !full) {
    el.textContent = full;
    onDone?.();
    return () => {};
  }
  el.innerHTML = `<span class="say-full">${esc(full)}</span><span class="say-t"></span>`;
  const out = el.querySelector(".say-t");
  let i = 0;
  let raf = 0;
  let last = performance.now();
  let acc = 0;
  const step = (now) => {
    acc += ((now - last) / 1000) * cps;
    last = now;
    const n = Math.min(full.length, Math.floor(acc));
    if (n !== i) {
      i = n;
      out.textContent = full.slice(0, i);
    }
    if (i < full.length) raf = requestAnimationFrame(step);
    else onDone?.();
  };
  raf = requestAnimationFrame(step);
  const cancel = () => {
    cancelAnimationFrame(raf);
    el._type = null;
  };
  el._type = cancel;
  return cancel;
}

/** A rule as one sentence plus chips. `rule` = {when_holding, avoid, response}. */
export function ruleSentence(rule) {
  if (!rule) return "";
  const hold = (rule.when_holding || []).join(" or ");
  const resp = words(rule.response, "respond");
  return `${hold ? `When holding something ${hold}` : "Always"}: ${resp}${rule.avoid ? ` ${rule.avoid}` : ""}.`;
}
export function ruleChips(rule) {
  if (!rule) return "";
  const chip = (k, v, cls = "") => `<span class="kvchip ${cls}"><b>${esc(k)}</b>${esc(v)}</span>`;
  const out = [];
  (rule.when_holding || []).forEach((t) => out.push(chip("holding", t, "muse")));
  if (rule.avoid) out.push(chip("avoid", rule.avoid, "brace"));
  out.push(chip("response", words(rule.response)));
  return out.join("");
}

export const I = (d) => `<svg viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
export const ICON = {
  play: I('<path d="M7 4.5v15l12-7.5z"/>'),
  pause: I('<rect x="6" y="5" width="4.2" height="14" rx="1"/><rect x="13.8" y="5" width="4.2" height="14" rx="1"/>'),
  replay: I('<path d="M4 12a8 8 0 1 0 2.6-5.9M4 4v4.5h4.5"/>'),
  prev: I('<path d="M7 5v14M9 12l9-6.5v13z"/>'),
  next: I('<path d="M17 5v14M15 12L6 5.5v13z"/>'),
  sim: I('<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 16l.7 1.8 1.8.7-1.8.7L19 21l-.7-1.8-1.8-.7 1.8-.7z"/>'),
  inc: I('<path d="M3 12h4l2.4-7 3.8 14 3-9 1.6 2H21"/>'),
  ses: I('<rect x="4" y="3.5" width="16" height="17" rx="2.5"/><path d="M8 8.5h8M8 12h8M8 15.5h5"/>'),
  check: I('<path d="M5 12.5l4.5 4.5L19 7.5"/>'),
  cross: I('<path d="M6 6l12 12M18 6L6 18"/>'),
};
