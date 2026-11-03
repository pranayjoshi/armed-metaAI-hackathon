// The Simulation table: a top-down-ish three.js scene (2D canvas fallback) drawn ONLY from API data.
//   zones / objects      incident meta (table_box / table_xy), same normalised 0-1 table as the schematic frame
//   gripper              scenario.gripper_xy (the server's schematic estimate) + arm.stage / arm.joints.gripper
//   trails / frozen / placed   accumulated by the Simulation view from that same estimate and from `runs`
// Nothing here invents a route: the lines are where the estimate actually went.
import { tableToWorld, DIM } from "/web/views/stage/kin.js";
import { buildProp, propKind, matTexture, glowTexture } from "/web/views/stage/props.js";
import { rbox } from "/web/views/stage/arm3d.js";

const HEX = { brace: 0xff4d5e, ok: 0x5af0a4, grip: 0x4fd8ff, warn: 0xffc15a };
const CSS = { brace: "#ff4d5e", ok: "#5af0a4", grip: "#4fd8ff", warn: "#ffc15a" };
const HOVER = { approach: 12, grasp: 7.5, carry: 12.5, release: 9, return: 12, home: 13 };
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const damp = (a, b, l, dt) => a + (b - a) * (1 - Math.exp(-l * dt));

/** Shared per-frame state: smoothed gripper position / hover height / jaw opening. */
function makeSmooth() {
  return { x: null, y: null, hh: 12, jaw: 0.6, ring: 0 };
}
function stepSmooth(sm, model, dt) {
  const g = model.grip || {};
  const stopped = g.state === "stopped" || g.state === "fault";
  if (g.xy) {
    if (sm.x == null) [sm.x, sm.y] = g.xy;
    else {
      sm.x = damp(sm.x, g.xy[0], 11, dt);
      sm.y = damp(sm.y, g.xy[1], 11, dt);
    }
  }
  const held = !!g.held;
  const want = (HOVER[g.stage] ?? 12) - (held || g.stage !== "grasp" ? 0 : 2.5);
  sm.hh = damp(sm.hh, want, 6, dt);
  sm.jaw = damp(sm.jaw, 0.55 + clamp((g.gripper ?? 40) / 100, 0, 1) * 1.7, 9, dt);
  sm.ring = damp(sm.ring, stopped ? 1 : 0, 10, dt);
  return stopped;
}

export async function createTable(ctx, host) {
  try {
    const T = await ctx.loadThree();
    return create3D(T, host, ctx);
  } catch (err) {
    console.info("[replay] 2D table (no WebGL):", err?.message || err);
    return create2D(host, ctx);
  }
}

// ═══ three.js ═══════════════════════════════════════════════════════════════
class Ribbon {
  constructor(T, color, { width = 0.9, max = 900, opacity = 0.95, y = 0.25, additive = true } = {}) {
    this.T = T;
    this.width = width;
    this.max = max;
    this.y = y;
    this.base = new T.Color(color);
    const g = (this.geo = new T.BufferGeometry());
    this.pos = new Float32Array(max * 2 * 3);
    this.col = new Float32Array(max * 2 * 4);
    g.setAttribute("position", new T.BufferAttribute(this.pos, 3).setUsage(T.DynamicDrawUsage));
    g.setAttribute("color", new T.BufferAttribute(this.col, 4).setUsage(T.DynamicDrawUsage));
    const idx = [];
    for (let i = 0; i < max - 1; i++) {
      const a = i * 2;
      idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
    g.setIndex(idx);
    g.setDrawRange(0, 0);
    this.mat = new T.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity, depthWrite: false, side: T.DoubleSide, blending: additive ? T.AdditiveBlending : T.NormalBlending });
    this.mesh = new T.Mesh(g, this.mat);
    this.mesh.frustumCulled = false;
    this.mesh.renderOrder = 2;
  }
  /** pts: [[wx, wz, alpha 0..1]] */
  set(pts) {
    const n = Math.min(pts.length, this.max);
    const off = pts.length - n;
    for (let i = 0; i < n; i++) {
      const [px, pz, al = 1] = pts[off + i];
      const a = pts[Math.max(0, off + i - 1)];
      const b = pts[Math.min(pts.length - 1, off + i + 1)];
      let dx = b[0] - a[0];
      let dz = b[1] - a[1];
      const len = Math.hypot(dx, dz) || 1;
      dx /= len;
      dz /= len;
      const hw = (this.width * (0.45 + 0.55 * al)) / 2;
      const o = i * 6;
      this.pos[o] = px - dz * hw;
      this.pos[o + 1] = this.y;
      this.pos[o + 2] = pz + dx * hw;
      this.pos[o + 3] = px + dz * hw;
      this.pos[o + 4] = this.y;
      this.pos[o + 5] = pz - dx * hw;
      for (const j of [i * 8, i * 8 + 4]) {
        this.col[j] = this.base.r;
        this.col[j + 1] = this.base.g;
        this.col[j + 2] = this.base.b;
        this.col[j + 3] = al;
      }
    }
    this.geo.attributes.position.needsUpdate = true;
    this.geo.attributes.color.needsUpdate = true;
    this.geo.setDrawRange(0, Math.max(0, n - 1) * 6);
  }
  dispose() {
    this.geo.dispose();
    this.mat.dispose();
  }
}

