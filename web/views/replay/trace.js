// EMG trace chart (SVG): grip / open / brace lines over time, the brace threshold, onset + freeze
// annotations with the real latency, optional stage bands and frame ticks, a playhead you can scrub.
// It draws only what it is given. No series means an honest empty message, never a made-up line.
import { esc } from "./util.js";

const f2 = (v) => (Number.isFinite(v) ? v.toFixed(2) : "—");
const LINE = { grip: "grip", open: "open", brace: "brace" };

/**
 * Chart model:
 *   lines  [{ cls: grip|open|brace|other, name, t: [], v: [] }]   t in seconds (any origin; x-domain below)
 *   domain [t0, t1]
 *   threshold  number | null        (brace_effort)
 *   onset  { t, label } | null      brace onset marker
 *   freeze { t, label } | null      arm-frozen marker
 *   latencyMs number | null         drawn as a bracket from onset to freeze
 *   bands  [{ t0, t1, label }]      stage bands lane
 *   ticks  [{ t, label }]           frame ticks under the axis
 *   marks  [{ t, label }]           extra thin marks
 *   empty  string                   message when there are no lines
 *   axis   (t) => string            tick formatter; ticks every `step` seconds
 */
export class Trace {
  constructor(host, { onScrub = null, label = "EMG trace" } = {}) {
    this.host = host;
    this.onScrub = onScrub;
    this.m = null;
    this.cursor = null;
    this.drawn = false;
    host.innerHTML = `<svg class="trc-svg" role="img" aria-label="${esc(label)}"></svg>`;
    this.svg = host.firstElementChild;
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(host);
    this.scrubbing = false;
    if (onScrub) {
      host.classList.add("scrub");
      host.addEventListener("pointerdown", (e) => {
        if (!this.m?.lines?.length) return;
        this.scrubbing = true;
        host.setPointerCapture(e.pointerId);
        this.fromEvent(e);
      });
      host.addEventListener("pointermove", (e) => this.scrubbing && this.fromEvent(e));
      host.addEventListener("pointerup", () => (this.scrubbing = false));
      host.addEventListener("pointercancel", () => (this.scrubbing = false));
    }
  }
  dispose() {
    this.ro.disconnect();
  }
  fromEvent(e) {
    const b = this.svg.getBoundingClientRect();
    this.onScrub?.(this.tOf(e.clientX - b.left));
  }
  tOf(px) {
    const [t0, t1] = this.m.domain;
    const f = Math.min(1, Math.max(0, (px - this.pad.l) / Math.max(1, this.w - this.pad.l - this.pad.r)));
    return t0 + f * (t1 - t0);
  }
  xOf(t) {
    const [t0, t1] = this.m.domain;
    return this.pad.l + ((t - t0) / (t1 - t0 || 1)) * (this.w - this.pad.l - this.pad.r);
  }
  set(model, { animate = false } = {}) {
    this.m = model;
    this.animateIn = animate;
    this.draw();
  }
  setCursor(t) {
    this.cursor = t;
    if (this.ph && this.m?.lines?.length && Number.isFinite(t)) {
      const x = Math.min(this.w - this.pad.r, Math.max(this.pad.l, this.xOf(t)));
      this.ph.setAttribute("transform", `translate(${x} 0)`);
      this.ph.style.display = "";
    } else if (this.ph) this.ph.style.display = "none";
  }

