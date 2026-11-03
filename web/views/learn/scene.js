// The table: one 3D scene (three.js) with a 2D SVG twin for browsers without WebGL.
// Both take the same data (replay model from data.js) and expose the same small API:
//   mount(host) / start() / stop() / dispose()
//   setModel(model, {cause}) / setCursor(t) / setRules({zones, paths, focus}) / setMode("incident"|"rules")
//   fireShock()
// Table units are the runtime's normalized table coordinates (x right, y away from the arm base).
import { WORLD as W, SHOULDER_H } from "./data.js";

const C = { ink: 0xeaf0f8, ink2: 0xaab6c6, brace: 0xff4d5e, grip: 0x4fd8ff, muse: 0xa58bff, ok: 0x5af0a4, steel: 0x8fa3bd };
const hex = (n) => `#${n.toString(16).padStart(6, "0")}`;

class Base {
  constructor(ctx) {
    this.ctx = ctx;
    this.el = document.createElement("div");
    this.el.className = "tbl";
    this.labelLayer = document.createElement("div");
    this.labelLayer.className = "tbl-labels";
    this.labels = [];
    this.model = null;
    this.mode = "incident";
    this.cause = null;
    this.rules = null;
    this._last = null;
    this.active = false;
  }
  mount(host) {
    host.prepend(this.el);
    this.resize();
  }
  addLabel(text, tone = "", get, anchor = "top") {
    const el = document.createElement("span");
    el.className = `tbl-l ${tone}`;
    el.textContent = text;
    this.labelLayer.append(el);
    const l = { el, get, anchor: anchor === "right" ? "translate(14px, -50%)" : "translate(-50%, -100%)" };
    this.labels.push(l);
    return l;
  }
  clearLabels() {
    this.labels.forEach((l) => l.el.remove());
    this.labels = [];
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// 3D
// ═════════════════════════════════════════════════════════════════════════════
export class Table3D extends Base {
  constructor(ctx, THREE) {
    super(ctx);
    const T = (this.T = THREE);
    this.kind = "3d";
    const r = (this.r = new T.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" }));
    r.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    r.setClearColor(0x000000, 0);
    this.el.append(r.domElement, this.labelLayer);
    this.scene = new T.Scene();
    this.cam = new T.PerspectiveCamera(34, 1, 0.1, 60);
    this.camAng = 0;
    this.camTarget = 0;
    this.pointer = 0;
    this.kick = 0;
    this.shockT = -10;
    this.freezeK = 0;
    this.zoneK = 0;
    this.v = new T.Vector3();
    this.up = new T.Vector3(0, 1, 0);

    this.scene.add(new T.AmbientLight(0xbfd0ea, 0.75));
    const sun = new T.DirectionalLight(0xffffff, 1.05);
    sun.position.set(-2.5, 6, 3.5);
    this.scene.add(sun);
    this.flash = new T.PointLight(C.brace, 0, 6, 1.6);
    this.scene.add(this.flash);

    this.buildTable();
    this.dyn = new T.Group(); // everything that depends on the data
    this.scene.add(this.dyn);
    this.fx = new T.Group();
    this.scene.add(this.fx);
    this.buildShock();

    this.ro = new ResizeObserver(() => this.resize());
    this.ro.observe(this.el);
    this.onMove = (e) => {
      const b = this.el.getBoundingClientRect();
      this.pointer = ((e.clientX - b.left) / Math.max(1, b.width) - 0.5) * 2;
    };
    this.el.addEventListener("pointermove", this.onMove);
  }

  P(x, y, h = 0, out = this.v) {
    return out.set((x - 0.5) * W, h * W, (0.5 - y) * W);
  }

  // ── static table ─────────────────────────────────────────────────────────
  buildTable() {
    const T = this.T;
    const cv = document.createElement("canvas");
    cv.width = cv.height = 1024;
    const g = cv.getContext("2d");
    const grad = g.createRadialGradient(512, 560, 40, 512, 512, 760);
    grad.addColorStop(0, "#16213a");
    grad.addColorStop(1, "#080d17");
    g.fillStyle = grad;
    g.fillRect(0, 0, 1024, 1024);
    for (let i = 0; i <= 20; i++) {
      const p = (i / 20) * 1024;
      g.strokeStyle = i % 5 === 0 ? "rgba(140,175,230,.20)" : "rgba(140,175,230,.07)";
      g.lineWidth = i % 5 === 0 ? 2 : 1;
      g.beginPath();
      g.moveTo(p, 0);
      g.lineTo(p, 1024);
      g.moveTo(0, p);
      g.lineTo(1024, p);
      g.stroke();
    }
    g.fillStyle = "rgba(170,190,225,.38)";
    g.font = "500 17px 'JetBrains Mono', monospace";
    for (let i = 0; i <= 4; i++) {
      const p = (i / 4) * 1024;
      g.fillText((i / 4).toFixed(2), Math.min(p + 6, 980), 1016);
      if (i) g.fillText((i / 4).toFixed(2), 8, 1024 - p - 6);
    }
    const tex = (this.tableTex = new T.CanvasTexture(cv));
    tex.colorSpace = T.SRGBColorSpace;
    tex.anisotropy = 8;
    const top = new T.Mesh(new T.PlaneGeometry(W, W), new T.MeshStandardMaterial({ map: tex, roughness: 0.92, metalness: 0.05 }));
    top.rotation.x = -Math.PI / 2;
    this.scene.add(top);
    const slab = new T.Mesh(new T.BoxGeometry(W + 0.1, 0.12, W + 0.1), new T.MeshStandardMaterial({ color: 0x060a12, roughness: 1 }));
    slab.position.y = -0.062;
    this.scene.add(slab);
    const edge = new T.LineLoop(
      new T.BufferGeometry().setFromPoints([new T.Vector3(-W / 2, 0.004, -W / 2), new T.Vector3(W / 2, 0.004, -W / 2), new T.Vector3(W / 2, 0.004, W / 2), new T.Vector3(-W / 2, 0.004, W / 2)]),
      new T.LineBasicMaterial({ color: C.grip, transparent: true, opacity: 0.4 }),
    );
    this.scene.add(edge);
    // soft pool of light under the table
    const gcv = document.createElement("canvas");
    gcv.width = gcv.height = 128;
    const gg = gcv.getContext("2d");
    const rg = gg.createRadialGradient(64, 64, 0, 64, 64, 64);
    rg.addColorStop(0, "rgba(79,216,255,.22)");
    rg.addColorStop(1, "rgba(79,216,255,0)");
    gg.fillStyle = rg;
    gg.fillRect(0, 0, 128, 128);
    this.glowTex = new T.CanvasTexture(gcv);
    const pool = new T.Mesh(new T.PlaneGeometry(W * 1.9, W * 1.9), new T.MeshBasicMaterial({ map: this.glowTex, transparent: true, depthWrite: false, blending: T.AdditiveBlending }));
    pool.rotation.x = -Math.PI / 2;
    pool.position.y = -0.13;
    this.scene.add(pool);

    // reusable textures
    const sc = document.createElement("canvas");
    sc.width = 64;
    sc.height = 16;
    const s = sc.getContext("2d");
    s.fillStyle = "rgba(255,77,94,0)";
    s.fillRect(0, 0, 64, 16);
    s.fillStyle = "rgba(255,77,94,1)";
    s.beginPath();
    s.moveTo(0, 16);
    s.lineTo(16, 0);
    s.lineTo(32, 0);
    s.lineTo(16, 16);
    s.closePath();
    s.fill();
    s.beginPath();
    s.moveTo(32, 16);
    s.lineTo(48, 0);
    s.lineTo(64, 0);
    s.lineTo(48, 16);
    s.closePath();
    s.fill();
    this.stripeTex = new T.CanvasTexture(sc);
    this.stripeTex.wrapS = T.RepeatWrapping;
    this.stripeTex.colorSpace = T.SRGBColorSpace;
    const hc = document.createElement("canvas");
    hc.width = hc.height = 64;
    const h = hc.getContext("2d");
    h.strokeStyle = "rgba(255,77,94,.55)";
    h.lineWidth = 5;
    for (let i = -64; i < 128; i += 18) {
      h.beginPath();
      h.moveTo(i, 64);
      h.lineTo(i + 64, 0);
      h.stroke();
    }
    this.hatchTex = new T.CanvasTexture(hc);
    this.hatchTex.wrapS = this.hatchTex.wrapT = T.RepeatWrapping;
    const dc = document.createElement("canvas");
    dc.width = dc.height = 64;
    const d = dc.getContext("2d");
    const dg = d.createRadialGradient(32, 32, 0, 32, 32, 32);
    dg.addColorStop(0, "rgba(255,255,255,.95)");
    dg.addColorStop(0.35, "rgba(255,255,255,.35)");
    dg.addColorStop(1, "rgba(255,255,255,0)");
    d.fillStyle = dg;
    d.fillRect(0, 0, 64, 64);
    this.dotTex = new T.CanvasTexture(dc);
  }

  buildShock() {
    const T = this.T;
    this.rings = [0, 1, 2].map(() => {
      const m = new T.Mesh(new T.RingGeometry(0.86, 1, 72), new T.MeshBasicMaterial({ color: C.brace, transparent: true, opacity: 0, depthWrite: false, side: T.DoubleSide, blending: T.AdditiveBlending }));
      m.rotation.x = -Math.PI / 2;
      m.visible = false;
      this.fx.add(m);
      return m;
    });
    this.beam = new T.Mesh(new T.CylinderGeometry(0.1, 0.34, 1.5, 28, 1, true), new T.MeshBasicMaterial({ color: C.brace, transparent: true, opacity: 0, depthWrite: false, side: T.DoubleSide, blending: T.AdditiveBlending }));
    this.beam.visible = false;
    this.fx.add(this.beam);
  }

  // ── lifecycle ────────────────────────────────────────────────────────────
  start() {
    if (this.active) return;
    this.active = true;
    this.stopLoop = this.ctx.loop.add((dt, t) => this.frame(dt, t));
    this.resize();
  }
  stop() {
    this.active = false;
    this.stopLoop?.();
  }
  resize() {
    const w = this.el.clientWidth;
    const h = this.el.clientHeight;
    if (!w || !h) return;
    this.r.setSize(w, h, false);
    this.r.domElement.style.width = "100%";
    this.r.domElement.style.height = "100%";
    this.cam.aspect = w / h;
    const vf = (this.cam.fov * Math.PI) / 180;
    const hf = 2 * Math.atan(Math.tan(vf / 2) * this.cam.aspect);
    const rh = this.mode === "rules" ? W * 0.56 : W * 0.57;
    const rv = this.mode === "rules" ? W * 0.34 : W * 0.4;
    this.dist = Math.max(rh / Math.tan(hf / 2), rv / Math.tan(vf / 2));
    this.cam.updateProjectionMatrix();
    this.size = [w, h];
    this.camDirty = true;
  }
  dispose() {
    this.stop();
    this.ro.disconnect();
    this.el.removeEventListener("pointermove", this.onMove);
    this.disposeGroup(this.dyn);
    this.disposeGroup(this.scene);
    [this.tableTex, this.glowTex, this.stripeTex, this.hatchTex, this.dotTex].forEach((t) => t?.dispose());
    this.r.dispose();
    this.r.forceContextLoss?.();
    this.el.remove();
  }
  disposeGroup(g) {
    g.traverse((o) => {
      o.geometry?.dispose?.();
      const ms = Array.isArray(o.material) ? o.material : o.material ? [o.material] : [];
      ms.forEach((m) => {
        m.map?.dispose?.();
        m.dispose?.();
      });
    });
  }
  clearDyn() {
    this.disposeGroup(this.dyn);
    this.dyn.clear();
    this.clearLabels();
    this.arm = this.objs = this.pathLit = this.pathDim = null;
    this.armMat = this.pillar = this.freezeRing = this.onsetMark = this.onsetLabel = this.freezeLabel = this.zoneMeshes = this.tip = this.halo = null;
    this.postFreeze = false;
    this.freezeK = 0;
  }

  // ── data ─────────────────────────────────────────────────────────────────
  setMode(mode) {
    if (this.mode === mode) return;
    this.mode = mode;
    this.resize();
    this.rebuild();
  }
  setModel(model, { cause } = {}) {
    this.model = model;
    this.cause = cause || null;
    this._last = null;
    this.rebuild();
  }
  setCause(c) {
    if (this.cause === c) return;
    this.cause = c;
    this.rebuild();
  }
  setRules(rules) {
    this.rules = rules;
    if (this.mode === "rules") this.rebuild();
  }

  rebuild() {
    this.clearDyn();
    const T = this.T;
    if (this.mode === "rules") return this.buildRules();
    const m = this.model;
    if (!m) {
      this.addLabel("arm base", "dim", () => this.P(0.5, 0.06, 0.02, new T.Vector3()));
      this.buildBaseOnly();
      return;
    }
    this.buildZones(m.zones, (z) => z.name === this.cause || m.zones.length === 1);
    this.buildObjects(m);
    if (m.empty) return;
    this.buildPath(m);
    this.buildArm(m);
    this.buildMarks(m);
    this.setCursor(this.cursor ?? m.range[0], true);
  }

  buildBaseOnly() {
    const T = this.T;
    const base = new T.Mesh(new T.CylinderGeometry(0.16, 0.2, 0.1, 32), new T.MeshStandardMaterial({ color: 0x1a2436, roughness: 0.6, metalness: 0.4, emissive: 0x0a1626 }));
    this.P(0.5, 0.06, 0.016, base.position);
    this.dyn.add(base);
  }

  buildZones(zones, isCause) {
    const T = this.T;
    this.zoneMeshes = zones.map((z) => {
      const [x0, y0, x1, y1] = z.box;
      const w = (x1 - x0) * W;
      const d = (y1 - y0) * W;
      const hgt = 0.34;
      const grp = new T.Group();
      this.P((x0 + x1) / 2, (y0 + y1) / 2, 0, grp.position);
      const fill = new T.Mesh(new T.BoxGeometry(w, hgt, d), new T.MeshBasicMaterial({ color: C.brace, transparent: true, opacity: 0.1, depthWrite: false }));
      fill.position.y = hgt / 2;
      const edges = new T.LineSegments(new T.EdgesGeometry(new T.BoxGeometry(w, hgt, d)), new T.LineBasicMaterial({ color: C.brace, transparent: true, opacity: 0.7 }));
      edges.position.y = hgt / 2;
      const hatch = this.hatchTex.clone();
      hatch.needsUpdate = true;
      hatch.repeat.set(Math.max(1, w * 3), Math.max(1, d * 3));
      const floor = new T.Mesh(new T.PlaneGeometry(w, d), new T.MeshBasicMaterial({ map: hatch, transparent: true, opacity: 0.8, depthWrite: false }));
      floor.rotation.x = -Math.PI / 2;
      floor.position.y = 0.006;
      grp.add(floor, fill, edges);
      this.dyn.add(grp);
      const cause = isCause(z);
      this.addLabel(`${z.name} · avoid zone`, "brace", () => this.P((x0 + x1) / 2, (y0 + y1) / 2, 0.36 / W + 0.015, new T.Vector3()));
      return { z, fill, edges, floor, cause, grp };
    });
  }

  buildObjects(m) {
    const T = this.T;
    this.objs = new Map();
    m.objects.forEach((o) => {
      const held = o.id === m.pickId;
      const mat = new T.MeshStandardMaterial({ color: held ? 0xe9eef7 : 0x93a3ba, roughness: 0.5, metalness: 0.1, emissive: held ? 0x1a2a3c : 0x0b1320 });
      let mesh;
      if (/ball/.test(o.id)) mesh = new T.Mesh(new T.SphereGeometry(0.1, 24, 16), mat);
      else if (/block|box|cube/.test(o.id)) mesh = new T.Mesh(new T.BoxGeometry(0.17, 0.17, 0.17), mat);
      else mesh = new T.Mesh(new T.CylinderGeometry(0.095, 0.075, 0.2, 28), mat);
      const g = new T.Group();
      mesh.position.y = /ball/.test(o.id) ? 0.1 : /block|box|cube/.test(o.id) ? 0.085 : 0.1;
      g.add(mesh);
      if (held) {
        const ring = new T.Mesh(new T.RingGeometry(0.17, 0.2, 40), new T.MeshBasicMaterial({ color: C.ink, transparent: true, opacity: 0.55, side: T.DoubleSide, depthWrite: false }));
        ring.rotation.x = -Math.PI / 2;
        ring.position.y = 0.008;
        g.add(ring);
      }
      this.P(o.x, o.y, 0, g.position);
      this.dyn.add(g);
      const label = this.addLabel(o.name, held ? "held" : "dim", () => this.P(0, 0, 0, new T.Vector3()).copy(g.position).setY(g.position.y + 0.32));
      this.objs.set(o.id, { g, o, label, base: g.position.clone() });
    });
  }

  tube(pts, radius, mat) {
    const T = this.T;
    const v = pts.map((p) => this.P(p.x, p.y, p.h, new T.Vector3()));
    const curve = new T.CatmullRomCurve3(v, false, "centripetal");
    const segs = Math.max(24, Math.min(400, pts.length * 5));
    const geo = new T.TubeGeometry(curve, segs, radius, 8, false);
    geo.userData = { segs, radial: 8 };
    return new T.Mesh(geo, mat);
  }

  buildPath(m) {
    const T = this.T;
    if (m.pts.length < 2) return;
    this.pathDim = this.tube(m.pts, 0.014, new T.MeshBasicMaterial({ color: 0x8aa0bf, transparent: true, opacity: 0.32, depthWrite: false }));
    this.pathLit = this.tube(m.pts, 0.022, new T.MeshBasicMaterial({ color: C.ink }));
    this.dyn.add(this.pathDim, this.pathLit);
  }

  buildArm(m) {
    const T = this.T;
    const steel = new T.MeshStandardMaterial({ color: C.steel, roughness: 0.4, metalness: 0.55, emissive: 0x101a2a });
    this.armMat = steel;
    const grp = new T.Group();
    const base = new T.Mesh(new T.CylinderGeometry(0.16, 0.2, 0.1, 32), new T.MeshStandardMaterial({ color: 0x1a2436, roughness: 0.6, metalness: 0.4, emissive: 0x0a1626 }));
    this.P(m.base.x, m.base.y, 0.016, base.position);
    grp.add(base);
    const link = (r) => {
      const l = new T.Mesh(new T.CylinderGeometry(r, r, 1, 14), steel);
      grp.add(l);
      return l;
    };
    const joint = (r) => {
      const j = new T.Mesh(new T.SphereGeometry(r, 18, 12), steel);
      grp.add(j);
      return j;
    };
    this.arm = { grp, l1: link(0.036), l2: link(0.03), j0: joint(0.06), j1: joint(0.05), j2: joint(0.042) };
    // the tip marker: a bright core with an additive halo
    this.tip = new T.Mesh(new T.SphereGeometry(0.052, 20, 14), new T.MeshBasicMaterial({ color: 0xffffff }));
    this.halo = new T.Sprite(new T.SpriteMaterial({ map: this.dotTex, color: C.grip, transparent: true, depthWrite: false, blending: T.AdditiveBlending, opacity: 0.9 }));
    this.halo.scale.setScalar(0.62);
    const dropGeo = new T.BufferGeometry().setFromPoints([new T.Vector3(), new T.Vector3(0, -1, 0)]);
    this.drop = new T.Line(dropGeo, new T.LineBasicMaterial({ color: C.ink, transparent: true, opacity: 0.35 }));
    this.shadow = new T.Mesh(new T.RingGeometry(0.06, 0.085, 32), new T.MeshBasicMaterial({ color: C.ink, transparent: true, opacity: 0.5, side: T.DoubleSide, depthWrite: false }));
    this.shadow.rotation.x = -Math.PI / 2;
    grp.add(this.tip, this.halo, this.drop, this.shadow);
    this.dyn.add(grp);
    const bp = new T.Vector3();
    this.P(m.base.x, m.base.y, 0, bp);
    this.addLabel("arm base", "dim", () => this.P(m.base.x, m.base.y, 0.1, new T.Vector3()));
  }

  buildMarks(m) {
    const T = this.T;
    // freeze pillar + onset diamond: where the arm was when you braced
    const fp = m.freezePose;
    this.pillar = new T.Mesh(new T.CylinderGeometry(0.008, 0.008, 1, 8), new T.MeshBasicMaterial({ color: C.brace, transparent: true, opacity: 0.55, depthWrite: false }));
    const top = this.P(fp.x, fp.y, fp.h + 0.05, new T.Vector3());
    this.pillar.scale.y = top.y;
    this.pillar.position.set(top.x, top.y / 2, top.z);
    this.freezeRing = new T.Mesh(new T.RingGeometry(0.12, 0.145, 48), new T.MeshBasicMaterial({ color: C.brace, transparent: true, opacity: 0.8, side: T.DoubleSide, depthWrite: false }));
    this.freezeRing.rotation.x = -Math.PI / 2;
    this.P(fp.x, fp.y, 0.002, this.freezeRing.position);
    this.dyn.add(this.pillar, this.freezeRing);
    this.freezeLabel = this.addLabel("freeze", "brace", () => this.P(fp.x, fp.y, fp.h + 0.09, new T.Vector3()));
    if (m.onsetPose) {
      const o = m.onsetPose;
      this.onsetMark = new T.Mesh(new T.OctahedronGeometry(0.06), new T.MeshBasicMaterial({ color: C.brace }));
      this.P(o.x, o.y, o.h, this.onsetMark.position);
      this.dyn.add(this.onsetMark);
      this.onsetLabel = this.addLabel("brace onset", "brace", () => this.P(o.x, o.y, o.h, new T.Vector3()), "right");
    }
    this.shockAt = new T.Vector3();
    this.P(fp.x, fp.y, 0.01, this.shockAt);
  }

  buildRules() {
    const T = this.T;
    const R = this.rules;
    this.buildBaseOnly();
    if (!R) return;
    const zones = R.zones || [];
    this.buildZones(zones, (z) => R.focusZones?.includes(z.name));
    (R.paths || []).forEach((p) => {
      if (!p.model || p.model.pts.length < 2) return;
      const focus = p.focus;
      const tex = this.stripeTex.clone();
      tex.needsUpdate = true;
      tex.repeat.set(Math.max(2, p.model.length * 26), 1);
      const striped = new T.MeshBasicMaterial({ map: tex, transparent: true, opacity: focus ? 1 : 0.45, depthWrite: false, color: 0xffffff });
      const base = this.tube(p.model.pts, 0.03, new T.MeshBasicMaterial({ color: 0x8aa0bf, transparent: true, opacity: focus ? 0.55 : 0.22, depthWrite: false }));
      const stripe = this.tube(p.model.pts, 0.034, striped);
      this.dyn.add(base, stripe);
      if (focus) {
        const fp = p.model.freezePose;
        const ring = new T.Mesh(new T.RingGeometry(0.1, 0.13, 40), new T.MeshBasicMaterial({ color: C.brace, side: T.DoubleSide, transparent: true, opacity: 0.9, depthWrite: false }));
        ring.rotation.x = -Math.PI / 2;
        this.P(fp.x, fp.y, 0.004, ring.position);
        this.dyn.add(ring);
        this.addLabel("blocked path · where you braced", "brace", () => this.P(fp.x, fp.y, 0.12, new T.Vector3()));
      }
      // faint object ghosts so the table reads the same as in the replay
    });
  }

  // ── per-frame ────────────────────────────────────────────────────────────
  setCursor(t, force = false) {
    this.cursor = t;
    const m = this.model;
    if (this.mode !== "incident" || !m || m.empty || !this.arm) return;
    const T = this.T;
    if (this._last != null && this._last < 0 && t >= 0 && t - this._last < 0.6) this.fireShock();
    this._last = t;
    const p = m.poseAt(t);
    this.pose = p;
    const B = this.P(m.base.x, m.base.y, 0.0, new T.Vector3());
    const S = this.P(m.base.x, m.base.y, m.scale * SHOULDER_H, new T.Vector3());
    const E = this.P(p.ex, p.ey, p.eh, new T.Vector3());
    const Wr = this.P(p.x, p.y, p.h, new T.Vector3());
    const setLink = (mesh, a, b) => {
      const d = this.v.copy(b).sub(a);
      const len = d.length() || 1e-4;
      mesh.position.copy(a).addScaledVector(d, 0.5);
      mesh.quaternion.setFromUnitVectors(this.up, d.divideScalar(len));
      mesh.scale.set(1, len, 1);
    };
    setLink(this.arm.l1, S, E);
    setLink(this.arm.l2, E, Wr);
    this.arm.j0.position.copy(S);
    this.arm.j1.position.copy(E);
    this.arm.j2.position.copy(Wr);
    this.tip.position.copy(Wr);
    this.halo.position.copy(Wr);
    const gp = this.drop.geometry.attributes.position;
    gp.setXYZ(0, Wr.x, Wr.y, Wr.z);
    gp.setXYZ(1, Wr.x, 0.004, Wr.z);
    gp.needsUpdate = true;
    this.shadow.position.set(Wr.x, 0.006, Wr.z);
    if (this.pathLit) {
      const g = this.pathLit.geometry;
      const frac = m.length ? Math.min(1, p.L / m.length) : 0;
      g.setDrawRange(0, Math.floor(frac * g.userData.segs) * g.userData.radial * 6);
    }
    // the held object rides the gripper
    const o = m.pickId && this.objs?.get(m.pickId);
    if (o) {
      if (p.holding) o.g.position.set(Wr.x, Math.max(0, Wr.y - 0.2), Wr.z);
      else o.g.position.copy(o.base);
    }
    this.postFreeze = t >= 0;
    this.holding = p.holding;
    // the onset marker and the freeze label appear when the replay reaches them
    const onsetOn = m.onset != null && t >= m.onset;
    if (this.onsetMark) this.onsetMark.visible = onsetOn;
    if (this.onsetLabel) this.onsetLabel.el.style.display = onsetOn ? "" : "none";
    if (this.freezeLabel) this.freezeLabel.el.style.display = t >= 0 ? "" : "none";
    if (o) o.label.el.style.display = p.holding ? "none" : "";
  }

  fireShock() {
    if (!this.model || this.model.empty) return;
    this.shockT = performance.now() / 1000;
    if (!this.ctx.reduced) this.kick = 1;
    if (this.shockAt) {
      this.rings.forEach((r) => r.position.copy(this.shockAt));
      this.beam.position.set(this.shockAt.x, 0.75, this.shockAt.z);
    }
  }

  frame(dt, tt) {
    if (!this.size) return;
    const T = this.T;
    const reduced = this.ctx.reduced;
    const now = performance.now() / 1000;
    // camera: slow sway + pointer parallax + shock kick, all damped
    const target = reduced ? 0 : Math.sin(tt * 0.16) * 0.1 + this.pointer * 0.16;
    this.camAng += (target - this.camAng) * (1 - Math.exp(-3 * dt));
    this.kick = Math.max(0, this.kick - dt * 2.4);
    const d = this.dist;
    const a = this.camAng;
    const k = this.kick * this.kick;
    const elev = this.mode === "rules" ? 1.15 : 0.76;
    this.cam.position.set(Math.sin(a) * d * 0.62 + Math.sin(tt * 90) * 0.015 * k, d * elev + Math.cos(tt * 77) * 0.02 * k, Math.cos(a) * d * 0.66);
    this.cam.lookAt(0, this.mode === "rules" ? 0.05 : 0.22, -0.03 * W);

    // shock rings
    const u0 = now - this.shockT;
    this.rings.forEach((r, i) => {
      const u = (u0 - i * 0.2) / 1.6;
      r.visible = u > 0 && u < 1;
      if (r.visible) {
        r.scale.setScalar(0.12 + u * 2.2);
        r.material.opacity = (1 - u) ** 1.6 * 0.95;
      }
    });
    this.beam.visible = u0 < 0.9;
    this.beam.material.opacity = Math.max(0, 1 - u0 / 0.9) ** 2 * 0.5;
    this.flash.intensity = Math.max(0, 1 - u0 / 0.7) ** 2 * 7;
    if (this.shockAt) this.flash.position.set(this.shockAt.x, 0.7, this.shockAt.z);

    // freeze tint on the arm, zone pulse, marker pulse
    this.freezeK += ((this.postFreeze ? 1 : 0) - this.freezeK) * (1 - Math.exp(-6 * dt));
    if (this.armMat) {
      this.armMat.emissive.setRGB(0.06 + 0.5 * this.freezeK, 0.1 * (1 - this.freezeK) + 0.02, 0.16 * (1 - this.freezeK) + 0.03);
      this.halo.material.color.setHex(this.freezeK > 0.5 ? C.brace : C.grip);
      this.halo.scale.setScalar(0.62 + (reduced ? 0 : Math.sin(tt * 4) * 0.05) + this.freezeK * 0.2);
      this.tip.material.color.setHex(this.freezeK > 0.5 ? 0xffd0d4 : 0xffffff);
      this.pillar.material.opacity = 0.18 + 0.5 * this.freezeK;
      this.freezeRing.material.opacity = 0.25 + 0.65 * this.freezeK;
      if (this.onsetMark) {
        this.onsetMark.rotation.y += dt * 1.4;
        this.onsetMark.material.opacity = 1;
      }
    }
    this.zoneMeshes?.forEach((zm, i) => {
      const pulse = reduced ? 0 : (Math.sin(tt * 2.2 + i) + 1) * 0.5;
      const hot = zm.cause ? 1 : 0.5;
      const near = this.mode === "incident" ? 0.6 + 0.4 * this.freezeK : 1;
      zm.fill.material.opacity = (0.07 + 0.1 * pulse * hot) * near + (zm.cause ? 0.05 : 0);
      zm.edges.material.opacity = (0.5 + 0.35 * pulse * hot) * (zm.cause ? 1 : 0.7);
    });

    // labels
    const [w, h] = this.size;
    const v = this.v;
    for (const l of this.labels) {
      if (!l.get) continue;
      v.copy(l.get()).project(this.cam);
      const vis = v.z < 1;
      l.el.style.transform = `translate(${((v.x * 0.5 + 0.5) * w).toFixed(1)}px, ${((-v.y * 0.5 + 0.5) * h).toFixed(1)}px) ${l.anchor}`;
      l.el.style.visibility = vis ? "" : "hidden";
    }
    this.r.render(this.scene, this.cam);
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// 2D fallback (SVG) — same API
// ═════════════════════════════════════════════════════════════════════════════
const NS = "http://www.w3.org/2000/svg";
const X = (x) => 40 + x * 920;
const Y = (y) => 40 + (1 - y) * 920;

export class Table2D extends Base {
  constructor(ctx) {
    super(ctx);
    this.kind = "2d";
    this.svg = document.createElementNS(NS, "svg");
    this.svg.setAttribute("viewBox", "0 0 1000 1000");
    this.svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
    this.svg.classList.add("tbl-svg");
    this.el.append(this.svg);
    this.draw();
  }
  start() {
    this.active = true;
  }
  stop() {
    this.active = false;
  }
  resize() {}
  dispose() {
    this.el.remove();
  }
  setMode(mode) {
    if (this.mode === mode) return;
    this.mode = mode;
    this.draw();
  }
  setModel(model, { cause } = {}) {
    this.model = model;
    this.cause = cause || null;
    this._last = null;
    this.draw();
    if (model && !model.empty) this.setCursor(this.cursor ?? model.range[0]);
  }
  setCause(c) {
    this.cause = c;
    this.draw();
  }
  setRules(r) {
    this.rules = r;
    if (this.mode === "rules") this.draw();
  }
  html(s) {
    this.svg.innerHTML = s;
  }
  draw() {
    const grid = Array.from({ length: 21 }, (_, i) => {
      const p = 40 + (i / 20) * 920;
      const c = i % 5 === 0 ? "rgba(140,175,230,.2)" : "rgba(140,175,230,.07)";
      return `<path d="M${p} 40V960M40 ${p}H960" stroke="${c}" stroke-width="${i % 5 === 0 ? 2 : 1}"/>`;
    }).join("");
    let body = "";
    const zoneSvg = (zs, cause) =>
      zs
        .map((z) => {
          const [x0, y0, x1, y1] = z.box;
          const hot = cause(z);
          return `<g><rect x="${X(x0)}" y="${Y(y1)}" width="${(x1 - x0) * 920}" height="${(y1 - y0) * 920}" fill="url(#hatch)" stroke="#ff4d5e" stroke-width="3" opacity="${hot ? 1 : 0.7}" class="${hot ? "z-pulse" : ""}"/><text x="${X((x0 + x1) / 2)}" y="${Y(y1) - 12}" text-anchor="middle" class="svg-l brace">${z.name} · avoid zone</text></g>`;
        })
        .join("");
    const baseSvg = (b) => `<circle cx="${X(b.x)}" cy="${Y(b.y)}" r="26" fill="#1a2436" stroke="rgba(160,190,230,.5)" stroke-width="3"/><text x="${X(b.x)}" y="${Y(b.y) + 56}" text-anchor="middle" class="svg-l dim">arm base</text>`;
    if (this.mode === "rules") {
      const R = this.rules || {};
      body += zoneSvg(R.zones || [], (z) => R.focusZones?.includes(z.name));
      (R.paths || []).forEach((p) => {
        if (!p.model || p.model.pts.length < 2) return;
        const d = p.model.pts.map((q, i) => `${i ? "L" : "M"}${X(q.x).toFixed(1)} ${Y(q.y).toFixed(1)}`).join("");
        body += `<path d="${d}" fill="none" stroke="#8aa0bf" stroke-width="10" opacity="${p.focus ? 0.55 : 0.2}" stroke-linecap="round"/><path d="${d}" fill="none" stroke="#ff4d5e" stroke-width="10" stroke-dasharray="14 18" opacity="${p.focus ? 1 : 0.45}" stroke-linecap="butt"/>`;
        if (p.focus) {
          const fp = p.model.freezePose;
          body += `<circle cx="${X(fp.x)}" cy="${Y(fp.y)}" r="22" fill="none" stroke="#ff4d5e" stroke-width="4"/><text x="${X(fp.x)}" y="${Y(fp.y) - 32}" text-anchor="middle" class="svg-l brace">blocked path · where you braced</text>`;
        }
      });
      this.html(this.defs() + `<rect x="40" y="40" width="920" height="920" rx="14" fill="#0b111b" stroke="rgba(79,216,255,.4)" stroke-width="2"/>` + grid + body);
      return;
    }
    const m = this.model;
    if (!m) {
      this.html(this.defs() + `<rect x="40" y="40" width="920" height="920" rx="14" fill="#0b111b" stroke="rgba(79,216,255,.4)" stroke-width="2"/>` + grid + baseSvg({ x: 0.5, y: 0.06 }));
      return;
    }
    body += zoneSvg(m.zones, (z) => z.name === this.cause || m.zones.length === 1);
    body += m.objects.map((o) => `<g class="obj" data-id="${o.id}"><circle cx="${X(o.x)}" cy="${Y(o.y)}" r="22" fill="${o.id === m.pickId ? "#e9eef7" : "#93a3ba"}" opacity=".9"/><text x="${X(o.x)}" y="${Y(o.y) + 46}" text-anchor="middle" class="svg-l ${o.id === m.pickId ? "held" : "dim"}">${o.name}</text></g>`).join("");
    if (!m.empty) {
      const d = m.pts.map((q, i) => `${i ? "L" : "M"}${X(q.x).toFixed(1)} ${Y(q.y).toFixed(1)}`).join("");
      body += baseSvg(m.base);
      body += `<path d="${d}" fill="none" stroke="#8aa0bf" stroke-width="8" opacity=".32" stroke-linecap="round" stroke-linejoin="round"/><path class="lit" d="" fill="none" stroke="#eaf0f8" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>`;
      const fp = m.freezePose;
      body += `<circle cx="${X(fp.x)}" cy="${Y(fp.y)}" r="22" fill="none" stroke="#ff4d5e" stroke-width="4" opacity=".8"/><text x="${X(fp.x)}" y="${Y(fp.y) - 34}" text-anchor="middle" class="svg-l brace">freeze</text>`;
      if (m.onsetPose) body += `<path d="M${X(m.onsetPose.x)} ${Y(m.onsetPose.y) - 14}l14 14l-14 14l-14 -14z" fill="#ff4d5e"/><text x="${X(m.onsetPose.x) + 24}" y="${Y(m.onsetPose.y) + 8}" class="svg-l brace">brace onset</text>`;
      body += `<g class="shock"><circle class="sr" cx="${X(fp.x)}" cy="${Y(fp.y)}" r="0" fill="none" stroke="#ff4d5e" stroke-width="6" opacity="0"/><circle class="sr s2" cx="${X(fp.x)}" cy="${Y(fp.y)}" r="0" fill="none" stroke="#ff4d5e" stroke-width="4" opacity="0"/></g>`;
      body += `<g class="armg"><path class="l1" d="" stroke="#8fa3bd" stroke-width="12" stroke-linecap="round"/><path class="l2" d="" stroke="#8fa3bd" stroke-width="10" stroke-linecap="round"/><circle class="tip" r="14" fill="#fff"/></g>`;
    }
    this.html(this.defs() + `<rect x="40" y="40" width="920" height="920" rx="14" fill="#0b111b" stroke="rgba(79,216,255,.4)" stroke-width="2"/>` + grid + body);
    this.q = (s) => this.svg.querySelector(s);
  }
  defs() {
    return `<defs><pattern id="hatch" width="22" height="22" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="22" height="22" fill="rgba(255,77,94,.08)"/><path d="M0 0V22" stroke="rgba(255,77,94,.5)" stroke-width="5"/></pattern></defs>`;
  }
  setCursor(t) {
    this.cursor = t;
    const m = this.model;
    if (this.mode !== "incident" || !m || m.empty || !this.q) return;
    if (this._last != null && this._last < 0 && t >= 0 && t - this._last < 0.6) this.fireShock();
    this._last = t;
    const p = m.poseAt(t);
    const lit = m.samples.filter((s) => s.L <= p.L).map((s) => `${X(s.x).toFixed(1)} ${Y(s.y).toFixed(1)}`);
    lit.push(`${X(p.x).toFixed(1)} ${Y(p.y).toFixed(1)}`);
    this.q(".lit")?.setAttribute("d", `M${lit.join("L")}`);
    this.q(".l1")?.setAttribute("d", `M${X(m.base.x)} ${Y(m.base.y)}L${X(p.ex)} ${Y(p.ey)}`);
    this.q(".l2")?.setAttribute("d", `M${X(p.ex)} ${Y(p.ey)}L${X(p.x)} ${Y(p.y)}`);
    const tip = this.q(".tip");
    tip?.setAttribute("cx", X(p.x));
    tip?.setAttribute("cy", Y(p.y));
    tip?.setAttribute("fill", t >= 0 ? "#ff9aa4" : "#fff");
    const o = m.pickId && this.svg.querySelector(`.obj[data-id="${m.pickId}"] circle`);
    if (o) {
      const base = m.objects.find((x) => x.id === m.pickId);
      o.setAttribute("cx", X(p.holding ? p.x : base.x));
      o.setAttribute("cy", Y(p.holding ? p.y : base.y));
    }
  }
  fireShock() {
    if (this.ctx.reduced) return;
    this.svg.querySelectorAll(".sr").forEach((c) => {
      c.animate([{ r: 10, opacity: 0.95 }, { r: 260, opacity: 0 }], { duration: 1400, easing: "cubic-bezier(.16,1,.3,1)", delay: c.classList.contains("s2") ? 200 : 0 });
    });
  }
}

export async function createTable(ctx) {
  let T = null;
  if (!/[?&]flat\b/.test(location.search)) {
    try {
      T = await ctx.loadThree();
    } catch {
      T = null;
    }
  }
  if (T) {
    try {
      return new Table3D(ctx, T);
    } catch (err) {
      console.warn("learn: WebGL scene failed, using SVG", err);
    }
  }
  return new Table2D(ctx);
}
