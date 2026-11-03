// Procedural forearm + hand with four electrode pads. Two renderers behind one interface:
//   createHand3d(THREE, host)  WebGL (three.js)
//   createHandSvg(host)        2D side view, used when WebGL is missing
//
//   hand.setPose("rest" | "squeeze" | "open" | "brace" | "save")   what the operator should do
//   hand.setLevels([ch1..ch4 0..1], braceMix 0..1)                what the electrodes see (live)
//   hand.setScan(null | 0..1, "#hex")                              recording sweep along the forearm
//   hand.frame(dt, t)  hand.resize()  hand.dispose()
//
// The pose is an instruction; the glow is data. They are deliberately separate.

const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
const damp = (a, b, k, dt) => a + (b - a) * (1 - Math.exp(-k * dt));

/** Target pose parameters. curl = [knuckle, middle, tip] radians (positive closes toward the palm). */
export const POSES = {
  rest: { curl: [0.28, 0.38, 0.22], spread: 0.04, wrist: 0, thumb: 0.25, tense: 0, roll: 0.34 },
  squeeze: { curl: [1.42, 1.68, 1.05], spread: 0, wrist: -0.1, thumb: 1.0, tense: 0, roll: 1.0 },
  open: { curl: [-0.12, -0.06, -0.04], spread: 0.3, wrist: 0.55, thumb: -0.25, tense: 0, roll: 0.0 },
  brace: { curl: [0.5, 0.46, 0.28], spread: 0.0, wrist: 0, thumb: 0.45, tense: 1, roll: 0.62 },
  save: { curl: [0.28, 0.38, 0.22], spread: 0.04, wrist: 0, thumb: 0.25, tense: 0, roll: 0.34 },
};

export const HUE = { flexor: "#4fd8ff", extensor: "#ffc15a", ref: "#aab6c6", unused: "#46526a" };
/** Where each role sits on the forearm: flexors on the palm side (phi = pi), extensors on the back (phi = 0). */
const SLOTS = {
  flexor: [{ x: -2.35, z: -0.1 }, { x: -1.65, z: 0.12 }],
  extensor: [{ x: -2.35, z: 0.1 }, { x: -1.65, z: -0.12 }],
};
export const DEFAULT_ROLES = ["flexor", "flexor", "extensor", "extensor"];
const REF = { x: -3.2, phi: Math.PI / 2 };

const FOREARM_L = 3.4;
const radiusAt = (x) => {
  const u = clamp((x + FOREARM_L) / FOREARM_L);
  return 0.3 + 0.26 * (1 - u) ** 0.8;
};