  draw() {
    const w = (this.w = this.host.clientWidth);
    const h = (this.h = this.host.clientHeight);
    const svg = this.svg;
    if (!w || !h) return;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    const m = this.m;
    const bandsOn = !!m?.bands?.length;
    const ticksOn = !!m?.ticks?.length;
    const compact = h < 150;
    this.pad = { l: 34, r: m?.threshold != null ? 74 : 54, t: bandsOn ? 54 : 28, b: ticksOn ? 34 : 20 };
    const { l, r, t, b } = this.pad;
    if (!m || !m.lines?.length) {
      let s = "";
      for (let i = 1; i < 4; i++) s += `<path d="M${l} ${(h * i) / 4}H${w - 12}" class="trc-grid h"/>`;
      s += `<text x="${w / 2}" y="${h / 2}" text-anchor="middle" class="trc-empty">${esc(m?.empty || "No EMG trace.")}</text>`;
      svg.innerHTML = s;
      this.ph = null;
      return;
    }
    const [t0, t1] = m.domain;
    const X = (v) => this.xOf(v);
    const laneY = t + 4;
    const laneH = h - laneY - b;
    const vmax = Math.max(1, ...m.lines.flatMap((ln) => ln.v.filter(Number.isFinite)));
    const Y = (v) => laneY + laneH - (Math.min(v, vmax) / vmax) * laneH;
    let s = "";

    // after-freeze shade
    if (m.freeze && Number.isFinite(m.freeze.t)) {
      const xf = X(m.freeze.t);
      s += `<rect x="${xf}" y="${laneY - 6}" width="${Math.max(0, w - r - xf)}" height="${laneH + 6}" class="trc-after"/>`;
      s += `<text x="${w - r - 5}" y="${laneY + 8}" text-anchor="end" class="trc-cap">arm held</text>`;
    }

    // time grid + ticks
    const span = t1 - t0;
    const step = m.step || (span > 16 ? 4 : span > 8 ? 2 : span > 3 ? 1 : 0.5);
    for (let tt = Math.ceil(t0 / step) * step; tt <= t1 + 1e-6; tt += step) {
      const x = X(tt);
      const zero = m.zero != null && Math.abs(tt - m.zero) < 1e-6;
      s += `<path d="M${x} ${laneY}V${laneY + laneH}" class="trc-grid ${zero ? "zero" : ""}"/>`;
      const edge = x > w - r - 6;
      s += `<text x="${edge ? x + 2 : x}" y="${h - (ticksOn ? 20 : 5)}" text-anchor="${edge ? "end" : "middle"}" class="trc-tick ${zero ? "zero" : ""}">${esc(m.axis ? m.axis(tt) : tt.toFixed(1))}</text>`;
    }
    [0, 0.5, 1].forEach((v) => {
      if (v > vmax) return;
      const y = Y(v);
      s += `<path d="M${l} ${y}H${w - r}" class="trc-grid h"/><text x="${l - 7}" y="${y + 3}" text-anchor="end" class="trc-tick">${v}</text>`;
    });

    // stage bands
    if (bandsOn) {
      m.bands.forEach((bd) => {
        const x0 = Math.max(l, X(bd.t0));
        const x1 = Math.min(w - r, X(bd.t1));
        if (x1 - x0 < 2) return;
        s += `<rect x="${x0}" y="${t - 38}" width="${Math.max(2, x1 - x0 - 1)}" height="12" rx="3" class="trc-band"/>`;
        if (x1 - x0 > 58) s += `<text x="${x0 + 6}" y="${t - 29}" class="trc-band-t">${esc(bd.label)}</text>`;
      });
    }

    // threshold
    if (m.threshold != null && m.threshold <= vmax) {
      const ty = Y(m.threshold);
      s += `<path d="M${l} ${ty}H${w - r}" class="trc-thr"/><text x="${w - r + 6}" y="${ty + 3}" class="trc-thr-t">threshold ${f2(m.threshold)}</text>`;
    }

    // lines
    const lines = [...m.lines].sort((a, b2) => (a.cls === "brace") - (b2.cls === "brace"));
    for (const ln of lines) {
      let d = "";
      let pen = false;
      for (let i = 0; i < ln.t.length; i++) {
        if (ln.t[i] < t0 - span * 0.01 || ln.t[i] > t1 + span * 0.01 || !Number.isFinite(ln.v[i])) {
          pen = false;
          continue;
        }
        d += `${pen ? "L" : "M"}${X(ln.t[i]).toFixed(1)} ${Y(ln.v[i]).toFixed(1)}`;
        pen = true;
      }
      if (d) s += `<path d="${d}" class="trc-line ${ln.cls}" pathLength="1"/>`;
    }
    // line names at the right end
    const ends = lines
      .map((ln) => {
        let i = ln.t.length - 1;
        while (i > 0 && ln.t[i] > t1) i--;
        return Number.isFinite(ln.v[i]) ? { y: Y(ln.v[i]), cls: ln.cls, name: ln.name } : null;
      })
      .filter(Boolean)
      .sort((a, b2) => a.y - b2.y);
    for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 11) ends[i].y = ends[i - 1].y + 11;
    ends.forEach((e) => (s += `<text x="${w - r + 6}" y="${e.y + 3}" class="trc-end ${e.cls}">${esc(e.name)}</text>`));

    // extra marks
    (m.marks || []).forEach((mk) => {
      if (mk.t < t0 || mk.t > t1) return;
      s += `<path d="M${X(mk.t)} ${laneY}V${laneY + laneH}" class="trc-mark soft"/>`;
    });

