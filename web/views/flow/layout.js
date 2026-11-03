// Geometry: grid -> world coordinates, edge ports, and smooth Bezier paths.
import { COLS, EDGES, NODES, NODE_BY_ID, ROWS } from "./graph.js";

export const NODE_W = 160;
export const NODE_H = 78;
export const PX = 192;
export const PY = 108;
export const PAD = 36;

export const WORLD_W = PAD * 2 + (COLS - 1) * PX + NODE_W;
export const WORLD_H = PAD * 2 + (ROWS - 1) * PY + NODE_H + 24;

export const colX = (c) => PAD + c * PX + NODE_W / 2; // centre x of a column (float ok)
export const rowY = (r) => PAD + (ROWS - 1 - r) * PY + NODE_H / 2; // centre y of a row (float ok)

export function boxOf(n) {
  const span = n.span || 1;
  const w = NODE_W + (span - 1) * PX;
  const x = PAD + n.col * PX;
  const y = rowY(n.row) - NODE_H / 2;
  return { x, y, w, h: NODE_H, cx: x + w / 2, cy: y + NODE_H / 2 };
}

const OUT = { top: [0, -1], bottom: [0, 1], left: [-1, 0], right: [1, 0] };

function portOf(n, side, at) {
  const b = boxOf(n);
  const out = OUT[side];
  const px = at != null ? colX(at) : b.cx;
  const x = side === "left" ? b.x : side === "right" ? b.x + b.w : px;
  const y = side === "top" ? b.y : side === "bottom" ? b.y + b.h : b.cy;
  return { x, y, out };
}

/** Smooth path through points with given end directions (unit vectors of travel). */
function smooth(pts, d0, d1) {
  const n = pts.length;
  const tan = pts.map((p, i) => {
    if (i === 0) {
      const l = Math.hypot(pts[1].x - p.x, pts[1].y - p.y);
      return { x: d0[0] * l * 1.15, y: d0[1] * l * 1.15 };
    }
    if (i === n - 1) {
      const l = Math.hypot(p.x - pts[n - 2].x, p.y - pts[n - 2].y);
      return { x: d1[0] * l * 1.15, y: d1[1] * l * 1.15 };
    }
    return { x: (pts[i + 1].x - pts[i - 1].x) * 0.5, y: (pts[i + 1].y - pts[i - 1].y) * 0.5 };
  });
  let d = `M${pts[0].x.toFixed(1)} ${pts[0].y.toFixed(1)}`;
  for (let i = 0; i < n - 1; i++) {
    const a = pts[i];
    const b = pts[i + 1];
    const c1 = { x: a.x + tan[i].x / 3, y: a.y + tan[i].y / 3 };
    const c2 = { x: b.x - tan[i + 1].x / 3, y: b.y - tan[i + 1].y / 3 };
    d += `C${c1.x.toFixed(1)} ${c1.y.toFixed(1)} ${c2.x.toFixed(1)} ${c2.y.toFixed(1)} ${b.x.toFixed(1)} ${b.y.toFixed(1)}`;
  }
  return d;
}

/** Everything the renderer needs to draw one edge. */
export function edgeGeometry(e) {
  const a = NODE_BY_ID[e.from];
  const b = NODE_BY_ID[e.to];
  const p0 = portOf(a, e.fromSide || "top", e.fromAt);
  const p1 = portOf(b, e.toSide || "bottom", e.toAt);
  const into = [-p1.out[0], -p1.out[1]]; // direction of travel on arrival
  const pts = [p0, ...(e.via || []).map(([c, r]) => ({ x: colX(c), y: rowY(r) })), p1];
  const d = smooth(pts, p0.out, into);
  return { d, p0, p1, into };
}

export function allGeometry() {
  return new Map(EDGES.map((e) => [e.id, edgeGeometry(e)]));
}

export { NODES };