function create3D(T, host, ctx) {
  const reduced = ctx.reduced;
  const renderer = new T.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.outputColorSpace = T.SRGBColorSpace;
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.12;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFShadowMap;
  renderer.domElement.className = "tbl-canvas";
  host.prepend(renderer.domElement);
  const labelLayer = document.createElement("div");
  labelLayer.className = "tbl-labels";
  host.append(labelLayer);

  const scene = new T.Scene();
  const camera = new T.PerspectiveCamera(34, 1, 1, 800);
  const own = [];
  const keep = (o) => (own.push(o), o);

  scene.add(new T.HemisphereLight(0x9bb8ff, 0x0a0f18, 0.75));
  const key = new T.DirectionalLight(0xdbe8ff, 2.1);
  key.position.set(-34, 80, 46);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  Object.assign(key.shadow.camera, { left: -50, right: 50, top: 50, bottom: -50, near: 10, far: 220 });
  key.shadow.bias = -0.0006;
  key.shadow.radius = 4;
  scene.add(key);
  const rim = new T.DirectionalLight(HEX.grip, 0.8);
  rim.position.set(30, 14, -80);
  scene.add(rim);
  const fill = new T.DirectionalLight(0x6a5cff, 0.45);
  fill.position.set(70, 20, 50);
  scene.add(fill);

  // table
  const slab = new T.Mesh(keep(rbox(T, 70, 62, 3, 6)), keep(new T.MeshStandardMaterial({ color: 0x080d15, roughness: 0.7, metalness: 0.15 })));
  slab.rotation.x = -Math.PI / 2;
  slab.position.y = -1.5;
  slab.receiveShadow = true;
  scene.add(slab);
  const mat = new T.Mesh(keep(new T.PlaneGeometry(DIM.S, DIM.S)), keep(new T.MeshStandardMaterial({ map: keep(matTexture(T)), transparent: true, roughness: 0.9, emissive: 0x16304a, emissiveIntensity: 0.25 })));
  mat.rotation.x = -Math.PI / 2;
  mat.position.y = 0.03;
  mat.receiveShadow = true;
  scene.add(mat);
  // "user side" edge: a soft bar on the near edge so the orientation reads
  const userEdge = new T.Mesh(keep(new T.PlaneGeometry(DIM.S * 0.56, 0.7)), keep(new T.MeshBasicMaterial({ color: HEX.grip, transparent: true, opacity: 0.35, blending: T.AdditiveBlending, depthWrite: false })));
  userEdge.rotation.x = -Math.PI / 2;
  userEdge.position.set(0, 0.08, DIM.S / 2 + 1.6);
  scene.add(userEdge);

  const groups = { zones: new T.Group(), objects: new T.Group(), marks: new T.Group(), trails: new T.Group(), fx: new T.Group() };
  Object.values(groups).forEach((g) => scene.add(g));

  // labels (HTML, projected)
  const labels = new Map(); // key -> { el, pos, text }
  const label = (id, text, cls, pos) => {
    let l = labels.get(id);
    if (!l) {
      const el = document.createElement("span");
      el.className = `tbl-l ${cls}`;
      labelLayer.append(el);
      l = { el, pos: new T.Vector3(), text: "", cls };
      labels.set(id, l);
    }
    if (l.text !== text) l.el.textContent = l.text = text;
    if (l.cls !== cls) l.el.className = `tbl-l ${(l.cls = cls)}`;
    l.pos.set(pos[0], pos[1], pos[2]);
    l.seen = true;
    return l;
  };
  const dropLabels = () => {
    for (const [k, l] of labels) if (!l.seen) (l.el.remove(), labels.delete(k));
    for (const l of labels.values()) l.seen = false;
  };

  // gripper marker ───────────────────────────────────────────────────────────
  const gripG = new T.Group();
  scene.add(gripG);
  const ringMat = keep(new T.MeshBasicMaterial({ color: HEX.ok, transparent: true, opacity: 0.95, side: T.DoubleSide, depthWrite: false }));
  const ring = new T.Mesh(keep(new T.RingGeometry(2.4, 2.9, 48)), ringMat);
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.2;
  gripG.add(ring);
  const glowTex = keep(glowTexture(T));
  const glowMat = keep(new T.SpriteMaterial({ map: glowTex, color: HEX.ok, transparent: true, opacity: 0.55, blending: T.AdditiveBlending, depthWrite: false }));
  const glow = new T.Sprite(glowMat);
  glow.scale.set(15, 15, 1);
  glow.position.y = 0.6;
  gripG.add(glow);
  const stem = new T.Mesh(keep(new T.CylinderGeometry(0.1, 0.1, 1, 8)), keep(new T.MeshBasicMaterial({ color: HEX.grip, transparent: true, opacity: 0.45, blending: T.AdditiveBlending, depthWrite: false })));
  gripG.add(stem);
  const body = new T.Group();
  gripG.add(body);
  const servoMat = keep(new T.MeshStandardMaterial({ color: 0x0f1620, metalness: 0.4, roughness: 0.5 }));
  const shellMat = keep(new T.MeshStandardMaterial({ color: 0x44597a, metalness: 0.45, roughness: 0.4 }));
  const accMat = keep(new T.MeshStandardMaterial({ color: HEX.ok, emissive: HEX.ok, emissiveIntensity: 0.9, roughness: 0.4 }));
  const mk = (geo, m, x, y, z, cast = true) => {
    const o = new T.Mesh(keep(geo), m);
    o.position.set(x, y, z);
    o.castShadow = cast;
    body.add(o);
    return o;
  };
  mk(rbox(T, 4.6, 2.2, 4.6, 0.8), shellMat, 0, 1.1, 0);
  mk(new T.BoxGeometry(3.2, 0.5, 3.2), servoMat, 0, 2.4, 0);
  mk(new T.BoxGeometry(4.7, 0.3, 0.5), accMat, 0, 1.2, 2.35, false);
  const jawL = mk(new T.BoxGeometry(0.55, 3.4, 2.2), shellMat, -1.3, -1.4, 0);
  const jawR = mk(new T.BoxGeometry(0.55, 3.4, 2.2), shellMat, 1.3, -1.4, 0);
  let heldProp = null; // { id, obj }

  // pulses (expanding rings)
  const pulses = [];
  const pulseGeo = keep(new T.RingGeometry(0.92, 1, 64));
  function pulse(xy, kind = "brace") {
    if (reduced) return;
    const [wx, wz] = tableToWorld(xy[0], xy[1]);
    for (let i = 0; i < 2; i++) {
      const m = keep(new T.MeshBasicMaterial({ color: HEX[kind] || HEX.brace, transparent: true, opacity: 0, side: T.DoubleSide, depthWrite: false, blending: T.AdditiveBlending }));
      const mesh = new T.Mesh(pulseGeo, m);
      mesh.rotation.x = -Math.PI / 2;
      mesh.position.set(wx, 0.35, wz);
      groups.fx.add(mesh);
      pulses.push({ mesh, m, age: -i * 0.28, life: 1.5, R: 15 + i * 7 });
    }
  }

  // scene content that changes with the model ────────────────────────────────
  const zoneObjs = new Map(); // name -> { g, vol, edges, mat }
  const props = new Map(); // id -> { kind, group, base }
  const trails = new Map(); // id -> { core, halo, end }
  const markers = new Map(); // key -> group
  let model = { zones: [], objects: [], grip: {}, trails: [], placed: {}, hot: false };
  let sig = { zones: "", objects: "" };

  function buildZone(z) {
    const [x0, y0, x1, y1] = z.box;
    const [wx0, wz0] = tableToWorld(x0, y0);
    const [wx1, wz1] = tableToWorld(x1, y1);
    const w = Math.abs(wx1 - wx0);
    const d = Math.abs(wz1 - wz0);
    const g = new T.Group();
    g.position.set((wx0 + wx1) / 2, 0, (wz0 + wz1) / 2);
    // laptop
    const baseMat = keep(new T.MeshStandardMaterial({ color: 0x2b3547, metalness: 0.6, roughness: 0.38 }));
    const base = new T.Mesh(keep(rbox(T, w * 0.9, 0.7, d * 0.9, 0.35)), baseMat);
    base.position.y = 0.4;
    base.castShadow = base.receiveShadow = true;
    g.add(base);
    const keys = new T.Mesh(keep(new T.PlaneGeometry(w * 0.7, d * 0.42)), keep(new T.MeshBasicMaterial({ color: 0x0b111b })));
    keys.rotation.x = -Math.PI / 2;
    keys.position.set(0, 0.78, d * 0.1);
    g.add(keys);
    const hinge = new T.Group();
    hinge.position.set(0, 0.7, -d * 0.4);
    hinge.rotation.x = -0.3;
    g.add(hinge);
    const sh = Math.min(w * 0.82, d * 0.8);
    const scr = new T.Mesh(keep(rbox(T, w * 0.9, sh, 0.4, 0.3)), baseMat);
    scr.position.y = sh / 2;
    scr.castShadow = true;
    hinge.add(scr);
    const face = new T.Mesh(keep(new T.PlaneGeometry(w * 0.78, sh * 0.82)), keep(new T.MeshBasicMaterial({ color: 0x1a4a78 })));
    face.position.set(0, sh / 2, 0.22);
    hinge.add(face);
    // avoid volume
    const volMat = keep(new T.MeshBasicMaterial({ color: HEX.brace, transparent: true, opacity: 0.07, depthWrite: false, side: T.DoubleSide }));
    const vol = new T.Mesh(keep(new T.BoxGeometry(w, 6, d)), volMat);
    vol.position.y = 3;
    vol.renderOrder = 1;
    g.add(vol);
    const edgeMat = keep(new T.LineBasicMaterial({ color: HEX.brace, transparent: true, opacity: 0.75 }));
    const edges = new T.LineSegments(keep(new T.EdgesGeometry(vol.geometry)), edgeMat);
    edges.position.copy(vol.position);
    g.add(edges);
    const floorMat = keep(new T.MeshBasicMaterial({ color: HEX.brace, transparent: true, opacity: 0.2, depthWrite: false }));
    const floor = new T.Mesh(keep(new T.PlaneGeometry(w + 1.6, d + 1.6)), floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = 0.1;
    g.add(floor);
    groups.zones.add(g);
    return { g, volMat, edgeMat, floorMat, top: [g.position.x, 8, g.position.z], name: z.name };
  }

  function syncGeometry() {
    const zs = JSON.stringify(model.zones);
    if (zs !== sig.zones) {
      sig.zones = zs;
      for (const z of zoneObjs.values()) groups.zones.remove(z.g);
      zoneObjs.clear();
      model.zones.forEach((z) => zoneObjs.set(z.name, buildZone(z)));
    }
    const os = JSON.stringify(model.objects.map((o) => [o.id, o.name]));
    if (os !== sig.objects) {
      sig.objects = os;
      for (const p of props.values()) groups.objects.remove(p.group);
      props.clear();
      model.objects.forEach((o) => {
        const p = buildProp(T, propKind(o.id, o.name));
        own.push(...p.geos, ...p.mats);
        groups.objects.add(p.group);
        props.set(o.id, { ...p, id: o.id, name: o.name });
      });
    }
  }

  function syncTrails() {
    const live = new Set();
    model.trails.forEach((tr, ti) => {
      live.add(tr.id);
      let r = trails.get(tr.id);
      if (!r) {
        const col = HEX[tr.color] || HEX.grip;
        r = { core: new Ribbon(T, col, { width: 1.1, opacity: 1, additive: false, y: 0.32 }), halo: new Ribbon(T, col, { width: 3.6, opacity: 0.22, y: 0.2 }) };
        groups.trails.add(r.halo.mesh, r.core.mesh);
        trails.set(tr.id, r);
      }
      // the carry part of the route is the story; the approach and the return are drawn faint
      const pts = tr.pts.map((p) => [...tableToWorld(p[0], p[1]), p[2] ? 1 : 0.18]);
      if (tr.active && sm.x != null) pts.push([...tableToWorld(sm.x, sm.y), tr.carrying ? 1 : 0.18]);
      if (pts.length >= 2) {
        r.core.set(pts);
        r.halo.set(pts);
      }
    });
    for (const [id, r] of trails) if (!live.has(id)) (groups.trails.remove(r.core.mesh, r.halo.mesh), r.core.dispose(), r.halo.dispose(), trails.delete(id));
  }

  function xMark(color) {
    const g = new T.Group();
    const m = keep(new T.MeshBasicMaterial({ color: HEX[color] || HEX.brace, transparent: true, opacity: 0.95, depthWrite: false }));
    for (const s of [1, -1]) {
      const b = new T.Mesh(keep(new T.BoxGeometry(5.4, 0.35, 0.9)), m);
      b.rotation.y = s * (Math.PI / 4);
      b.position.y = 0.4;
      g.add(b);
    }
    return g;
  }
  function endMark(color) {
    const g = new T.Group();
    const r = new T.Mesh(keep(new T.RingGeometry(1.7, 2.2, 40)), keep(new T.MeshBasicMaterial({ color: HEX[color] || HEX.ok, transparent: true, opacity: 0.95, side: T.DoubleSide, depthWrite: false })));
    r.rotation.x = -Math.PI / 2;
    r.position.y = 0.4;
    g.add(r);
    const dot = new T.Mesh(keep(new T.CircleGeometry(0.7, 20)), r.material);
    dot.rotation.x = -Math.PI / 2;
    dot.position.y = 0.4;
    g.add(dot);
    return g;
  }
  function syncMarks() {
    const want = new Map();
    model.trails.forEach((tr) => {
      if (tr.end && tr.pts.length) {
        const at = tr.endAt || tr.pts[tr.pts.length - 1];
        want.set(`${tr.id}:${tr.end}`, { kind: tr.end, xy: at, color: tr.end === "frozen" ? "brace" : "ok", text: tr.endText });
      }
    });
    for (const [k, g] of markers) if (!want.has(k)) (groups.marks.remove(g), markers.delete(k));
    for (const [k, w] of want) {
      let g = markers.get(k);
      if (!g) {
        g = w.kind === "frozen" ? xMark(w.color) : endMark(w.color);
        groups.marks.add(g);
        markers.set(k, g);
      }
      const [wx, wz] = tableToWorld(w.xy[0], w.xy[1]);
      g.position.set(wx, 0, wz);
      if (w.text) label(`mk:${k}`, w.text, w.color === "brace" ? "brace" : "ok", [wx, 3.5, wz]);
    }
  }

  // camera director ──────────────────────────────────────────────────────────
  const cam = { az: -0.18, el: 1.0, r: 96, tx: 0, ty: 2, tz: -1 };
  const want = { ...cam };
  const user = { az: 0, el: 0, dragging: false, last: 0, px: 0, py: 0, hover: [0, 0] };
  const el = renderer.domElement;
  el.style.touchAction = "none";
  el.addEventListener("pointerdown", (e) => {
    user.dragging = true;
    user.px = e.clientX;
    user.py = e.clientY;
    el.setPointerCapture(e.pointerId);
  });
  el.addEventListener("pointermove", (e) => {
    if (user.dragging) {
      user.az = clamp(user.az - (e.clientX - user.px) * 0.006, -1.1, 1.1);
      user.el = clamp(user.el + (e.clientY - user.py) * 0.004, -0.5, 0.55);
      user.px = e.clientX;
      user.py = e.clientY;
      user.last = performance.now();
    } else {
      const b = el.getBoundingClientRect();
      user.hover = [((e.clientX - b.left) / b.width - 0.5) * 2, ((e.clientY - b.top) / b.height - 0.5) * 2];
    }
  });
  const endDrag = () => (user.dragging = false);
  el.addEventListener("pointerup", endDrag);
  el.addEventListener("pointercancel", endDrag);
  el.addEventListener("dblclick", () => ((user.az = 0), (user.el = 0)));

  let W = 1;
  let H = 1;
  let fitR = 96;
  function resize() {
    W = Math.max(1, host.clientWidth);
    H = Math.max(1, host.clientHeight);
    renderer.setSize(W, H, false);
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    camera.aspect = W / H;
    const tv = Math.tan((camera.fov * Math.PI) / 360);
    fitR = Math.max(62, 25 / tv, 34 / (tv * camera.aspect));
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();

  const sm = makeSmooth();
  const v3 = new T.Vector3();
  let running = false;
  let remove = null;
  let time = 0;
  let lastHeldId = null;

  function frame(dt, t) {
    time = t;
    const stopped = stepSmooth(sm, model, dt);
    const g = model.grip || {};

    // director: wide while idle, follow during a carry, push in on a freeze
    const focus = sm.x != null ? tableToWorld(sm.x, sm.y) : [0, 3];
    if (stopped && g.xy) {
      want.tx = focus[0] * 0.7 + 5;
      want.tz = focus[1] * 0.7;
      want.r = fitR * 0.78;
      want.el = 0.95;
    } else if (g.state === "moving") {
      want.tx = focus[0] * 0.35 + 5;
      want.tz = -1 + focus[1] * 0.3;
      want.r = fitR * 0.93;
      want.el = 1.0;
    } else {
      want.tx = 5;
      want.tz = -1;
      want.r = fitR;
      want.el = 1.0;
    }
    const k = reduced ? 30 : 2.6;
    cam.tx = damp(cam.tx, want.tx, k, dt);
    cam.tz = damp(cam.tz, want.tz, k, dt);
    cam.r = damp(cam.r, want.r, k, dt);
    cam.el = damp(cam.el, want.el, k, dt);
    if (!user.dragging && !reduced && performance.now() - user.last > 4000) {
      user.az = damp(user.az, 0, 0.8, dt);
      user.el = damp(user.el, 0, 0.8, dt);
    }
    const par = reduced ? [0, 0] : user.hover;
    const az = cam.az + user.az + par[0] * 0.06 + (reduced ? 0 : Math.sin(t * 0.2) * 0.03);
    const e = clamp(cam.el + user.el - par[1] * 0.025, 0.25, 1.4);
    camera.position.set(cam.tx + cam.r * Math.sin(az) * Math.cos(e), cam.ty + cam.r * Math.sin(e), cam.tz + cam.r * Math.cos(az) * Math.cos(e));
    camera.lookAt(cam.tx, cam.ty, cam.tz);

    // gripper marker
    gripG.visible = sm.x != null;
    if (sm.x != null) {
      const [gx, gz] = tableToWorld(sm.x, sm.y);
      gripG.position.set(gx, 0, gz);
      const hh = sm.hh + (g.held ? 0 : -2.5);
      body.position.y = hh;
      stem.scale.y = Math.max(0.01, hh);
      stem.position.y = hh / 2;
      jawL.position.x = -(sm.jaw + 0.28);
      jawR.position.x = sm.jaw + 0.28;
      const col = new T.Color().setHex(HEX.ok).lerp(new T.Color(HEX.brace), sm.ring);
      ringMat.color.copy(col);
      glowMat.color.copy(col);
      accMat.color.copy(col);
      accMat.emissive.copy(col);
      const breathe = reduced ? 1 : stopped ? 1 + Math.sin(t * 7) * 0.12 : 1 + Math.sin(t * 2.4) * 0.05;
      ring.scale.setScalar(breathe);
      glowMat.opacity = 0.35 + sm.ring * 0.3;
      // held prop rides in the jaws
      if (g.held !== lastHeldId) {
        if (heldProp) (body.remove(heldProp.obj), (heldProp = null));
        lastHeldId = g.held || null;
        const p = props.get(g.held);
        if (p) {
          const b = buildProp(T, p.kind);
          own.push(...b.geos, ...b.mats);
          b.group.scale.setScalar(0.9);
          b.group.position.set(0, -1.3 - b.height * 0.9 + 1.1, 0);
          body.add(b.group);
          heldProp = { id: g.held, obj: b.group };
        }
      }
    }

    // props on the table
    for (const [id, p] of props) {
      const o = model.objects.find((x) => x.id === id);
      const placed = model.placed?.[id];
      const xy = placed || o?.xy;
      p.group.visible = !!xy && g.held !== id;
      if (xy) {
        const [wx, wz] = tableToWorld(xy[0], xy[1]);
        p.group.position.set(wx, 0, wz);
        label(`o:${id}`, p.name || id, "dim", [wx, p.height + 1.4, wz]);
      }
    }

    // zones: breathe, hot when the carry is near / frozen at it
    const hot = !!model.hot;
    for (const z of zoneObjs.values()) {
      const base = hot ? 0.17 : 0.06;
      z.volMat.opacity = base + (reduced ? 0 : Math.sin(t * (hot ? 5 : 1.6)) * (hot ? 0.06 : 0.015));
      z.floorMat.opacity = hot ? 0.34 : 0.2;
      z.edgeMat.opacity = hot ? 1 : 0.7;
      label(`z:${z.name}`, `avoid · ${z.name}`, "brace", z.top);
    }
    if (sm.x != null && g.stage) {
      const [gx, gz] = tableToWorld(sm.x, sm.y);
      label("grip", g.held ? "gripper estimate · holding" : "gripper estimate", stopped ? "brace" : "ok", [gx, sm.hh + 5.5, gz]);
    }

    // pulses
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i];
      p.age += dt;
      if (p.age < 0) continue;
      const u = p.age / p.life;
      if (u >= 1) {
        groups.fx.remove(p.mesh);
        p.m.dispose();
        pulses.splice(i, 1);
        continue;
      }
      const s = 1 + (p.R - 1) * (1 - (1 - u) ** 3);
      p.mesh.scale.setScalar(s);
      p.m.opacity = (1 - u) * 0.9;
    }

    syncGeometry();
    syncTrails();
    syncMarks();

    // labels
    for (const l of labels.values()) {
      v3.copy(l.pos).project(camera);
      const hide = v3.z > 1 || !l.seen;
      l.el.style.opacity = hide ? "0" : "";
      l.el.style.transform = `translate(${((v3.x * 0.5 + 0.5) * W).toFixed(1)}px, ${((-v3.y * 0.5 + 0.5) * H).toFixed(1)}px) translate(-50%, -135%)`;
    }
    dropLabels();
    renderer.render(scene, camera);
  }

  const api = {
    mode: "3d",
    set(m) {
      model = m;
    },
    pulse,
    start() {
      if (running) return;
      running = true;
      resize();
      remove = ctx.loop.add(frame);
    },
    stop() {
      running = false;
      remove?.();
      remove = null;
    },
    resize,
    dispose() {
      api.stop();
      ro.disconnect();
      for (const r of trails.values()) (r.core.dispose(), r.halo.dispose());
      own.forEach((o) => o?.dispose?.());
      renderer.dispose();
      renderer.domElement.remove();
      labelLayer.remove();
    },
  };
  return api;
}