// ── WebGL ────────────────────────────────────────────────────────────────────
export function createHand3d(THREE, host) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.domElement.className = "hand-canvas";
  host.prepend(renderer.domElement);
  const labels = document.createElement("div");
  labels.className = "pad-tags";
  host.append(labels);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 80);
  scene.add(new THREE.HemisphereLight(0x9cc8ff, 0x0a0e15, 0.9));
  const key = new THREE.DirectionalLight(0xffffff, 1.1);
  key.position.set(2, 5, 4);
  scene.add(key);

  const disposables = [];
  const track = (o) => (disposables.push(o), o);

  // glass skin: fresnel rim + a little fake top light; `glow` blends toward the brace red
  const skinU = {
    base: { value: new THREE.Color(0x15233a) },
    rim: { value: new THREE.Color(0x7fb4ff) },
    glowC: { value: new THREE.Color(0xff4d5e) },
    glow: { value: 0 },
  };
  const skin = track(
    new THREE.ShaderMaterial({
      uniforms: skinU,
      transparent: true,
      depthWrite: false,
      vertexShader: `varying vec3 vN; varying vec3 vV;
        void main(){ vN = normalize(normalMatrix*normal); vec4 mv = modelViewMatrix*vec4(position,1.0); vV = normalize(-mv.xyz); gl_Position = projectionMatrix*mv; }`,
      fragmentShader: `uniform vec3 base; uniform vec3 rim; uniform vec3 glowC; uniform float glow; varying vec3 vN; varying vec3 vV;
        void main(){
          float f = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 2.4);
          vec3 c = mix(base, glowC, glow*(0.28 + 0.6*f)) + mix(rim, glowC, glow)*f*0.85 + vec3(0.05)*max(vN.y,0.0);
          float a = 0.3 + f*0.6 + glow*0.16;
          gl_FragColor = vec4(c, a);
        }`,
    }),
  );

  const root = new THREE.Group(); // rolls about the forearm axis
  scene.add(root);

  // forearm: lathe along x, tapering to the wrist
  const prof = [new THREE.Vector2(0, -FOREARM_L - 0.06)];
  for (let i = 0; i <= 16; i++) {
    const y = -FOREARM_L + (i / 16) * FOREARM_L;
    prof.push(new THREE.Vector2(radiusAt(y) * (i === 0 ? 0.82 : 1), y));
  }
  prof.push(new THREE.Vector2(0, 0.02));
  const forearmGeo = track(new THREE.LatheGeometry(prof, 40));
  forearmGeo.rotateZ(-Math.PI / 2);
  const forearm = new THREE.Mesh(forearmGeo, skin);
  forearm.scale.set(1, 1, 0.92);
  forearm.renderOrder = 3;
  root.add(forearm);

  // muscles inside the glass: one belly per electrode channel (placed by role, see applyRoles)
  const muscles = [];
  const muscleGeo = track(new THREE.SphereGeometry(1, 28, 18));
  for (let i = 0; i < 4; i++) {
    const mat = track(new THREE.MeshStandardMaterial({ color: 0x1c2a40, emissive: new THREE.Color(HUE.flexor), emissiveIntensity: 0.05, roughness: 0.55, metalness: 0.1 }));
    const m = new THREE.Mesh(muscleGeo, mat);
    m.scale.set(0.8, 0.075, 0.11);
    root.add(m);
    muscles.push(m);
  }

  // hand
  const handG = new THREE.Group();
  root.add(handG);
  const palm = new THREE.Mesh(track(new THREE.SphereGeometry(1, 28, 18)), skin);
  palm.scale.set(0.55, 0.19, 0.46);
  palm.position.set(0.5, 0, 0);
  palm.renderOrder = 3;
  handG.add(palm);

  const fingers = [];
  const segGeoCache = new Map();
  const segment = (len, r) => {
    const k = `${len.toFixed(3)}:${r}`;
    if (!segGeoCache.has(k)) {
      const g = track(new THREE.CapsuleGeometry(r, len, 5, 12));
      g.rotateZ(-Math.PI / 2);
      g.translate(len / 2, 0, 0);
      segGeoCache.set(k, g);
    }
    const m = new THREE.Mesh(segGeoCache.get(k), skin);
    m.renderOrder = 3;
    return m;
  };
  const FINGER = [
    { z: -0.3, len: 0.95, spread: 1.0 },
    { z: -0.1, len: 1.0, spread: 0.3 },
    { z: 0.1, len: 0.92, spread: -0.3 },
    { z: 0.29, len: 0.74, spread: -1.0 },
  ];
  for (const f of FINGER) {
    const j1 = new THREE.Group();
    j1.position.set(0.98, 0, f.z);
    const l = [0.4, 0.27, 0.21].map((v) => v * f.len);
    const r = 0.085 * (0.85 + 0.15 * f.len);
    j1.add(segment(l[0], r));
    const j2 = new THREE.Group();
    j2.position.x = l[0];
    j2.add(segment(l[1], r * 0.94));
    const j3 = new THREE.Group();
    j3.position.x = l[1];
    j3.add(segment(l[2], r * 0.88));
    j2.add(j3);
    j1.add(j2);
    handG.add(j1);
    fingers.push({ j1, j2, j3, spread: f.spread });
  }
  const thumb1 = new THREE.Group();
  thumb1.position.set(0.28, -0.02, -0.4);
  thumb1.add(segment(0.34, 0.1));
  const thumb2 = new THREE.Group();
  thumb2.position.x = 0.34;
  thumb2.add(segment(0.28, 0.088));
  thumb1.add(thumb2);
  handG.add(thumb1);

  // electrode pads, cables, halos
  const haloTex = (() => {
    const c = document.createElement("canvas");
    c.width = c.height = 64;
    const g = c.getContext("2d");
    const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
    grd.addColorStop(0, "rgba(255,255,255,1)");
    grd.addColorStop(0.35, "rgba(255,255,255,.35)");
    grd.addColorStop(1, "rgba(255,255,255,0)");
    g.fillStyle = grd;
    g.fillRect(0, 0, 64, 64);
    return track(new THREE.CanvasTexture(c));
  })();
  const padGeo = track(new THREE.CylinderGeometry(0.12, 0.12, 0.035, 36));
  const ringGeo = track(new THREE.TorusGeometry(0.135, 0.012, 8, 40));
  const mkPad = (hue) => {
    const g = new THREE.Group();
    const mat = track(new THREE.MeshStandardMaterial({ color: 0x222c3c, emissive: new THREE.Color(hue), emissiveIntensity: 0.12, roughness: 0.4, metalness: 0.3 }));
    const disc = new THREE.Mesh(padGeo, mat);
    const ring = new THREE.Mesh(ringGeo, track(new THREE.MeshBasicMaterial({ color: hue, transparent: true, opacity: 0.8 })));
    ring.rotation.x = Math.PI / 2;
    ring.position.y = 0.02;
    const halo = new THREE.Sprite(track(new THREE.SpriteMaterial({ map: haloTex, color: hue, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false })));
    halo.position.y = 0.05;
    g.add(disc, ring, halo);
    g.renderOrder = 4;
    root.add(g);
    const cableMat = track(new THREE.MeshBasicMaterial({ color: hue, transparent: true, opacity: 0.42 }));
    return { g, mat, ring, halo, cableMat, cable: null, pos: new THREE.Vector3(), dir: new THREE.Vector3(0, 1, 0), tag: null, role: null, id: "" };
  };
  const pads = [0, 1, 2, 3].map((i) => ({ ...mkPad(HUE.flexor), id: `ch${i + 1}` }));
  pads.push({ ...mkPad(HUE.ref), id: "ref" });

  function placePad(p, i, x, phi, z) {
    const r = radiusAt(x);
    p.dir.set(0, Math.cos(phi), Math.sin(phi) * 0.92).normalize();
    p.pos.set(x, Math.cos(phi) * r, Math.sin(phi) * r * 0.92 + (phi === Math.PI / 2 ? 0 : z));
    p.g.position.copy(p.pos);
    p.g.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), p.dir);
    const out = new THREE.Vector3(-4.7, p.pos.y * 1.3 + (i - 2) * 0.1, p.pos.z * 1.2 + (i - 2) * 0.18);
    const curve = new THREE.CatmullRomCurve3([p.pos.clone(), p.pos.clone().add(p.dir.clone().multiplyScalar(0.16)).add(new THREE.Vector3(-0.3, 0, 0)), new THREE.Vector3(-3.7, p.pos.y * 1.25, p.pos.z * 1.1), out]);
    p.cable?.geometry.dispose();
    if (!p.cable) {
      p.cable = new THREE.Mesh(new THREE.BufferGeometry(), p.cableMat);
      root.add(p.cable);
    }
    p.cable.geometry = new THREE.TubeGeometry(curve, 36, 0.016, 6);
  }
  placePad(pads[4], 4, REF.x, REF.phi, 0);

  /** roles: per channel "flexor" | "extensor" | null (unused). Repositions, recolours, hides. */
  function applyRoles(roles) {
    const seen = { flexor: 0, extensor: 0 };
    roles.forEach((role, i) => {
      const p = pads[i];
      const m = muscles[i];
      p.role = role;
      const on = role === "flexor" || role === "extensor";
      p.g.visible = on;
      m.visible = on;
      const hue = HUE[role] || HUE.unused;
      p.mat.emissive.set(hue);
      p.ring.material.color.set(hue);
      p.halo.material.color.set(hue);
      p.cableMat.color.set(hue);
      m.userData.base = new THREE.Color(hue);
      if (on) {
        const slot = SLOTS[role][Math.min(1, seen[role]++)];
        const phi = role === "flexor" ? Math.PI : 0;
        placePad(p, i, slot.x, phi, slot.z);
        const r = radiusAt(slot.x);
        m.position.set(slot.x - 0.1, (role === "flexor" ? -1 : 1) * r * 0.42, slot.z * 1.5);
      }
      if (p.cable) p.cable.visible = on;
      p.tag.style.setProperty("--c", hue);
      p.tag.innerHTML = `<b>CH${i + 1}</b><i>${role || "unused"}</i>`;
      p.tag.hidden = !on;
    });
  }

  // recording sweep ring
  const scanMat = track(new THREE.MeshBasicMaterial({ color: 0x4fd8ff, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false }));
  const scan = new THREE.Mesh(track(new THREE.TorusGeometry(1, 0.018, 8, 64)), scanMat);
  scan.rotation.y = Math.PI / 2;
  root.add(scan);

  // floor rings
  const floor = new THREE.Group();
  const lineMat = track(new THREE.LineBasicMaterial({ color: 0x4fd8ff, transparent: true, opacity: 0.12 }));
  for (const rad of [2.2, 3.4, 4.6]) {
    const pts = [];
    for (let a = 0; a <= 96; a++) pts.push(new THREE.Vector3(Math.cos((a / 96) * 6.2832) * rad, 0, Math.sin((a / 96) * 6.2832) * rad));
    floor.add(new THREE.Line(track(new THREE.BufferGeometry().setFromPoints(pts)), lineMat));
  }
  for (let a = 0; a < 24; a++) {
    const t = (a / 24) * 6.2832;
    floor.add(new THREE.Line(track(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(Math.cos(t) * 2.2, 0, Math.sin(t) * 2.2), new THREE.Vector3(Math.cos(t) * 4.6, 0, Math.sin(t) * 4.6)])), lineMat));
  }
  floor.position.set(-0.75, -1.15, 0);
  scene.add(floor);

  // pad tags (HTML, projected each frame)
  for (const p of pads) {
    const el = document.createElement("span");
    el.className = `pad-tag ${p.id}`;
    el.style.setProperty("--c", p.id === "ref" ? HUE.ref : HUE.flexor);
    if (p.id === "ref") el.innerHTML = "<b>REF</b><i>reference · elbow bone</i>";
    labels.append(el);
    p.tag = el;
  }
  applyRoles(DEFAULT_ROLES);

  // state
  const cur = { ...POSES.rest, curl: [...POSES.rest.curl] };
  let tgt = POSES.rest;
  let levels = [0, 0, 0, 0];
  const lv = [0, 0, 0, 0];
  let braceMix = 0;
  let braceCur = 0;
  let scanP = null;
  let rolesKey = DEFAULT_ROLES.join("|");
  let yaw = 0;
  let pitch = 0;
  let yawT = 0;
  let pitchT = 0;
  let w = 1;
  let h = 1;
  const tmp = new THREE.Vector3();
  const tv = new THREE.Vector3();
  const tv2 = new THREE.Vector3();
  const red = new THREE.Color(0xff4d5e);
  const tint = new THREE.Color();

  host.addEventListener("pointermove", (e) => {
    const b = host.getBoundingClientRect();
    yawT = ((e.clientX - b.left) / b.width - 0.5) * 0.7;
    pitchT = ((e.clientY - b.top) / b.height - 0.5) * 0.3;
  });
  host.addEventListener("pointerleave", () => {
    yawT = 0;
    pitchT = 0;
  });

  function resize() {
    const r = host.getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;
    w = r.width;
    h = r.height;
    renderer.setSize(w, h, false);
    renderer.domElement.style.width = `${w}px`;
    renderer.domElement.style.height = `${h}px`;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function frame(dt, t) {
    if (w < 4) resize();
    const calm = matchMedia("(prefers-reduced-motion: reduce)").matches;
    // pose
    for (let i = 0; i < 3; i++) cur.curl[i] = damp(cur.curl[i], tgt.curl[i], 7, dt);
    for (const k of ["spread", "wrist", "thumb", "tense", "roll"]) cur[k] = damp(cur[k], tgt[k], k === "roll" ? 3.2 : 7, dt);
    fingers.forEach((f, i) => {
      const lag = 1 - i * 0.04;
      f.j1.rotation.z = -cur.curl[0] * lag;
      f.j2.rotation.z = -cur.curl[1] * lag;
      f.j3.rotation.z = -cur.curl[2] * lag;
      f.j1.rotation.y = cur.spread * f.spread;
    });
    thumb1.rotation.y = 0.78 - cur.thumb * 0.62;
    thumb1.rotation.z = -cur.thumb * 0.55;
    thumb2.rotation.z = -cur.thumb * 0.7;
    handG.rotation.z = cur.wrist;
    root.rotation.x = cur.roll * Math.PI;
    // tension (brace pose): forearm swells and trembles a little
    const shiver = cur.tense * (calm ? 0 : Math.sin(t * 38) * 0.006);
    forearm.scale.set(1, 1 + cur.tense * 0.06 + shiver, 0.92 * (1 + cur.tense * 0.06 + shiver));
    // live levels
    for (let i = 0; i < 4; i++) lv[i] = damp(lv[i], levels[i], 14, dt);
    braceCur = damp(braceCur, braceMix, 10, dt);
    skinU.glow.value = braceCur * 0.9;
    muscles.forEach((m, i) => {
      tint.copy(m.userData.base).lerp(red, braceCur);
      m.material.emissive.copy(tint);
      m.material.emissiveIntensity = 0.06 + lv[i] * 1.15;
      m.scale.y = 0.075 + lv[i] * 0.05 + cur.tense * 0.025;
      m.scale.z = 0.11 + lv[i] * 0.03;
    });
    pads.forEach((p, i) => {
      if (i < 4 && !p.g.visible) return;
      const L = i < 4 ? lv[i] : 0;
      p.mat.emissiveIntensity = 0.14 + L * 1.5;
      p.halo.material.opacity = i < 4 ? clamp(L * 1.15) : 0.0;
      const s = 0.3 + L * 0.9;
      p.halo.scale.set(s, s, s);
      p.ring.material.opacity = 0.45 + L * 0.55;
    });
    // sweep
    if (scanP == null) scanMat.opacity = damp(scanMat.opacity, 0, 12, dt);
    else {
      scanMat.opacity = damp(scanMat.opacity, 0.9, 12, dt);
      const x = -FOREARM_L + scanP * FOREARM_L;
      const r = radiusAt(x) + 0.1;
      scan.position.x = x;
      scan.scale.set(r, r, r * 0.92);
    }
    // camera
    yaw = damp(yaw, yawT, 3, dt);
    pitch = damp(pitch, pitchT, 3, dt);
    const sway = calm ? 0 : Math.sin(t * 0.35) * 0.1;
    const fov = (camera.fov * Math.PI) / 180;
    const dist = Math.max(5.9 / (2 * Math.tan(fov / 2) * camera.aspect), 5.0 / (2 * Math.tan(fov / 2)));
    const az = 0.2 + yaw + sway;
    const el = 0.5 + pitch;
    camera.position.set(-0.75 + Math.sin(az) * dist * Math.cos(el), Math.sin(el) * dist, Math.cos(az) * dist * Math.cos(el));
    camera.lookAt(-0.75, -0.05, 0);
    camera.rotateZ(w < 600 ? 0 : -0.3);
    renderer.render(scene, camera);
    // tags
    camera.updateMatrixWorld();
    for (const p of pads) {
      if (!p.g.visible) continue;
      p.g.getWorldPosition(tmp);
      const facing = tv.copy(p.dir).transformDirection(root.matrixWorld).dot(tv2.copy(camera.position).sub(tmp).normalize());
      tmp.project(camera);
      p.tag.style.transform = `translate(${((tmp.x + 1) / 2) * w}px, ${((1 - tmp.y) / 2) * h}px)`;
      p.tag.style.opacity = String(clamp(0.25 + facing * 1.4, 0.22, 1));
      p.tag.classList.toggle("back", facing < 0);
    }
  }

  resize();
  return {
    kind: "webgl",
    setPose(name) {
      tgt = POSES[name] || POSES.rest;
    },
    setLevels(l, brace = 0) {
      levels = l.map((v) => clamp(v || 0));
      braceMix = clamp(brace);
    },
    setRoles(roles) {
      const key = roles.join("|");
      if (key === rolesKey) return;
      rolesKey = key;
      applyRoles(roles);
    },
    setScan(p, color) {
      scanP = p;
      if (color) scanMat.color.set(color);
    },
    frame,
    resize,
    dispose() {
      disposables.forEach((o) => o.dispose?.());
      renderer.dispose();
      renderer.domElement.remove();
      labels.remove();
    },
  };
}

