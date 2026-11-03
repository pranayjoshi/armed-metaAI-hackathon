// The Stage digital twin: a three.js scene driven only by data the runtime publishes.
import { buildArm, armSkins, disposeSkins, rbox } from "./arm3d.js";
import { BASE, DIM, fk, tableToWorld } from "./kin.js";
import { buildProp, dashedRing, fadeTexture, glowTexture, matTexture, propKind } from "./props.js";

const COL = { grip: 0x4fd8ff, ok: 0x5af0a4, warn: 0xffc15a, brace: 0xff4d5e, muse: 0xa58bff };
const TRAIL_N = 150, TRAIL_LIFE = 2.6;
const PRESETS = {
  angled: { az: -0.42, el: 0.58, r: 100, t: [0, 6, -2] },
  top: { az: 0, el: 1.5, r: 112, t: [0, 6, -4] },
  follow: { az: -0.62, el: 0.5, r: 58, t: null },
};
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const damp = (a, b, l, dt) => a + (b - a) * (1 - Math.exp(-l * dt));

export function createTwin3D(T, host, { reduced = false, onPreset = () => {} } = {}) {
  const renderer = new T.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.outputColorSpace = T.SRGBColorSpace;
  renderer.toneMapping = T.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = T.PCFSoftShadowMap;
  renderer.domElement.className = "tw-canvas";
  host.prepend(renderer.domElement);
  const labelLayer = host.querySelector(".tw-labels");

  const scene = new T.Scene();
  const camera = new T.PerspectiveCamera(36, 1, 1, 1200);
  const disposables = [];
  const own = (o) => (disposables.push(o), o);

  // ── light ───────────────────────────────────────────────────────────────
  scene.add(new T.HemisphereLight(0x9bb8ff, 0x0a0f18, 0.7));
  const key = new T.DirectionalLight(0xdbe8ff, 2.2);
  key.position.set(-46, 90, 52);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  Object.assign(key.shadow.camera, { left: -60, right: 60, top: 60, bottom: -60, near: 10, far: 260 });
  key.shadow.bias = -0.0006;
  key.shadow.radius = 4;
  scene.add(key);
  const rim = new T.DirectionalLight(COL.grip, 0.9);
  rim.position.set(30, 14, -90);
  scene.add(rim);
  const warmFill = new T.DirectionalLight(0x6a5cff, 0.5);
  warmFill.position.set(70, 20, 60);
  scene.add(warmFill);
  const tipLight = new T.PointLight(COL.grip, 0, 46, 1.8);
  scene.add(tipLight);

  // ── table ───────────────────────────────────────────────────────────────
  const table = new T.Mesh(own(rbox(T, 124, 92, 3, 6)), own(new T.MeshStandardMaterial({ color: 0x080d15, roughness: 0.7, metalness: 0.15 })));
  table.rotation.x = -Math.PI / 2;
  table.position.y = -1.5;
  table.receiveShadow = true;
  scene.add(table);
  const mat = new T.Mesh(own(new T.PlaneGeometry(DIM.S, DIM.S)), own(new T.MeshStandardMaterial({ map: own(matTexture(T)), transparent: true, roughness: 0.9, metalness: 0, emissive: 0x16304a, emissiveMap: null, emissiveIntensity: 0.25 })));
  mat.rotation.x = -Math.PI / 2;
  mat.position.y = 0.03;
  mat.receiveShadow = true;
  scene.add(mat);
  const edgeGlow = new T.Mesh(own(new T.RingGeometry(DIM.S * 0.74, DIM.S * 0.745, 4, 1, Math.PI / 4, Math.PI * 2)), own(new T.MeshBasicMaterial({ color: COL.grip, transparent: true, opacity: 0.0 })));
  edgeGlow.rotation.x = -Math.PI / 2;
  scene.add(edgeGlow);
  // pan heading on the table: where the arm is pointing, from the live pan angle
  const strip = (color, op) => {
    const group = new T.Group();
    const m = new T.Mesh(own(new T.PlaneGeometry(1, 1)), own(new T.MeshBasicMaterial({ map: own(fadeTexture(T)), color, transparent: true, opacity: op, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide })));
    m.rotation.x = -Math.PI / 2;
    group.add(m);
    group.position.y = 0.08;
    scene.add(group);
    // lays the strip from (x0,z0) toward (x1,z1); alpha is strongest at the start
    group.set = (x0, z0, x1, z1, width) => {
      const dx = x1 - x0, dz = z1 - z0, len = Math.hypot(dx, dz) || 0.01;
      group.position.x = x0; group.position.z = z0;
      group.rotation.y = Math.atan2(-dx, -dz);
      m.scale.set(width, len, 1);
      m.position.z = -len / 2;
    };
    group.mat = m.material;
    return group;
  };
  const heading = strip(COL.grip, 0.32);

  // ── the arm (+ red ghost) ───────────────────────────────────────────────
  const skins = armSkins(T, "real");
  const arm = buildArm(T, skins);
  arm.root.position.set(BASE.x, 0, BASE.z);
  scene.add(arm.root);
  const gskins = armSkins(T, "ghost");
  const ghost = buildArm(T, gskins);
  ghost.root.position.copy(arm.root.position);
  ghost.root.scale.setScalar(1.025);
  ghost.root.visible = false;
  scene.add(ghost.root);

  // gripper glow
  const glowTex = own(glowTexture(T));
  const gripGlow = new T.Sprite(own(new T.SpriteMaterial({ map: glowTex, color: COL.grip, transparent: true, opacity: 0, blending: T.AdditiveBlending, depthWrite: false })));
  scene.add(gripGlow);
  // stop marker: drop line + table ring at the frozen gripper
  const stopLine = new T.Mesh(own(new T.CylinderGeometry(0.12, 0.12, 1, 8)), own(new T.MeshBasicMaterial({ color: COL.brace, transparent: true, opacity: 0.8, blending: T.AdditiveBlending, depthWrite: false })));
  stopLine.visible = false;
  scene.add(stopLine);
  const stopRing = new T.Mesh(own(new T.RingGeometry(3.6, 4.0, 48)), own(new T.MeshBasicMaterial({ color: COL.brace, transparent: true, opacity: 0.9, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide })));
  stopRing.rotation.x = -Math.PI / 2;
  stopRing.position.y = 0.12;
  stopRing.visible = false;
  scene.add(stopRing);

  // motion trail
  const trailPos = new Float32Array(TRAIL_N * 3), trailAge = new Float32Array(TRAIL_N), trailSize = new Float32Array(TRAIL_N);
  const trailGeo = own(new T.BufferGeometry());
  trailGeo.setAttribute("position", new T.BufferAttribute(trailPos, 3));
  trailGeo.setAttribute("aAge", new T.BufferAttribute(trailAge, 1));
  trailGeo.setAttribute("aSize", new T.BufferAttribute(trailSize, 1));
  const trailMat = own(new T.ShaderMaterial({
    uniforms: { uColor: { value: new T.Color(COL.grip) }, uPx: { value: renderer.getPixelRatio() } },
    vertexShader: "attribute float aAge; attribute float aSize; varying float vA; uniform float uPx; void main(){ vA=aAge; vec4 mv=modelViewMatrix*vec4(position,1.); gl_Position=projectionMatrix*mv; gl_PointSize=aSize*uPx*(300.0/-mv.z); }",
    fragmentShader: "uniform vec3 uColor; varying float vA; void main(){ float d=length(gl_PointCoord-.5); float a=smoothstep(.5,.0,d); gl_FragColor=vec4(uColor, a*a*vA*.85); }",
    transparent: true, blending: T.AdditiveBlending, depthWrite: false,
  }));
  const trail = new T.Points(trailGeo, trailMat);
  trail.frustumCulled = false;
  scene.add(trail);
  const trailBirth = new Float32Array(TRAIL_N).fill(-1e9);
  let trailHead = 0, trailLast = null;

  // ── target ring / beam, candidate halos, pointing ray ───────────────────
  const fade = own(fadeTexture(T));
  const targetGroup = new T.Group();
  targetGroup.visible = false;
  const ringMat = own(new T.MeshBasicMaterial({ color: COL.grip, transparent: true, opacity: 0.95, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide }));
  const ring1 = new T.Mesh(own(new T.RingGeometry(5.5, 5.95, 64)), ringMat);
  const ring2 = new T.Mesh(own(new T.RingGeometry(5.5, 5.8, 64)), own(ringMat.clone()));
  const beam = new T.Mesh(own(new T.CylinderGeometry(5.3, 5.3, 26, 40, 1, true)), own(new T.MeshBasicMaterial({ map: fade, color: COL.grip, transparent: true, opacity: 0.34, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide })));
  beam.position.y = 13;
  ring1.rotation.x = ring2.rotation.x = -Math.PI / 2;
  ring1.position.y = ring2.position.y = 0.14;
  targetGroup.add(ring1, ring2, beam);
  scene.add(targetGroup);

  const haloGeo = own(dashedRing(T, 6.3, 0.34, 16, 0.55));
  const haloMat = own(new T.MeshBasicMaterial({ color: COL.warn, transparent: true, opacity: 0.95, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide }));
  const halos = new Map(); // id -> mesh

  const rayMesh = strip(COL.muse, 0.8);
  rayMesh.position.y = 0.25;
  rayMesh.visible = false;

  // shockwaves
  const waves = [];
  const waveGeo = own(new T.RingGeometry(0.92, 1, 64));
  for (let i = 0; i < 4; i++) {
    const m = new T.Mesh(waveGeo, own(new T.MeshBasicMaterial({ color: COL.brace, transparent: true, opacity: 0, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide })));
    m.rotation.x = -Math.PI / 2;
    m.position.y = 0.15;
    m.visible = false;
    m.userData = { age: 9, delay: 0, dur: 1.4, max: 52 };
    scene.add(m);
    waves.push(m);
  }

  // zones & routes (only what the API provides)
  const overlayGroup = new T.Group();
  scene.add(overlayGroup);
  let overlayKey = "";
  const zoneLabels = [];
  const routeDots = [];

  // ── props ───────────────────────────────────────────────────────────────
  const props = new Map(); // id -> { prop, group, pos, label, want }
  const heldProps = new Map();
  const mkLabel = (cls = "") => {
    const el = document.createElement("span");
    el.className = `tw-label ${cls}`;
    labelLayer.append(el);
    return el;
  };

  // ── camera ──────────────────────────────────────────────────────────────
  const cam = { az: PRESETS.angled.az, el: PRESETS.angled.el, r: PRESETS.angled.r, t: new T.Vector3(...PRESETS.angled.t) };
  const goal = { az: cam.az, el: cam.el, r: cam.r, t: cam.t.clone() };
  let preset = "angled", lastTouch = -99, now = 0, dragging = null;
  const setPreset = (name, silent) => {
    if (!PRESETS[name]) return;
    preset = name;
    const p = PRESETS[name];
    goal.az = p.az; goal.el = p.el; goal.r = p.r;
    if (p.t) goal.t.set(...p.t);
    lastTouch = now - 10;
    if (reduced) { cam.az = goal.az; cam.el = goal.el; cam.r = goal.r; cam.t.copy(goal.t); }
    if (!silent) onPreset(name);
  };
  const dom = renderer.domElement;
  dom.addEventListener("pointerdown", (e) => {
    dragging = { x: e.clientX, y: e.clientY };
    dom.setPointerCapture(e.pointerId);
    dom.classList.add("drag");
  });
  dom.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    goal.az -= (e.clientX - dragging.x) * 0.0065;
    goal.el = clamp(goal.el + (e.clientY - dragging.y) * 0.005, 0.12, 1.52);
    dragging.x = e.clientX; dragging.y = e.clientY;
    lastTouch = now;
    if (preset !== "free") { preset = "free"; onPreset("free"); }
  });
  const endDrag = (e) => { dragging = null; dom.classList.remove("drag"); try { dom.releasePointerCapture(e.pointerId); } catch {} };
  dom.addEventListener("pointerup", endDrag);
  dom.addEventListener("pointercancel", endDrag);
  dom.addEventListener("wheel", (e) => {
    e.preventDefault();
    goal.r = clamp(goal.r * Math.exp(e.deltaY * 0.0012), 42, 180);
    lastTouch = now;
    if (preset !== "free") { preset = "free"; onPreset("free"); }
  }, { passive: false });
  dom.addEventListener("dblclick", () => setPreset("angled"));

  // ── state ───────────────────────────────────────────────────────────────
  const cur = { shoulder_pan: 0, shoulder_lift: -90, elbow_flex: 90, wrist_flex: 60, wrist_roll: 0, gripper: 5 };
  let want = { ...cur }, first = true;
  let data = { objects: [], candidates: [], stopped: false, load: 0, moving: false };
  let flash = 0, loadVis = 0, stopAge = 99;
  const tipW = new T.Vector3(), anchorW = new T.Vector3(), tmp = new T.Vector3();

  function setData(d) {
    data = d;
    if (d.joints) want = { ...cur, ...d.joints };
    if (first && d.joints) { Object.assign(cur, want); first = false; }
    if (d.stopped && !ghost.root.visible) {
      ghost.root.visible = true;
      ghost.set(want);
    }
    if (!d.stopped) ghost.root.visible = false;
    syncProps(d);
    syncOverlay(d);
  }

  function syncProps(d) {
    const seen = new Set();
    for (const o of d.objects || []) {
      if (!o.table_xy) continue;
      seen.add(o.id);
      let p = props.get(o.id);
      if (!p) {
        const prop = buildProp(T, propKind(o.id, o.name));
        scene.add(prop.group);
        prop.mats.forEach((m) => { m.userData.base = m.opacity; m.transparent = true; });
        const [wx, wz] = tableToWorld(...o.table_xy);
        prop.group.position.set(wx, 0, wz);
        prop.group.scale.setScalar(0.01);
        p = { prop, label: mkLabel(), born: now };
        props.set(o.id, p);
      }
      p.o = o;
    }
    for (const [id, p] of props) {
      if (seen.has(id)) continue;
      scene.remove(p.prop.group);
      p.prop.geos.forEach((g) => g.dispose());
      p.prop.mats.forEach((m) => m.dispose());
      p.label.remove();
      props.delete(id);
    }
    // candidate halos
    for (const id of d.candidates || []) {
      if (!halos.has(id)) {
        const h = new T.Mesh(haloGeo, haloMat);
        h.position.y = 0.16;
        scene.add(h);
        halos.set(id, h);
      }
    }
    for (const [id, h] of halos) {
      if (!(d.candidates || []).includes(id)) { scene.remove(h); halos.delete(id); }
    }
  }

  function syncOverlay(d) {
    const key = JSON.stringify([d.zones, d.routes, d.routeName]);
    if (key === overlayKey) return;
    overlayKey = key;
    while (overlayGroup.children.length) {
      const c = overlayGroup.children.pop();
      c.geometry?.dispose();
      c.material?.dispose?.();
    }
    zoneLabels.splice(0).forEach((z) => z.el.remove());
    routeDots.length = 0;
    const zones = d.zones && !Array.isArray(d.zones) ? d.zones : {};
    for (const [name, box] of Object.entries(zones)) {
      if (!Array.isArray(box) || box.length !== 4) continue;
      const [x0, z0] = tableToWorld(box[0], box[1]), [x1, z1] = tableToWorld(box[2], box[3]);
      const w = x1 - x0, h = z1 - z0;
      const m = new T.Mesh(new T.PlaneGeometry(w, h), new T.MeshBasicMaterial({ color: COL.warn, transparent: true, opacity: 0.2, depthWrite: false, side: T.DoubleSide }));
      m.rotation.x = -Math.PI / 2;
      m.position.set((x0 + x1) / 2, 0.1, (z0 + z1) / 2);
      overlayGroup.add(m);
      const edge = new T.LineLoop(new T.BufferGeometry().setFromPoints([[x0, z0], [x1, z0], [x1, z1], [x0, z1]].map(([x, z]) => new T.Vector3(x, 0.12, z))), new T.LineBasicMaterial({ color: COL.warn, transparent: true, opacity: 0.9 }));
      overlayGroup.add(edge);
      zoneLabels.push({ el: mkLabel("zone"), pos: new T.Vector3((x0 + x1) / 2, 1, (z0 + z1) / 2), text: `avoid · ${name}` });
      zoneLabels.at(-1).el.textContent = `avoid · ${name}`;
    }
    const routes = d.routes && typeof d.routes === "object" && !Array.isArray(d.routes) ? d.routes : {};
    for (const [name, pts] of Object.entries(routes)) {
      if (!Array.isArray(pts) || pts.length < 2) continue;
      const on = name === d.routeName;
      const v = pts.map(([x, y]) => { const [wx, wz] = tableToWorld(x, y); return new T.Vector3(wx, 0.6, wz); });
      const line = new T.Line(new T.BufferGeometry().setFromPoints(v), new T.LineBasicMaterial({ color: on ? COL.ok : 0x6c7a8d, transparent: true, opacity: on ? 0.95 : 0.4 }));
      overlayGroup.add(line);
      if (on) {
        for (let i = 1; i < v.length; i++) { // a wide glowing ribbon under the chosen route
          const len = v[i].distanceTo(v[i - 1]);
          const seg = new T.Mesh(new T.PlaneGeometry(len, 1.1), new T.MeshBasicMaterial({ color: COL.ok, transparent: true, opacity: 0.28, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide }));
          seg.rotation.x = -Math.PI / 2;
          const g = new T.Group();
          g.position.copy(v[i - 1]).lerp(v[i], 0.5);
          g.position.y = 0.3;
          g.rotation.y = Math.atan2(-(v[i].z - v[i - 1].z), v[i].x - v[i - 1].x);
          g.add(seg);
          overlayGroup.add(g);
        }
        const dot = new T.Sprite(new T.SpriteMaterial({ map: glowTex, color: COL.ok, transparent: true, blending: T.AdditiveBlending, depthWrite: false }));
        dot.scale.setScalar(5);
        overlayGroup.add(dot);
        routeDots.push({ dot, v });
      }
    }
  }

  function spawnWave(x, z, color, delay = 0, max = 52, dur = 1.5) {
    const w = waves.find((m) => m.userData.age > m.userData.dur + m.userData.delay) || waves[0];
    w.position.x = x; w.position.z = z;
    w.material.color.setHex(color);
    Object.assign(w.userData, { age: 0, delay, max, dur });
    w.visible = true;
  }

  /** Called by the section when a runtime event lands while the section is on screen. */
  function pulse(kind) {
    flash = 1;
    stopAge = 0;
    arm.root.updateMatrixWorld(true);
    arm.tip.getWorldPosition(tipW);
    const color = kind === "brace" ? COL.brace : COL.brace;
    if (reduced) {
      spawnWave(tipW.x, tipW.z, color, 0, 26, 0.6);
    } else {
      spawnWave(tipW.x, tipW.z, color, 0, 56, 1.5);
      spawnWave(tipW.x, tipW.z, color, 0.18, 36, 1.3);
      spawnWave(BASE.x, BASE.z, color, 0.05, 40, 1.4);
    }
  }

  // ── projection of DOM labels ────────────────────────────────────────────
  let W = 1, H = 1;
  const project = (v, el, dx = 0, dy = 0) => {
    tmp.copy(v).project(camera);
    if (tmp.z > 1 || tmp.z < -1) { el.style.opacity = "0"; return; }
    el.style.transform = `translate(${(tmp.x * 0.5 + 0.5) * W + dx}px, ${(-tmp.y * 0.5 + 0.5) * H + dy}px) translate(-50%, -100%)`;
    el.style.opacity = "";
  };

  // ── frame ───────────────────────────────────────────────────────────────
  function frame(dt, t) {
    now = t;
    // joints
    for (const k of Object.keys(cur)) cur[k] = reduced ? want[k] : damp(cur[k], want[k] ?? cur[k], 15, dt);
    arm.set(cur);
    if (data.stopped) ghost.set(want);
    arm.root.updateMatrixWorld(true);
    arm.tip.getWorldPosition(tipW);
    arm.heldAnchor.getWorldPosition(anchorW);
    const f = fk(cur);

    // heading line on the table
    const hl = Math.max(6, f.reach + 6);
    heading.set(BASE.x, BASE.z, BASE.x + Math.sin(f.pan) * hl, BASE.z + Math.cos(f.pan) * hl, 2.2);

    // trail
    if (!trailLast || tipW.distanceTo(trailLast) > 0.45) {
      trailPos.set([tipW.x, tipW.y, tipW.z], trailHead * 3);
      trailBirth[trailHead] = t;
      trailHead = (trailHead + 1) % TRAIL_N;
      (trailLast ||= new T.Vector3()).copy(tipW);
    }
    for (let i = 0; i < TRAIL_N; i++) {
      const a = 1 - (t - trailBirth[i]) / TRAIL_LIFE;
      trailAge[i] = a > 0 ? a * a : 0;
      trailSize[i] = 4 + 9 * Math.max(0, a);
    }
    trailGeo.attributes.aAge.needsUpdate = true;
    trailGeo.attributes.aSize.needsUpdate = true;
    trailGeo.attributes.position.needsUpdate = true;
    trail.visible = !data.stopped || stopAge < 3;

    // gripper load glow
    const loadWant = clamp((data.load || 0) / 0.5, 0, 1);
    loadVis = damp(loadVis, loadWant, 9, dt);
    gripGlow.position.copy(anchorW);
    gripGlow.scale.setScalar(7 + loadVis * 15);
    gripGlow.material.opacity = 0.08 + loadVis * 0.8 + (data.moving ? 0.12 : 0);
    tipLight.position.copy(anchorW);
    tipLight.intensity = (0.4 + loadVis * 5) * (data.stopped ? 0 : 1);

    // stop freeze
    stopAge += dt;
    flash = Math.max(0, flash - dt * 0.7);
    const frozenGlow = data.stopped ? 0.18 + 0.08 * Math.sin(t * 5) : 0;
    const em = Math.max(flash, frozenGlow);
    skins.body.emissive.setRGB(em * 0.85, em * 0.12, em * 0.18);
    skins.servo.emissive.setRGB(em * 0.5, em * 0.06, em * 0.1);
    ghost.root.visible = data.stopped;
    gskins.body.opacity = 0.14 + 0.12 * (0.5 + 0.5 * Math.sin(t * 4)) + flash * 0.3;
    gskins.servo.opacity = gskins.body.opacity * 1.5;
    gskins.accent.opacity = 0.4 + flash * 0.4;
    gskins.hub.opacity = 0.35;
    stopLine.visible = stopRing.visible = data.stopped;
    if (data.stopped) {
      const h = Math.max(0.5, tipW.y);
      stopLine.scale.set(1, h, 1);
      stopLine.position.set(tipW.x, h / 2, tipW.z);
      stopRing.position.x = tipW.x; stopRing.position.z = tipW.z;
      const k = 1 + 0.12 * Math.sin(t * 6);
      stopRing.scale.set(k, k, k);
    }
    for (const w of waves) {
      const u = w.userData;
      u.age += dt;
      const a = u.age - u.delay;
      if (a < 0 || a > u.dur) { w.visible = a < 0; w.material.opacity = 0; if (a > u.dur) w.visible = false; continue; }
      const k = a / u.dur;
      const e = 1 - (1 - k) ** 3;
      w.scale.setScalar(reduced ? 8 + e * u.max : 2 + e * u.max);
      w.material.opacity = (1 - k) ** 1.6 * 0.9;
      w.visible = true;
    }

    // props
    const heldId = data.heldId;
    const heldAny = !!heldId;
    for (const [id, p] of props) {
      const o = p.o;
      if (!o) continue;
      const [wx, wz] = tableToWorld(...o.table_xy);
      const g = p.prop.group;
      g.position.x = reduced ? wx : damp(g.position.x, wx, 12, dt);
      g.position.z = reduced ? wz : damp(g.position.z, wz, 12, dt);
      const age = Math.min(1, (t - p.born) / 0.7);
      const s = reduced ? 1 : 1 - (1 - age) ** 3;
      g.scale.setScalar(Math.max(0.01, s));
      const isHeld = id === heldId;
      const fadeTo = isHeld ? (o.virtual ? 0.22 : 0) : 1;
      const vis = p.vis ?? 1;
      p.vis = damp(vis, fadeTo, 14, dt);
      for (const m of p.prop.mats) m.opacity = p.vis * (m.userData.base ?? 1);
      g.visible = p.vis > 0.02;
      // label
      const cls = ["tw-label", id === data.targetId ? "target" : "", (data.candidates || []).includes(id) ? "cand" : "", isHeld ? "held" : ""].join(" ").trim();
      if (p.label.className !== cls) p.label.className = cls;
      const txt = o.virtual ? o.name : `${o.name} ${Math.round((o.confidence ?? 0) * 100)}%`;
      if (p.label.dataset.t !== txt) { p.label.dataset.t = txt; p.label.textContent = txt; }
      tmp.set(g.position.x, p.prop.height + 4.5, g.position.z);
      project(tmp, p.label);
      p.label.style.opacity = p.vis < 0.4 && !isHeld ? "0.55" : "";
    }
    // held attachment
    let heldShown = null;
    if (heldAny) {
      const src = props.get(heldId);
      const kind = src?.prop.kind || "block";
      let hp = heldProps.get(heldId);
      if (!hp) {
        hp = buildProp(T, kind);
        hp.group.traverse((m) => { if (m.isMesh) m.castShadow = true; });
        scene.add(hp.group);
        heldProps.set(heldId, hp);
      }
      hp.group.visible = true;
      hp.group.position.set(anchorW.x, Math.max(0, anchorW.y - hp.height * 0.35), anchorW.z);
      heldShown = hp;
    }
    for (const [id, hp] of heldProps) if (hp !== heldShown) hp.group.visible = false;

    // target + candidates + ray
    const tp = props.get(data.targetId);
    targetGroup.visible = !!tp;
    if (tp) {
      targetGroup.position.set(tp.prop.group.position.x, 0, tp.prop.group.position.z);
      const k = reduced ? 0.5 : (t * 0.9) % 1;
      ring2.scale.setScalar(1 + k * 0.7);
      ring2.material.opacity = (1 - k) * 0.7;
      ring1.rotation.z = reduced ? 0 : t * 0.4;
      ringMat.color.setHex(data.stopped ? COL.brace : COL.grip);
      beam.material.color.setHex(data.stopped ? COL.brace : COL.grip);
      beam.material.opacity = 0.2 + 0.14 * (reduced ? 1 : 0.5 + 0.5 * Math.sin(t * 2.2));
    }
    for (const [id, h] of halos) {
      const p = props.get(id);
      h.visible = !!p;
      if (p) {
        h.position.x = p.prop.group.position.x; h.position.z = p.prop.group.position.z;
        h.rotation.y = reduced ? 0 : t * 0.6;
        const s = reduced ? 1 : 1 + 0.06 * Math.sin(t * 3 + id.length);
        h.scale.set(s, 1, s);
      }
    }
    const act = clamp(data.rayActive ?? 0, 0, 1);
    const ray = data.rayWorld;
    rayMesh.visible = !!ray && act > 0.3;
    if (rayMesh.visible) {
      rayMesh.set(...ray, 1.8);
      rayMesh.mat.opacity = 0.3 + act * 0.6;
    }
    // zones labels + route dots
    for (const z of zoneLabels) project(z.pos, z.el);
    for (const r of routeDots) {
      const total = r.v.reduce((a, p, i) => a + (i ? p.distanceTo(r.v[i - 1]) : 0), 0);
      let d = ((t * 14) % (total || 1)), i = 1;
      while (i < r.v.length - 1 && d > r.v[i].distanceTo(r.v[i - 1])) { d -= r.v[i].distanceTo(r.v[i - 1]); i++; }
      const seg = r.v[i].distanceTo(r.v[i - 1]) || 1;
      r.dot.position.lerpVectors(r.v[i - 1], r.v[i], Math.min(1, d / seg));
    }
    edgeGlow.material.opacity = 0;

    // camera
    const p = PRESETS[preset];
    if (preset === "follow") goal.t.set(tipW.x, Math.max(4, tipW.y * 0.6), tipW.z);
    else if (p?.t) goal.t.set(...p.t);
    const idle = !reduced && (preset === "angled" || preset === "top") && t - lastTouch > 4 && !dragging;
    const swayAz = idle ? Math.sin(t * 0.16) * (preset === "top" ? 0.12 : 0.3) : 0;
    const baseAz = preset === "free" ? goal.az : (p?.az ?? goal.az);
    const gz = preset === "free" ? goal.az : baseAz + swayAz;
    const L = reduced ? 1 : 1 - Math.exp(-4.2 * dt);
    cam.az += (gz - cam.az) * (reduced ? 1 : 1 - Math.exp(-3.4 * dt));
    cam.el += (goal.el - cam.el) * L;
    cam.r += (goal.r - cam.r) * L;
    cam.t.lerp(goal.t, preset === "follow" ? 1 - Math.exp(-5 * dt) : L);
    camera.position.set(
      cam.t.x + cam.r * Math.sin(cam.az) * Math.cos(cam.el),
      cam.t.y + cam.r * Math.sin(cam.el),
      cam.t.z + cam.r * Math.cos(cam.az) * Math.cos(cam.el),
    );
    camera.lookAt(cam.t);
    renderer.render(scene, camera);
  }

  function resize() {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    W = w; H = h;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // keep the arm and mat in frame on narrow canvases
    camera.fov = w / h < 1.2 ? 46 : w / h > 2 ? 29 : 36;
    camera.updateProjectionMatrix();
  }
  renderer.domElement.addEventListener("webglcontextlost", (e) => e.preventDefault());
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();
  setPreset("angled", true);

  function dispose() {
    ro.disconnect();
    for (const p of props.values()) { p.prop.geos.forEach((g) => g.dispose()); p.prop.mats.forEach((m) => m.dispose()); p.label.remove(); }
    for (const hp of heldProps.values()) { hp.geos.forEach((g) => g.dispose()); hp.mats.forEach((m) => m.dispose()); }
    arm.geos.forEach((g) => g.dispose());
    ghost.geos.forEach((g) => g.dispose());
    disposeSkins(skins);
    disposeSkins(gskins);
    disposables.forEach((o) => o.dispose?.());
    renderer.dispose();
    renderer.domElement.remove();
  }

  return { setData, pulse, frame, resize, setPreset, getPreset: () => preset, dispose, camera, renderer, tipWorld: () => tipW };
}
