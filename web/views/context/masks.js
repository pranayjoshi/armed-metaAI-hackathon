// Mask paint: SAM 3.1 masks over the raw frame, one colour per phrase, labels after the masks.
// Input is state.scene.masks = [{phrase, polygon: [[x,y]..] | bbox: [x0,y0,x1,y1]}], all normalised 0-1.
const NS = "http://www.w3.org/2000/svg";
// Phrase hues. These are identity colours for objects, not state colours: they avoid the token hues' meanings by staying pastel.
const PALETTE = ["#6fb7ff", "#ff8fb1", "#b4e36a", "#ffb86b", "#c79bff", "#5fe0c8", "#ffe36b", "#ff7a6b"];

export const phraseColors = (masks) => {
  const map = new Map();
  for (const m of masks) if (!map.has(m.phrase)) map.set(m.phrase, PALETTE[map.size % PALETTE.length]);
  return map;
};

const points = (m) => {
  if (Array.isArray(m.polygon) && m.polygon.length >= 3) return m.polygon.map(([x, y]) => [x, y]);
  if (Array.isArray(m.bbox) && m.bbox.length === 4) {
    const [a, b, c, d] = m.bbox;
    return [[a, b], [c, b], [c, d], [a, d]];
  }
  return null;
};
const centroid = (pts) => {
  const x0 = Math.min(...pts.map((p) => p[0])), x1 = Math.max(...pts.map((p) => p[0]));
  const y0 = Math.min(...pts.map((p) => p[1]));
  return [(x0 + x1) / 2, y0];
};

export class MaskLayer {
  constructor(host) {
    this.svg = document.createElementNS(NS, "svg");
    this.svg.setAttribute("viewBox", "0 0 1000 1000");
    this.svg.setAttribute("preserveAspectRatio", "none");
    this.svg.classList.add("cx-masks");
    this.labels = document.createElement("div");
    this.labels.className = "cx-labels";
    host.append(this.svg, this.labels);
    this.timers = [];
  }
  clear() {
    this.timers.forEach(clearTimeout);
    this.timers = [];
    this.svg.replaceChildren();
    this.labels.replaceChildren();
  }
  /** Paint masks one after another, then the labels. `instant` shows the settled result. Returns ms until done. */
  paint(masks, { instant = false } = {}) {
    this.clear();
    const colors = phraseColors(masks);
    const step = instant ? 0 : 140;
    const drawn = [];
    masks.forEach((m) => {
      const pts = points(m);
      if (!pts) return;
      const i = drawn.length;
      const poly = document.createElementNS(NS, "polygon");
      poly.setAttribute("points", pts.map(([x, y]) => `${(x * 1000).toFixed(1)},${(y * 1000).toFixed(1)}`).join(" "));
      const c = colors.get(m.phrase);
      poly.style.setProperty("--c", c);
      poly.classList.add("cx-mask");
      if (instant) poly.classList.add("on");
      else this.timers.push(setTimeout(() => poly.classList.add("on"), i * step + 30));
      this.svg.append(poly);
      drawn.push({ m, pts, c });
    });
    const labelAt = drawn.length * step + (instant ? 0 : 260);
    drawn.forEach(({ m, pts, c }, i) => {
      const [x, y] = centroid(pts);
      const el = document.createElement("span");
      el.className = "cx-label";
      el.textContent = m.phrase;
      el.style.cssText = `left:${(x * 100).toFixed(2)}%;top:${(y * 100).toFixed(2)}%;--c:${c}`;
      if (instant) el.classList.add("on");
      else this.timers.push(setTimeout(() => el.classList.add("on"), labelAt + i * 70));
      this.labels.append(el);
    });
    return instant ? 0 : labelAt + drawn.length * 70 + 400;
  }
}