    // onset + freeze with the real latency
    const noteY = bandsOn ? t - 9 : compact ? t - 6 : t - 5;
    if (m.onset && Number.isFinite(m.onset.t) && m.onset.t >= t0 && m.onset.t <= t1) {
      const xo = X(m.onset.t);
      s += `<path d="M${xo} ${laneY - 8}V${laneY + laneH}" class="trc-mark onset"/>`;
    }
    if (m.freeze && Number.isFinite(m.freeze.t) && m.freeze.t >= t0 && m.freeze.t <= t1) {
      const xs = X(m.freeze.t);
      s += `<path d="M${xs} ${laneY - 8}V${laneY + laneH}" class="trc-mark stop"/>`;
      const hasOn = m.onset && Number.isFinite(m.onset.t);
      const xo = hasOn ? X(m.onset.t) : xs;
      if (hasOn && xs - xo > 3) s += `<path d="M${xo} ${laneY - 4}H${xs}" class="trc-brk"/>`;
      const lat = m.latencyMs != null ? ` · <tspan class="trc-num">${Math.round(m.latencyMs)} ms</tspan> after onset` : "";
      s += `<text x="${xs - 6}" y="${noteY}" text-anchor="end" class="trc-note stop">${esc(m.freeze.label || "arm frozen")}${lat}</text>`;
      if (hasOn && xo < xs - 120) s += `<text x="${xo - 6}" y="${noteY}" text-anchor="end" class="trc-note">${esc(m.onset.label || "brace onset")}</text>`;
    } else if (m.onset && Number.isFinite(m.onset.t) && m.onset.t >= t0 && m.onset.t <= t1) {
      s += `<text x="${X(m.onset.t) - 6}" y="${noteY}" text-anchor="end" class="trc-note">${esc(m.onset.label || "brace onset")}</text>`;
    }

    // frame ticks
    if (ticksOn) {
      m.ticks.forEach((tk) => {
        const x = X(tk.t);
        s += `<path d="M${x} ${h - b + 2}v6" class="trc-ftick"/>`;
      });
      s += `<text x="${l - 7}" y="${h - 5}" text-anchor="end" class="trc-tick">frames</text>`;
    }

    // playhead
    s += `<g class="trc-ph" transform="translate(${Number.isFinite(this.cursor) ? X(this.cursor) : -9} 0)"><path d="M0 ${laneY - 6}V${laneY + laneH}" class="trc-ph-line"/><path d="M-6 ${laneY - 12}h12l-6 8z" class="trc-ph-cap"/></g>`;
    svg.innerHTML = s;
    this.ph = svg.querySelector(".trc-ph");
    if (this.cursor == null && this.ph) this.ph.style.display = "none";
    if (this.animateIn && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      this.animateIn = false;
      svg.querySelectorAll(".trc-line").forEach((p, i) =>
        p.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration: 900, delay: 100 + i * 80, easing: "cubic-bezier(.16,1,.3,1)", fill: "backwards" }),
      );
    }
  }
}

/** Build a chart model from the /emg payload of an incident. */
export function modelFromEmg(emg, { bands = [], ticks = [], marks = [], axis } = {}) {
  if (!emg) return { lines: [], empty: "Loading the EMG trace…" };
  const t = emg.t || [];
  const ch = emg.channels || {};
  const names = Object.keys(ch);
  if (!t.length || !names.length) {
    return { lines: [], empty: emg.error ? `No EMG trace: ${emg.error}` : "This incident has no EMG samples." };
  }
  const known = { flexor: ["grip", "grip"], extensor: ["open", "open"], brace: ["brace", "brace"] };
  const lines = names.map((n) => ({ cls: known[n]?.[0] || "other", name: known[n]?.[1] || n, t, v: ch[n] }));
  const onset = emg.onset && Number.isFinite(emg.onset.t) ? { t: emg.onset.t, label: emg.onset.source === "derived" ? "brace onset (derived)" : "brace onset" } : null;
  const t0 = Math.min(t[0], ...bands.map((b) => b.t0), ...ticks.map((k) => k.t));
  const t1 = Math.max(t[t.length - 1], ...bands.map((b) => b.t1), ...ticks.map((k) => k.t));
  return {
    lines,
    domain: [t0, t1],
    threshold: Number.isFinite(emg.brace_effort) ? emg.brace_effort : null,
    onset,
    freeze: { t: 0, label: "arm frozen" },
    latencyMs: onset ? -onset.t * 1000 : null,
    bands,
    ticks,
    marks,
    zero: 0,
    axis: axis || ((v) => (Math.abs(v) < 1e-6 ? "freeze" : `${v < 0 ? "−" : "+"}${Math.abs(v)}s`)),
    note: emg.note,
  };
}
