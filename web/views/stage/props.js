// Table props (cup / block / ball / bottle) and canvas textures for the Stage twin.
import { rbox } from "./arm3d.js";
import { DIM } from "./kin.js";

export function glowTexture(T, inner = "rgba(255,255,255,1)", outer = "rgba(255,255,255,0)") {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
  grd.addColorStop(0, inner);
  grd.addColorStop(0.35, "rgba(255,255,255,.35)");
  grd.addColorStop(1, outer);
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  const t = new T.CanvasTexture(c);
  t.colorSpace = T.SRGBColorSpace;
  return t;
}

/** Vertical fade used by the target beam and the pointing ray. */
export function fadeTexture(T) {
  const c = document.createElement("canvas");
  c.width = 4; c.height = 128;
  const g = c.getContext("2d");
  const grd = g.createLinearGradient(0, 0, 0, 128);
  grd.addColorStop(0, "rgba(255,255,255,0)");
  grd.addColorStop(1, "rgba(255,255,255,1)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 4, 128);
  return new T.CanvasTexture(c);
}

/** Workspace mat: the normalised 0-1 table square, gridded every 0.1, with axis ticks. */
export function matTexture(T) {
  const N = 1024, c = document.createElement("canvas");
  c.width = c.height = N;
  const g = c.getContext("2d");
  g.fillStyle = "rgba(20,32,48,.55)";
  g.fillRect(0, 0, N, N);
  g.strokeStyle = "rgba(160,190,230,.10)";
  g.lineWidth = 2;
  for (let i = 0; i <= 10; i++) {
    const p = (i / 10) * (N - 4) + 2;
    g.beginPath(); g.moveTo(p, 0); g.lineTo(p, N); g.moveTo(0, p); g.lineTo(N, p); g.stroke();
  }
  g.strokeStyle = "rgba(160,190,230,.26)";
  g.lineWidth = 3;
  for (const i of [0, 5, 10]) {
    const p = (i / 10) * (N - 4) + 2;
    g.beginPath(); g.moveTo(p, 0); g.lineTo(p, N); g.moveTo(0, p); g.lineTo(N, p); g.stroke();
  }
  // corner brackets
  g.strokeStyle = "rgba(79,216,255,.7)";
  g.lineWidth = 6;
  const L = 60, m = 5;
  for (const [x, y, sx, sy] of [[m, m, 1, 1], [N - m, m, -1, 1], [m, N - m, 1, -1], [N - m, N - m, -1, -1]]) {
    g.beginPath(); g.moveTo(x + sx * L, y); g.lineTo(x, y); g.lineTo(x, y + sy * L); g.stroke();
  }
  g.fillStyle = "rgba(170,182,198,.55)";
  g.font = "500 22px 'JetBrains Mono', ui-monospace, monospace";
  g.textBaseline = "top";
  for (const i of [0, 5, 10]) {
    g.fillText((i / 10).toFixed(1), (i / 10) * (N - 80) + 14, N - 34);
    g.fillText((i / 10).toFixed(1), 14, (i / 10) * (N - 60) + 12);
  }
  const t = new T.CanvasTexture(c);
  t.colorSpace = T.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

function ballTexture(T) {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 128;
  const g = c.getContext("2d");
  g.fillStyle = "#b9d63a";
  g.fillRect(0, 0, 256, 128);
  g.strokeStyle = "rgba(255,255,255,.85)";
  g.lineWidth = 5;
  for (const x of [64, 192]) {
    g.beginPath();
    g.ellipse(x, 64, 22, 90, 0, 0, Math.PI * 2);
    g.stroke();
  }
  const t = new T.CanvasTexture(c);
  t.colorSpace = T.SRGBColorSpace;
  return t;
}

/** Which primitive stands in for an object. Unknown ids fall back on words in the name. */
export function propKind(id = "", name = "") {
  const s = `${id} ${name}`.toLowerCase();
  if (/cup|mug|glass/.test(s)) return "cup";
  if (/ball/.test(s)) return "ball";
  if (/bottle|can/.test(s)) return "bottle";
  return "block";
}

/**
 * Build a prop standing on y=0. Returns { group, height, mats, geos } so callers can fade and dispose it.
 */
export function buildProp(T, kind) {
  const group = new T.Group();
  const mats = [], geos = [];
  const mat = (o) => { const m = new T.MeshStandardMaterial(o); mats.push(m); return m; };
  const mesh = (g, m, x = 0, y = 0, z = 0) => {
    geos.push(g);
    const o = new T.Mesh(g, m);
    o.position.set(x, y, z);
    o.castShadow = true;
    o.receiveShadow = true;
    group.add(o);
    return o;
  };
  let height = 6;
  if (kind === "cup") {
    height = 8.6;
    const pts = [[0, 0], [2.3, 0], [2.5, 0.3], [3.4, 8.0], [3.7, 8.2], [3.55, 8.6], [3.2, 8.5], [3.3, 8.1], [2.4, 0.9], [0, 0.9]]
      .map(([r, y]) => new T.Vector2(r, y));
    mesh(new T.LatheGeometry(pts, 40), mat({ color: 0xe9e4d8, roughness: 0.8, metalness: 0, side: T.DoubleSide }));
    mesh(new T.CylinderGeometry(3.12, 2.78, 2.8, 40, 1, true), mat({ color: 0x8a5a44, roughness: 0.7, side: T.DoubleSide }), 0, 4.2, 0);
  } else if (kind === "ball") {
    height = 6.6;
    mesh(new T.SphereGeometry(3.3, 32, 20), mat({ map: ballTexture(T), roughness: 0.85 }), 0, 3.3, 0);
  } else if (kind === "bottle") {
    height = 12;
    mesh(new T.CylinderGeometry(2.6, 2.8, 8.4, 32), mat({ color: 0x6aa6d8, roughness: 0.25, metalness: 0.1, transparent: true, opacity: 0.8 }), 0, 4.2, 0);
    mesh(new T.CylinderGeometry(1.1, 2.4, 2.4, 24), mat({ color: 0x6aa6d8, roughness: 0.25, transparent: true, opacity: 0.8 }), 0, 9.6, 0);
    mesh(new T.CylinderGeometry(1.2, 1.2, 1.1, 20), mat({ color: 0xdfe6ee, roughness: 0.6 }), 0, 11.4, 0);
  } else {
    height = 4.4;
    mesh(rbox(T, 4.6, 4.4, 4.6, 0.5), mat({ color: 0xb98a55, roughness: 0.75 }), 0, 2.2, 0);
  }
  return { group, height, mats, geos, kind };
}

/** A flat dashed ring made of arcs (WebGL lines are 1 px, so build it from geometry). */
export function dashedRing(T, r = 5.8, w = 0.28, count = 16, fill = 0.55) {
  const seg = (Math.PI * 2) / count;
  const geos = [];
  for (let i = 0; i < count; i++) geos.push(new T.RingGeometry(r - w / 2, r + w / 2, 6, 1, i * seg, seg * fill));
  const merged = mergeGeos(T, geos);
  geos.forEach((g) => g.dispose());
  merged.rotateX(-Math.PI / 2);
  return merged;
}

function mergeGeos(T, list) {
  let n = 0, ni = 0;
  list.forEach((g) => { n += g.attributes.position.count; ni += g.index.count; });
  const pos = new Float32Array(n * 3), idx = new Uint32Array(ni);
  let o = 0, io = 0;
  for (const g of list) {
    pos.set(g.attributes.position.array, o * 3);
    for (let i = 0; i < g.index.count; i++) idx[io + i] = g.index.array[i] + o;
    o += g.attributes.position.count;
    io += g.index.count;
  }
  const out = new T.BufferGeometry();
  out.setAttribute("position", new T.BufferAttribute(pos, 3));
  out.setIndex(new T.BufferAttribute(idx, 1));
  return out;
}

export const S = DIM.S;
