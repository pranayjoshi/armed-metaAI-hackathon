// Playback clock + the incident timeline (SVG). The chart shows only what the incident really has:
// joint motion and stage bands over the whole clip, and the fast EMG window carried by the brace event.
import { fmtT, ago } from "./data.js";

export class Player {
  constructor(loop, onTick) {
    this.t = 0;
    this.range = [0, 1];
    this.speed = 1;
    this.playing = false;
    this.onTick = onTick;
    this.stopLoop = null;
    this.loop = loop;
  }
  setRange(r, t = r[0]) {
    this.range = r;
    this.t = t;
    this.onTick(this.t, true);
  }
  seek(t) {
    this.t = Math.min(this.range[1], Math.max(this.range[0], t));
    this.onTick(this.t, true);
  }
  play() {
    if (this.t >= this.range[1] - 0.001) this.t = this.range[0];
    this.playing = true;
    this.stopLoop ||= this.loop.add((dt) => {
      this.t = Math.min(this.range[1], this.t + dt * this.speed);
      if (this.t >= this.range[1]) this.pause();
      this.onTick(this.t, false);
    });
    this.onTick(this.t, true);
  }
  pause() {
    this.playing = false;
    this.stopLoop?.();
    this.stopLoop = null;
    this.onTick(this.t, true);
  }
  toggle() {
    this.playing ? this.pause() : this.play();
  }
  setSpeed(s) {
    this.speed = s;
    this.onTick(this.t, true);
  }
}

const SVG = "http://www.w3.org/2000/svg";
const f2 = (v) => (Number.isFinite(v) ? v.toFixed(2) : "—");

export class Timeline {
  constructor(host, { onScrub }) {
    this.host = host;
    this.onScrub = onScrub;
    this.model = null;
    this.cursor = 0;
    this.threshold = null;
    host.innerHTML = `<svg class="tl-svg" role="img" aria-label="Incident timeline"></svg>`;
    this.svg = host.firstElementChild;
    this.ro = new ResizeObserver(() => this.draw());
    this.ro.observe(host);
    this.scrubbing = false;
    host.addEventListener("pointerdown", (e) => {
      if (!this.model || this.model.empty) return;
      this.scrubbing = true;
      host.setPointerCapture(e.pointerId);
      this.fromEvent(e);
    });
    host.addEventListener("pointermove", (e) => this.scrubbing && this.fromEvent(e));
    host.addEventListener("pointerup", () => (this.scrubbing = false));
    host.addEventListener("pointercancel", () => (this.scrubbing = false));
  }
  dispose() {
    this.ro.disconnect();
  }
  fromEvent(e) {
    const b = this.svg.getBoundingClientRect();
    const t = this.tOf(e.clientX - b.left);
    this.onScrub(t);
  }
  setModel(model, threshold) {
    this.model = model;
    this.threshold = threshold;
    this.animateIn = true;
    this.draw();
  }
  // Piecewise-linear axis: when the fast EMG window covers only the end of a long clip, the lead-in is
  // compressed (and marked with an axis break) so the brace itself stays legible. Ticks stay honest.
  knots() {
    const m = this.model;
    const [t0, t1] = m.range;
    const w0 = m.emg ? m.emg.rows[0].t : null;
    if (w0 != null && w0 - t0 > 1.8) return [[t0, 0], [w0, 0.25], [t1, 1]];
    return [[t0, 0], [t1, 1]];
  }
  tOf(px) {
    const f = Math.min(1, Math.max(0, (px - this.pad.l) / (this.w - this.pad.l - this.pad.r)));
    const K = this.knots();
    for (let i = 1; i < K.length; i++) if (f <= K[i][1]) return K[i - 1][0] + ((f - K[i - 1][1]) / (K[i][1] - K[i - 1][1])) * (K[i][0] - K[i - 1][0]);
    return K[K.length - 1][0];
  }
  xOf(t) {
    const K = this.knots();
    let f = 1;
    if (t <= K[0][0]) f = 0;
    else
      for (let i = 1; i < K.length; i++)
        if (t <= K[i][0]) {
          f = K[i - 1][1] + ((t - K[i - 1][0]) / (K[i][0] - K[i - 1][0])) * (K[i][1] - K[i - 1][1]);
          break;
        }
    return this.pad.l + f * (this.w - this.pad.l - this.pad.r);
  }

