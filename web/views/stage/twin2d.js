// 2D fallback twin: an overhead SVG of the same data, for browsers without WebGL.
import { BASE, DIM, fk, worldToTable } from "./kin.js";
import { propKind } from "./props.js";

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const damp = (a, b, l, dt) => a + (b - a) * (1 - Math.exp(-l * dt));

export function createTwin2D(host, { reduced = false } = {}) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "-8 -4 116 112");
  svg.setAttribute("class", "tw-canvas tw-svg");
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  host.prepend(svg);

  const cur = { shoulder_pan: 0, shoulder_lift: -90, elbow_flex: 90, wrist_flex: 60, wrist_roll: 0, gripper: 5 };
  let want = { ...cur }, first = true, data = { objects: [], candidates: [] };
  let flash = null, trail = [], now = 0;

  const toXY = (p) => worldToTable(p.x, p.z).map((v) => v * 100);

  function setData(d) {
    data = d;
    if (d.joints) want = { ...cur, ...d.joints };
    if (first && d.joints) { Object.assign(cur, want); first = false; }
  }
  const pulse = () => { flash = now; };

  function frame(dt, t) {
    now = t;
    for (const k of Object.keys(cur)) cur[k] = reduced ? want[k] : damp(cur[k], want[k] ?? cur[k], 15, dt);
    const f = fk(cur);
    const pts = f.pts.map(toXY);
    const tip = pts[4];
    if (!trail.length || Math.hypot(tip[0] - trail.at(-1)[0], tip[1] - trail.at(-1)[1]) > 0.7) trail.push([tip[0], tip[1], t]);
    trail = trail.filter((p) => t - p[2] < 2.6);

    const out = [];
    out.push('<rect class="s-mat" x="0" y="0" width="100" height="100" rx="1.6"/>');
    for (let i = 1; i < 10; i++) out.push(`<path class="s-grid" d="M${i * 10} 0V100M0 ${i * 10}H100"/>`);
    // zones (geometry only if the API gave it)
    const zones = data.zones && !Array.isArray(data.zones) ? data.zones : {};
    for (const [name, b] of Object.entries(zones)) {
      if (!Array.isArray(b) || b.length !== 4) continue;
      out.push(`<rect class="s-zone" x="${b[0] * 100}" y="${b[1] * 100}" width="${(b[2] - b[0]) * 100}" height="${(b[3] - b[1]) * 100}"/><text class="s-zone-t" x="${b[0] * 100 + 1}" y="${b[1] * 100 + 3.4}">avoid · ${esc(name)}</text>`);
    }
    const routes = data.routes && !Array.isArray(data.routes) ? data.routes : {};
    for (const [name, pl] of Object.entries(routes)) {
      if (!Array.isArray(pl)) continue;
      out.push(`<polyline class="s-route ${name === data.routeName ? "on" : ""}" points="${pl.map(([x, y]) => `${x * 100},${y * 100}`).join(" ")}"/>`);
    }
    // heading
    out.push(`<line class="s-head" x1="${pts[0][0]}" y1="${pts[0][1]}" x2="${pts[0][0] + Math.sin(f.pan) * 80}" y2="${pts[0][1] + Math.cos(f.pan) * 80}"/>`);
    // trail
    for (const p of trail) out.push(`<circle class="s-trail" cx="${p[0]}" cy="${p[1]}" r="${0.4 + 0.9 * (1 - (t - p[2]) / 2.6)}" opacity="${(1 - (t - p[2]) / 2.6) * 0.7}"/>`);
    // objects
    for (const o of data.objects || []) {
      if (!o.table_xy) continue;
      const [x, y] = [o.table_xy[0] * 100, o.table_xy[1] * 100];
      const r = { cup: 3.1, ball: 3, bottle: 2.6, block: 3.4 }[propKind(o.id, o.name)];
      const isT = o.id === data.targetId, isC = (data.candidates || []).includes(o.id), held = o.id === data.heldId;
      if (isT) out.push(`<circle class="s-target" cx="${x}" cy="${y}" r="${r + 3.2 + (reduced ? 0 : 0.6 * Math.sin(t * 3))}"/>`);
      if (isC) out.push(`<circle class="s-cand" cx="${x}" cy="${y}" r="${r + 3.4}"/>`);
      const shape = propKind(o.id, o.name) === "block" ? `<rect class="s-obj" x="${x - r}" y="${y - r}" width="${r * 2}" height="${r * 2}" rx="0.8"/>` : `<circle class="s-obj ${propKind(o.id, o.name)}" cx="${x}" cy="${y}" r="${r}"/>`;
      out.push(`<g opacity="${held ? 0.3 : 1}">${shape}</g><text class="s-label ${isT ? "t" : ""}" x="${x}" y="${y + r + 5.2}" text-anchor="middle">${esc(o.name)}${o.virtual ? "" : ` ${Math.round((o.confidence ?? 0) * 100)}%`}</text>`);
    }
    // pointing ray
    if (data.rayWorld && (data.rayActive ?? 0) > 0.3) {
      const [x0, z0, x1, z1] = data.rayWorld.map((v, i) => v);
      const a = worldToTable(x0, z0), b = worldToTable(x1, z1);
      out.push(`<line class="s-ray" x1="${a[0] * 100}" y1="${a[1] * 100}" x2="${b[0] * 100}" y2="${b[1] * 100}"/>`);
    }
    // arm
    const poly = pts.slice(0, 5).map((p) => p.join(",")).join(" ");
    if (data.stopped) out.push(`<polyline class="s-ghost" points="${poly}"/>`);
    out.push(`<polyline class="s-arm" points="${poly}"/>`);
    pts.slice(0, 4).forEach((p, i) => out.push(`<circle class="s-joint" cx="${p[0]}" cy="${p[1]}" r="${i ? 1.5 : 3}"/>`));
    const open = (cur.gripper ?? 5) / 100;
    out.push(`<g transform="translate(${tip[0]} ${tip[1]}) rotate(${(-f.pan * 180) / Math.PI})"><path class="s-jaw" d="M${-1.2 - open * 2.4} 0V2.8M${1.2 + open * 2.4} 0V2.8"/></g>`);
    if (data.heldId) out.push(`<circle class="s-held" cx="${tip[0]}" cy="${tip[1] + 1.6}" r="2.6"/>`);
    out.push(`<circle class="s-glow" cx="${tip[0]}" cy="${tip[1]}" r="${2.4 + (data.load || 0) * 18}" opacity="${0.15 + Math.min(1, (data.load || 0) / 0.5) * 0.6}"/>`);
    if (data.stopped && flash != null) {
      const age = t - flash;
      if (age < 1.5) out.push(`<circle class="s-wave" cx="${tip[0]}" cy="${tip[1]}" r="${2 + age * 28}" opacity="${1 - age / 1.5}"/>`);
      out.push(`<circle class="s-stop" cx="${tip[0]}" cy="${tip[1]}" r="3.6"/>`);
    }
    svg.innerHTML = out.join("");
  }

  return {
    setData, pulse, frame, resize() {}, setPreset() {}, getPreset: () => "top",
    dispose() { svg.remove(); },
  };
}