// ═══ 2D canvas fallback ═════════════════════════════════════════════════════
function create2D(host, ctx) {
  const canvas = document.createElement("canvas");
  canvas.className = "tbl-canvas";
  host.prepend(canvas);
  const g = canvas.getContext("2d");
  let model = { zones: [], objects: [], grip: {}, trails: [], placed: {}, hot: false };
  const sm = makeSmooth();
  const pulses = [];
  let W = 1;
  let H = 1;
  let dpr = 1;
  let running = false;
  let remove = null;
  function resize() {
    dpr = Math.min(devicePixelRatio || 1, 2);
    W = Math.max(1, host.clientWidth);
    H = Math.max(1, host.clientHeight);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = "100%";
    canvas.style.height = "100%";
  }
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();

  const rect = () => {
    const s = Math.min(W * 0.9, H * 0.86);
    return { x: (W - s) / 2, y: (H - s) / 2 - 4, s };
  };
  const P = (r, xy) => [r.x + xy[0] * r.s, r.y + xy[1] * r.s];

  function frame(dt, t) {
    const stopped = stepSmooth(sm, model, dt);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, W, H);
    const r = rect();
    g.fillStyle = "rgba(20,32,48,.7)";
    g.strokeStyle = "rgba(160,190,230,.2)";
    g.lineWidth = 1;
    g.beginPath();
    g.roundRect(r.x, r.y, r.s, r.s, 10);
    g.fill();
    g.stroke();
    g.strokeStyle = "rgba(160,190,230,.08)";
    for (let i = 1; i < 10; i++) {
      const [x] = P(r, [i / 10, 0]);
      const [, y] = P(r, [0, i / 10]);
      g.beginPath();
      g.moveTo(x, r.y);
      g.lineTo(x, r.y + r.s);
      g.moveTo(r.x, y);
      g.lineTo(r.x + r.s, y);
      g.stroke();
    }
    g.font = '500 11px "JetBrains Mono", monospace';
    g.textAlign = "center";
    g.fillStyle = "#6c7a8d";
    g.fillText("user side", r.x + r.s / 2, r.y + r.s + 15);
    for (const z of model.zones) {
      const [x0, y0] = P(r, [z.box[0], z.box[1]]);
      const [x1, y1] = P(r, [z.box[2], z.box[3]]);
      g.fillStyle = `rgba(255,77,94,${model.hot ? 0.34 : 0.18})`;
      g.strokeStyle = "#ff4d5e";
      g.fillRect(x0, y0, x1 - x0, y1 - y0);
      g.strokeRect(x0, y0, x1 - x0, y1 - y0);
      g.fillStyle = "#ff4d5e";
      g.fillText(`avoid · ${z.name}`, (x0 + x1) / 2, y0 - 6);
    }
    for (const o of model.objects) {
      const xy = model.placed?.[o.id] || o.xy;
      if (!xy || model.grip?.held === o.id) continue;
      const [x, y] = P(r, xy);
      g.fillStyle = "#c9b6a0";
      g.beginPath();
      g.arc(x, y, 7, 0, 6.283);
      g.fill();
      g.fillStyle = "#aab6c6";
      g.fillText(o.name || o.id, x, y + 20);
    }
    for (const tr of model.trails) {
      const pts = tr.pts.map((p) => P(r, p));
      if (tr.active && sm.x != null) pts.push(P(r, [sm.x, sm.y]));
      if (pts.length < 2) continue;
      g.lineJoin = g.lineCap = "round";
      g.strokeStyle = CSS[tr.color] || CSS.grip;
      g.shadowColor = g.strokeStyle;
      g.shadowBlur = 10;
      g.lineWidth = 3;
      g.beginPath();
      pts.forEach((p, i) => (i ? g.lineTo(p[0], p[1]) : g.moveTo(p[0], p[1])));
      g.stroke();
      g.shadowBlur = 0;
      if (tr.end) {
        const [x, y] = P(r, tr.endAt || tr.pts[tr.pts.length - 1]);
        g.strokeStyle = tr.end === "frozen" ? CSS.brace : CSS.ok;
        g.lineWidth = 3;
        g.beginPath();
        if (tr.end === "frozen") {
          g.moveTo(x - 8, y - 8);
          g.lineTo(x + 8, y + 8);
          g.moveTo(x + 8, y - 8);
          g.lineTo(x - 8, y + 8);
        } else g.arc(x, y, 8, 0, 6.283);
        g.stroke();
        if (tr.endText) {
          g.fillStyle = g.strokeStyle;
          g.fillText(tr.endText, x, y - 14);
        }
      }
    }
    if (sm.x != null) {
      const [x, y] = P(r, [sm.x, sm.y]);
      const col = stopped ? CSS.brace : CSS.ok;
      if (model.grip.held) {
        g.fillStyle = "#c9b6a0";
        g.beginPath();
        g.arc(x, y, 7, 0, 6.283);
        g.fill();
      }
      g.strokeStyle = col;
      g.lineWidth = 2.5;
      g.beginPath();
      g.arc(x, y, 12 + (stopped ? Math.sin(t * 7) * 1.5 : 0), 0, 6.283);
      g.stroke();
      g.fillStyle = col;
      g.fillText("gripper estimate", x, y - 20);
    }
    for (let i = pulses.length - 1; i >= 0; i--) {
      const p = pulses[i];
      p.age += dt;
      if (p.age < 0) continue;
      const u = p.age / 1.4;
      if (u >= 1) {
        pulses.splice(i, 1);
        continue;
      }
      const [x, y] = P(r, p.xy);
      g.strokeStyle = `rgba(255,77,94,${(1 - u) * 0.9})`;
      g.lineWidth = 2;
      g.beginPath();
      g.arc(x, y, 6 + u * r.s * 0.16 * (1 + p.k), 0, 6.283);
      g.stroke();
    }
  }
  const api = {
    mode: "2d",
    set(m) {
      model = m;
    },
    pulse(xy) {
      pulses.push({ xy, age: 0, k: 0 }, { xy, age: -0.25, k: 0.5 });
    },
    start() {
      if (running) return;
      running = true;
      resize();
      remove = ctx.loop.add(frame);
    },
    stop() {
      running = false;
      remove?.();
      remove = null;
    },
    resize,
    dispose() {
      api.stop();
      ro.disconnect();
      canvas.remove();
    },
  };
  return api;
}