// ── 2D fallback ──────────────────────────────────────────────────────────────
export function createHandSvg(host) {
  const NS = "http://www.w3.org/2000/svg";
  host.insertAdjacentHTML(
    "afterbegin",
    `<svg class="hand-svg" viewBox="0 0 800 420" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Forearm and hand with four electrode pads">
      <defs><radialGradient id="hg"><stop offset="0" stop-color="#fff" stop-opacity="1"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient></defs>
      <path class="arm-body" d="M40 168 Q40 150 70 148 L430 176 L430 252 L70 282 Q40 280 40 262 Z"/>
      <g class="mus"></g>
      <g class="fing"></g>
      <path class="palm" d=""/>
      <g class="pads"></g>
      <text class="note" x="400" y="404" text-anchor="middle">2D diagram (WebGL unavailable)</text>
    </svg>`,
  );
  const svg = host.querySelector("svg");
  const padG = svg.querySelector(".pads");
  const musG = svg.querySelector(".mus");
  const fingG = svg.querySelector(".fing");
  const palm = svg.querySelector(".palm");
  const SLOT2 = { flexor: [[150, 281], [270, 276]], extensor: [[150, 152], [270, 162]] };
  const nodes = [0, 1, 2, 3, 4].map((i) => {
    const g = document.createElementNS(NS, "g");
    const hue = i === 4 ? HUE.ref : HUE.flexor;
    g.innerHTML = `<circle class="halo" r="34" fill="${hue}" opacity="0"/><circle class="disc" r="13" fill="#222c3c" stroke="${hue}" stroke-width="2.5"/><text text-anchor="middle" class="tag" fill="${hue}"></text>`;
    padG.append(g);
    const m = document.createElementNS(NS, "ellipse");
    m.setAttribute("rx", 54);
    m.setAttribute("ry", 12);
    m.setAttribute("opacity", ".12");
    if (i < 4) musG.append(m);
    return { g, m, hue, role: null };
  });
  const setAt = (n, x, y, text, hue, below) => {
    for (const el of n.g.querySelectorAll("circle")) {
      el.setAttribute("cx", x);
      el.setAttribute("cy", y);
    }
    const t = n.g.querySelector("text");
    t.setAttribute("x", x);
    t.setAttribute("y", y + (below ? 38 : -24));
    t.setAttribute("fill", hue);
    t.textContent = text;
    n.g.querySelector(".disc").setAttribute("stroke", hue);
    n.g.querySelector(".halo").setAttribute("fill", hue);
  };
  setAt(nodes[4], 70, 214, "REF elbow", HUE.ref, true);
  let rolesKey = "";
  function setRoles(roles) {
    const key = roles.join("|");
    if (key === rolesKey) return;
    rolesKey = key;
    const seen = { flexor: 0, extensor: 0 };
    roles.forEach((role, i) => {
      const n = nodes[i];
      n.role = role;
      const on = role === "flexor" || role === "extensor";
      n.g.style.display = on ? "" : "none";
      n.m.style.display = on ? "" : "none";
      if (!on) return;
      const [x, y] = SLOT2[role][Math.min(1, seen[role]++)];
      const hue = HUE[role];
      setAt(n, x, y, `CH${i + 1} ${role}`, hue, role === "flexor");
      n.m.setAttribute("cx", x + 20);
      n.m.setAttribute("cy", y + (role === "flexor" ? -34 : 34));
      n.m.setAttribute("fill", hue);
    });
  }
  setRoles(DEFAULT_ROLES);
  const fing = [0, 1, 2, 3].map(() => {
    const p = document.createElementNS(NS, "path");
    p.setAttribute("class", "finger");
    fingG.append(p);
    return p;
  });
  const cur = { ...POSES.rest, curl: [...POSES.rest.curl] };
  let tgt = POSES.rest;
  let levels = [0, 0, 0, 0];
  const lv = [0, 0, 0, 0];
  let braceMix = 0;
  let braceCur = 0;
  const body = svg.querySelector(".arm-body");
  const lens = [[56, 38, 30], [62, 42, 32], [58, 39, 30], [46, 31, 24]];

  function frame(dt) {
    for (let i = 0; i < 3; i++) cur.curl[i] = damp(cur.curl[i], tgt.curl[i], 7, dt);
    cur.wrist = damp(cur.wrist, tgt.wrist, 7, dt);
    for (let i = 0; i < 4; i++) lv[i] = damp(lv[i], levels[i], 14, dt);
    braceCur = damp(braceCur, braceMix, 10, dt);
    cur.tense = damp(cur.tense, tgt.tense, 7, dt);
    // wrist at (430, 214); the hand tilts with the wrist angle (positive = back of hand up)
    const wa = -cur.wrist;
    const wx = 430;
    const wy = 214;
    const px = wx + Math.cos(wa) * 70;
    const py = wy + Math.sin(wa) * 70;
    palm.setAttribute("d", `M${wx} ${wy - 36} L${px} ${py - 40} L${px} ${py + 40} L${wx} ${wy + 38} Z`);
    fing.forEach((f, i) => {
      let x = px;
      let y = py - 26 + i * 18;
      let a = wa;
      let d = `M${x} ${y}`;
      for (let j = 0; j < 3; j++) {
        a += cur.curl[j] * 0.95;
        x += Math.cos(a) * lens[i][j] * 1.35;
        y += Math.sin(a) * lens[i][j] * 1.35;
        d += ` L${x} ${y}`;
      }
      f.setAttribute("d", d);
    });
    const glow = braceCur;
    body.style.fill = glow > 0.05 ? `color-mix(in srgb, #ff4d5e ${Math.round(glow * 45)}%, #15233a)` : "";
    nodes.forEach((n, i) => {
      const L = i < 4 ? lv[i] : 0;
      n.g.querySelector(".halo").setAttribute("opacity", String(clamp(L * 0.9)));
      n.g.querySelector(".disc").setAttribute("fill", L > 0.02 ? n.hue : "#222c3c");
      n.g.querySelector(".disc").setAttribute("fill-opacity", String(0.25 + L * 0.75));
      if (i < 4) n.m.setAttribute("opacity", String(0.1 + L * 0.7));
    });
  }
  return {
    kind: "svg",
    setPose(name) {
      tgt = POSES[name] || POSES.rest;
    },
    setLevels(l, brace = 0) {
      levels = l.map((v) => clamp(v || 0));
      braceMix = clamp(brace);
    },
    setRoles,
    setScan() {},
    frame,
    resize() {},
    dispose() {
      svg.remove();
    },
  };
}