  /** EMG values at time t (interpolated), or null outside the stored window. */
  valuesAt(t) {
    const rows = this.model?.emg?.rows;
    if (!rows || t < rows[0].t || t > rows[rows.length - 1].t) return null;
    let i = 0;
    while (i < rows.length - 2 && rows[i + 1].t < t) i++;
    const a = rows[i];
    const b = rows[i + 1] || a;
    const k = b.t > a.t ? (t - a.t) / (b.t - a.t) : 0;
    return { f: a.f + (b.f - a.f) * k, e: a.e + (b.e - a.e) * k, b: a.b + (b.b - a.b) * k };
  }

  draw() {
    const w = (this.w = this.host.clientWidth);
    const h = (this.h = this.host.clientHeight);
    const svg = this.svg;
    if (!w || !h) return;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    const m = this.model;
    const compact = h < 165;
    this.pad = { l: 40, r: 60, t: compact ? 34 : 40, b: 20 };
    if (!m || m.empty) {
      svg.innerHTML = `<text x="${w / 2}" y="${h / 2}" text-anchor="middle" class="tl-empty">No incident yet. When you brace, the timeline appears here.</text>${this.gridOnly()}`;
      return;
    }
    const { l, r, t, b } = this.pad;
    const bandH = 12;
    const spdH = compact ? 12 : Math.max(14, Math.min(22, h * 0.12));
    const laneAy = t;
    const laneBy = t + bandH + spdH + (compact ? 14 : 24);
    const laneBh = Math.max(36, h - laneBy - b);
    const [t0, t1] = m.range;
    const X = (v) => this.xOf(v);
    const yB = (v, vmax) => laneBy + laneBh - (Math.min(v, vmax) / vmax) * laneBh;
    let s = "";

    // after-freeze shade
    s += `<rect x="${X(0)}" y="${t - 4}" width="${Math.max(0, X(t1) - X(0))}" height="${h - t - b + 4}" class="tl-after"/>`;
    s += `<text x="${X(t1) - 6}" y="${laneAy + 9}" text-anchor="end" class="tl-cap">arm held</text>`;

    // time axis (an axis break marks the compressed lead-in)
    const K = this.knots();
    if (K.length > 2) {
      const xb = X(K[1][0]);
      s += `<path d="M${xb - 3} ${h - b + 1}l6 -8M${xb + 1} ${h - b + 1}l6 -8" class="tl-break"/>`;
    }
    for (let sec = Math.ceil(t0); sec <= Math.floor(t1); sec++) {
      if (K.length > 2 && sec < K[1][0] && Math.abs(sec) % 2 === 1) continue;
      const x = X(sec);
      s += `<path d="M${x} ${laneAy}V${laneBy + laneBh}" class="tl-grid ${sec === 0 ? "zero" : ""}"/>`;
      s += `<text x="${x}" y="${h - 5}" text-anchor="middle" class="tl-tick ${sec === 0 ? "zero" : ""}">${sec === 0 ? "freeze" : `${sec > 0 ? "+" : "−"}${Math.abs(sec)}s`}</text>`;
    }

    // lane A: stage bands + joint speed
    m.bands.forEach((bd) => {
      const x0 = X(bd.t0);
      const x1 = X(bd.t1);
      s += `<rect x="${x0}" y="${laneAy}" width="${Math.max(2, x1 - x0 - 1)}" height="${bandH}" rx="3" class="tl-band"/>`;
      if (x1 - x0 > 66) s += `<text x="${x0 + 6}" y="${laneAy + 9}" class="tl-band-t">${bd.stage}</text>`;
    });
    const vmax = Math.max(20, ...m.speed.map((p) => p.v));
    const sy = (v) => laneAy + bandH + 4 + spdH - (v / vmax) * spdH;
    const area = m.speed.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)} ${sy(p.v).toFixed(1)}`).join("");
    s += `<path d="${area}L${X(m.speed[m.speed.length - 1].t)} ${laneAy + bandH + 4 + spdH}L${X(m.speed[0].t)} ${laneAy + bandH + 4 + spdH}Z" class="tl-speed-fill"/>`;
    s += `<path d="${area}" class="tl-speed"/>`;
    s += `<text x="${l - 8}" y="${laneAy + bandH + 4 + spdH}" text-anchor="end" class="tl-cap">deg/s</text>`;

    // lane B: EMG
    const emg = m.emg;
    const ymax = emg ? Math.max(1, emg.peaks.f, emg.peaks.e, emg.peaks.b) : 1;
    [0, 0.5, 1].forEach((v) => {
      const y = yB(v, ymax);
      s += `<path d="M${l} ${y}H${w - r}" class="tl-grid h"/><text x="${l - 8}" y="${y + 3}" text-anchor="end" class="tl-tick">${v}</text>`;
    });
    if (emg) {
      const x0 = X(emg.rows[0].t);
      const x1 = X(emg.rows[emg.rows.length - 1].t);
      s += `<rect x="${x0}" y="${laneBy}" width="${x1 - x0}" height="${laneBh}" class="tl-win"/>`;
      s += `<text x="${x0 + 6}" y="${laneBy + 11}" class="tl-cap">EMG · last ${(emg.rows[emg.rows.length - 1].t - emg.rows[0].t).toFixed(1)} s</text>`;
      const line = (key, cls) => `<path d="${emg.rows.map((p, i) => `${i ? "L" : "M"}${X(p.t).toFixed(1)} ${yB(p[key], ymax).toFixed(1)}`).join("")}" class="tl-line ${cls}" pathLength="1"/>`;
      s += line("e", "open") + line("f", "grip") + line("b", "brace");
      const last = emg.rows[emg.rows.length - 1];
      const lab = [["f", "grip", "grip"], ["e", "open", "open"], ["b", "brace", "brace"]].map(([k, c, n]) => ({ y: yB(last[k], ymax), c, n })).sort((a, b) => a.y - b.y);
      for (let i = 1; i < lab.length; i++) if (lab[i].y - lab[i - 1].y < 11) lab[i].y = lab[i - 1].y + 11;
      lab.forEach((q) => (s += `<text x="${x1 + 7}" y="${q.y + 3}" class="tl-end ${q.c}">${q.n}</text>`));
      if (this.threshold != null) {
        const ty = yB(this.threshold, ymax);
        s += `<path d="M${l} ${ty}H${w - r}" class="tl-thr"/><text x="${w - r + 6}" y="${ty + 3}" class="tl-thr-t">threshold ${this.threshold}</text>`;
      }
    } else {
      s += `<text x="${(l + w - r) / 2}" y="${laneBy + laneBh / 2}" text-anchor="middle" class="tl-empty">No EMG trace for this incident (the brace event is no longer in the log).</text>`;
    }

    // onset + stop annotations with the real latency
    if (m.onset != null) {
      const xo = X(m.onset);
      s += `<path d="M${xo} ${t - 8}V${laneBy + laneBh}" class="tl-mark onset"/>`;
      s += `<text x="${xo - 7}" y="${t - (compact ? 20 : 22)}" text-anchor="end" class="tl-note">brace onset</text>`;
    }
    if (m.stop) {
      const xs = X(m.stop.t);
      s += `<path d="M${xs} ${t - 8}V${laneBy + laneBh}" class="tl-mark stop"/>`;
      s += `<text x="${xs - 7}" y="${t - (compact ? 8 : 9)}" text-anchor="end" class="tl-note stop">arm stopped${m.stop.latencyMs != null ? ` · <tspan class="tl-num">${m.stop.latencyMs} ms</tspan> after onset` : ""}</text>`;
      if (m.onset != null && xs - X(m.onset) > 5) s += `<path d="M${X(m.onset)} ${t - 4}H${xs}" class="tl-brk"/>`;
    }

    // playhead
    s += `<g class="tl-ph" transform="translate(${X(this.cursor)} 0)"><path d="M0 ${t - 6}V${laneBy + laneBh}" class="tl-ph-line"/><path d="M-6 ${t - 12}h12l-6 8z" class="tl-ph-cap"/></g>`;
    svg.innerHTML = s;
    this.ph = svg.querySelector(".tl-ph");
    if (this.animateIn && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      this.animateIn = false;
      svg.querySelectorAll(".tl-line").forEach((p, i) => p.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }], { duration: 900, delay: 150 + i * 90, easing: "cubic-bezier(.16,1,.3,1)", fill: "backwards" }));
    }
  }
  gridOnly() {
    const { w, h } = this;
    let s = "";
    for (let i = 1; i < 4; i++) s += `<path d="M40 ${(h * i) / 4}H${w - 16}" class="tl-grid h"/>`;
    return s;
  }
  setCursor(t) {
    this.cursor = t;
    if (this.ph && this.model && !this.model.empty) this.ph.setAttribute("transform", `translate(${this.xOf(t)} 0)`);
  }
}

export { fmtT, ago, f2 };
