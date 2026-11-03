// Packets: glowing dots that travel along an edge path. One pooled <g> per packet.
// A packet is created only in response to a runtime event; it never loops.

const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}) => {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

const SPEED = 560; // px per second along a path
const MIN_MS = 380;
const MAX_MS = 1000;

export class Packets {
  /**
   * @param layer   <g> that holds packet visuals
   * @param paths   Map<edgeId, {path: SVGPathElement, len: number}>
   * @param reduced true: no travel, a short fade at the destination instead
   */
  constructor(layer, paths, reduced) {
    this.layer = layer;
    this.paths = paths;
    this.reduced = reduced;
    this.live = [];
    this.pool = [];
  }

  /** Duration a packet needs to cross an edge. */
  duration(edgeId) {
    const p = this.paths.get(edgeId);
    if (this.reduced) return 160;
    const ms = ((p?.len || 120) / SPEED) * 1000;
    return Math.min(MAX_MS, Math.max(MIN_MS, ms));
  }

  /**
   * Send a packet. start/dur are performance.now() based (ms).
   * @param o {edge, hue, start, dur, from (0..1 start fraction), onStart, onArrive, kind: 'stub'|undefined}
   */
  send(o) {
    const p = this.paths.get(o.edge);
    if (!p) {
      o.onStart?.();
      o.onArrive?.();
      return;
    }
    const pk = { ...o, p, dur: o.dur ?? this.duration(o.edge), started: false, node: null };
    this.live.push(pk);
  }

  /** Run fn at performance.now() >= start (kept on the same clock as packets, so ordering holds). */
  at(start, fn) {
    this.live.push({ start, timer: true, onArrive: fn });
  }

  _acquire(pk) {
    const g = this.pool.pop() || this._make();
    g.style.setProperty("--h", `var(--${pk.hue || "grip"})`);
    g.style.display = "";
    const len = pk.p.len;
    const trail = Math.min(len * 0.5, 120);
    for (const t of [g._t1, g._t2]) {
      t.setAttribute("d", pk.p.d);
    }
    pk.trail = trail;
    pk.node = g;
    this.layer.append(g);
  }

  _make() {
    const g = el("g", { class: "pk" });
    const t1 = el("path", { class: "pk-trail wide", fill: "none" });
    const t2 = el("path", { class: "pk-trail", fill: "none" });
    const halo = el("circle", { class: "pk-halo", r: 17 });
    const mid = el("circle", { class: "pk-mid", r: 9 });
    const core = el("circle", { class: "pk-core", r: 3.8 });
    g.append(t1, t2, halo, mid, core);
    Object.assign(g, { _t1: t1, _t2: t2, _halo: halo, _mid: mid, _core: core });
    return g;
  }

  _release(pk) {
    if (!pk.node) return;
    pk.node.style.display = "none";
    pk.node.remove();
    this.pool.push(pk.node);
    pk.node = null;
  }

  tick(now) {
    const keep = [];
    for (const pk of this.live) {
      if (now < pk.start) {
        keep.push(pk);
        continue;
      }
      if (pk.timer) {
        pk.onArrive();
        continue;
      }
      if (!pk.started) {
        pk.started = true;
        pk.onStart?.();
        if (!this.reduced) this._acquire(pk);
      }
      const k = Math.min(1, (now - pk.start) / pk.dur);
      if (this.reduced) {
        if (k >= 1) pk.onArrive?.();
        else keep.push(pk);
        continue;
      }
      const f0 = pk.from || 0;
      const s = (f0 + (1 - f0) * easeInOut(k)) * pk.p.len;
      const pt = pk.p.path.getPointAtLength(s);
      const g = pk.node;
      // fade in at the start, out in the last 12%
      const alpha = Math.min(1, k / 0.1, (1 - k) / 0.12 + 0.0001);
      g.style.opacity = String(Math.max(0, Math.min(1, alpha)));
      g._halo.setAttribute("cx", pt.x.toFixed(1));
      g._halo.setAttribute("cy", pt.y.toFixed(1));
      g._mid.setAttribute("cx", pt.x.toFixed(1));
      g._mid.setAttribute("cy", pt.y.toFixed(1));
      g._core.setAttribute("cx", pt.x.toFixed(1));
      g._core.setAttribute("cy", pt.y.toFixed(1));
      const T = pk.trail;
      const len = pk.p.len;
      g._t1.style.strokeDasharray = `${T} ${len + T}`;
      g._t1.style.strokeDashoffset = String(T - s);
      g._t2.style.strokeDasharray = `${T * 0.45} ${len + T}`;
      g._t2.style.strokeDashoffset = String(T * 0.45 - s);
      if (k >= 1) {
        this._release(pk);
        pk.onArrive?.();
      } else {
        keep.push(pk);
      }
    }
    this.live = keep;
  }

  clear() {
    for (const pk of this.live) this._release(pk);
    this.live = [];
  }
}
