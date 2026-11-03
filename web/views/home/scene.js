// Home hub scene: a "core" that wears the live body signal, and five section portals around it.
// Everything that moves here is driven by the snapshot (setSignal) or a runtime event (shock / museSweep / packet).
// Nothing is decorative loop-motion beyond a slow idle float.

import { buildEmblems } from "./emblems.js";

const HUES = { base: 0x6f93c4, grip: 0x4fd8ff, open: 0xffc15a, brace: 0xff4d5e, muse: 0xa58bff, ok: 0x5af0a4 };

// Where the five portals hang, as an arch above the core (x, y, z). Tuned for the 1.63 aspect hero.
// The fifth slot is Lab live and Replay in replay mode (ids come from the router).
export const SLOTS = [
  [-4.4, 0.85, 0.25], // stage
  [-2.4, 2.05, -0.95], // flow
  [0, 2.85, -1.9], // signals
  [2.4, 2.05, -0.95], // learn
  [4.4, 0.85, 0.25], // lab | replay
];

const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));
const smooth = (x) => x * x * (3 - 2 * x);
const outExpo = (k) => (k >= 1 ? 1 : 1 - 2 ** (-10 * k));
const outBack = (k) => 1 + 2.2 * (k - 1) ** 3 + 1.2 * (k - 1) ** 2;

export function createScene(THREE, canvas, { onHover, onPick, ids }) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, 1.6, 0.1, 80);
  const world = new THREE.Group();
  scene.add(world);

  // disposal bookkeeping
  const disposables = [];
  const track = (o) => (disposables.push(o), o);

  // ── textures ───────────────────────────────────────────────────────────────
  const haloTex = track(
    (() => {
      const c = document.createElement("canvas");
      c.width = c.height = 128;
      const g = c.getContext("2d");
      const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
      grd.addColorStop(0, "rgba(255,255,255,1)");
      grd.addColorStop(0.25, "rgba(255,255,255,.35)");
      grd.addColorStop(1, "rgba(255,255,255,0)");
      g.fillStyle = grd;
      g.fillRect(0, 0, 128, 128);
      const t = new THREE.CanvasTexture(c);
      t.colorSpace = THREE.SRGBColorSpace;
      return t;
    })(),
  );
  const halo = (color, size, opacity) => {
    const m = track(new THREE.SpriteMaterial({ map: haloTex, color, transparent: true, opacity, depthWrite: false, blending: THREE.AdditiveBlending }));
    const s = new THREE.Sprite(m);
    s.scale.setScalar(size);
    return s;
  };

  // ── the core ───────────────────────────────────────────────────────────────
  const core = new THREE.Group();
  world.add(core);

  // 1. crystal: faceted, fresnel-lit, colour = what the body is doing
  const crystalGeo = track(new THREE.IcosahedronGeometry(0.7, 1));
  crystalGeo.computeVertexNormals();
  const faceCount = crystalGeo.attributes.position.count / 3;
  const rnd = new Float32Array(faceCount * 3);
  for (let f = 0; f < faceCount; f++) rnd.fill(Math.random(), f * 3, f * 3 + 3);
  crystalGeo.setAttribute("aRand", new THREE.BufferAttribute(rnd, 1));
  const crystalMat = track(
    new THREE.ShaderMaterial({
      uniforms: { uColor: { value: new THREE.Color(HUES.base) }, uI: { value: 0.5 }, uTime: { value: 0 }, uFlash: { value: 0 } },
      vertexShader: `
        attribute float aRand; varying vec3 vN; varying vec3 vV; varying float vR;
        void main(){ vec4 mv = modelViewMatrix * vec4(position,1.0); vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz); vR = aRand; gl_Position = projectionMatrix * mv; }`,
      fragmentShader: `
        uniform vec3 uColor; uniform float uI; uniform float uTime; uniform float uFlash; varying vec3 vN; varying vec3 vV; varying float vR;
        void main(){
          float f = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 1.8);
          float facet = 0.5 + 0.5 * sin(uTime * 0.7 + vR * 6.2831);
          vec3 c = uColor * (0.10 + 0.42 * facet * uI) + uColor * f * (0.9 + 1.4 * uI) + vec3(1.0) * uFlash * 0.55;
          gl_FragColor = vec4(c, 1.0);
          #include <colorspace_fragment>
        }`,
    }),
  );
  const crystal = new THREE.Mesh(crystalGeo, crystalMat);
  core.add(crystal);

  // 2. wire shell
  const shellGeo = track(new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(1.02, 2)));
  const shellMat = track(new THREE.LineBasicMaterial({ color: HUES.base, transparent: true, opacity: 0.25, blending: THREE.AdditiveBlending, depthWrite: false }));
  const shell = new THREE.LineSegments(shellGeo, shellMat);
  core.add(shell);

  // 3. particle cloud
  const N = 2400;
  const dir = new Float32Array(N * 3);
  const aR = new Float32Array(N);
  const aSeed = new Float32Array(N);
  const aSize = new Float32Array(N);
  const golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2;
    const r = Math.sqrt(1 - y * y);
    const th = golden * i;
    dir.set([Math.cos(th) * r, y, Math.sin(th) * r], i * 3);
    aR[i] = 1.0 + Math.random() * 0.38;
    aSeed[i] = Math.random();
    aSize[i] = 0.55 + Math.random() * 1.1;
  }
  const cloudGeo = track(new THREE.BufferGeometry());
  cloudGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(N * 3), 3));
  cloudGeo.setAttribute("aDir", new THREE.BufferAttribute(dir, 3));
  cloudGeo.setAttribute("aR", new THREE.BufferAttribute(aR, 1));
  cloudGeo.setAttribute("aSeed", new THREE.BufferAttribute(aSeed, 1));
  cloudGeo.setAttribute("aSize", new THREE.BufferAttribute(aSize, 1));
  cloudGeo.boundingSphere = new THREE.Sphere(new THREE.Vector3(), 3);
  const cloudMat = track(
    new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      uniforms: {
        uTime: { value: 0 }, uK: { value: 60 }, uColor: { value: new THREE.Color(HUES.base) }, uMuseC: { value: new THREE.Color(HUES.muse) },
        uEffort: { value: 0 }, uExt: { value: 0 }, uBrace: { value: 0 }, uMuse: { value: 0 }, uWave: { value: -9 }, uIntro: { value: 0 }, uI: { value: 0.5 }, uFlash: { value: 0 },
      },
      vertexShader: `
        attribute vec3 aDir; attribute float aR; attribute float aSeed; attribute float aSize;
        uniform float uTime, uK, uEffort, uExt, uBrace, uMuse, uWave, uIntro, uI;
        varying float vA; varying float vW; varying float vS;
        float h(float n){ return fract(sin(n * 91.345) * 47453.5453); }
        void main(){
          float r = aR;
          r *= 1.0 + 0.045 * sin(uTime * 0.7 + aSeed * 31.0);          // idle breath
          r *= 1.0 - 0.28 * uEffort;                                    // squeeze pulls the shell in
          r *= 1.0 + 0.32 * uExt;                                       // open hand pushes it out
          r *= mix(0.25, 1.0, uIntro);
          vec3 d = aDir;
          float a = uTime * (0.05 + 0.25 * uI) * (0.4 + aSeed);         // slow swirl
          float c = cos(a), s = sin(a);
          d = vec3(c * d.x + s * d.z, d.y, -s * d.x + c * d.z);
          float j = uBrace * (h(aSeed * 17.0 + floor(uTime * 24.0)) - 0.5) * 0.7;  // brace: the shell shudders
          vec3 p = d * (r + j);
          float w = exp(-pow((p.y - uWave) * 2.4, 2.0)) * uMuse;
          vW = w; vS = aSeed;
          vec4 mv = modelViewMatrix * vec4(p, 1.0);
          gl_Position = projectionMatrix * mv;
          gl_PointSize = aSize * uK / -mv.z * (1.0 + w * 2.2 + uBrace * 0.8);
          vA = (0.28 + 0.5 * uI) * (0.55 + 0.45 * aSeed);
        }`,
      fragmentShader: `
        uniform vec3 uColor; uniform vec3 uMuseC; uniform float uFlash;
        varying float vA; varying float vW; varying float vS;
        void main(){
          float d = length(gl_PointCoord - 0.5);
          float a = smoothstep(0.5, 0.0, d);
          vec3 col = mix(uColor, uMuseC, clamp(vW * 1.4, 0.0, 1.0)) + vec3(uFlash * 0.4);
          gl_FragColor = vec4(col, a * (vA + vW * 0.9 + uFlash * 0.5));
          #include <colorspace_fragment>
        }`,
    }),
  );
  const cloud = new THREE.Points(cloudGeo, cloudMat);
  cloud.frustumCulled = false;
  core.add(cloud);

  const coreHalo = halo(HUES.base, 7, 0.3);
  core.add(coreHalo);

  // 4. signal ring: the live EMG trace, wrapped around the core. Grip rises, open falls, brace pushes out.
  const RING_N = 160;
  const RING_R = 1.72;
  const ringTilt = new THREE.Group();
  ringTilt.rotation.x = -0.22;
  core.add(ringTilt);
  const mkRing = (color, opacity) => {
    const g = track(new THREE.BufferGeometry());
    g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(RING_N * 3), 3));
    const m = track(new THREE.LineBasicMaterial({ color, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false }));
    const l = new THREE.LineLoop(g, m);
    l.frustumCulled = false;
    ringTilt.add(l);
    return l;
  };
  const ringBase = mkRing(0x8fa3be, 0.22);
  const ringGrip = mkRing(HUES.grip, 0.95);
  const ringOpen = mkRing(HUES.open, 0.95);
  const ringBrace = mkRing(HUES.brace, 0.9);
  const ringTarget = { grip: new Float32Array(RING_N), open: new Float32Array(RING_N), brace: new Float32Array(RING_N) };
  const ringCur = { grip: new Float32Array(RING_N), open: new Float32Array(RING_N), brace: new Float32Array(RING_N) };
  const writeRing = (line, kind) => {
    const p = line.geometry.attributes.position.array;
    const cur = ringCur[kind];
    for (let i = 0; i < RING_N; i++) {
      const a = (i / RING_N) * Math.PI * 2;
      const v = cur[i];
      let rr = RING_R;
      let y = 0;
      if (kind === "grip") y = v * 0.85;
      else if (kind === "open") y = -v * 0.85;
      else rr += v * 0.9;
      p[i * 3] = Math.cos(a) * rr;
      p[i * 3 + 1] = y;
      p[i * 3 + 2] = Math.sin(a) * rr;
    }
    line.geometry.attributes.position.needsUpdate = true;
  };
  for (const [l, k] of [[ringBase, "base"], [ringGrip, "grip"], [ringOpen, "open"], [ringBrace, "brace"]]) {
    if (k === "base") {
      const p = l.geometry.attributes.position.array;
      for (let i = 0; i < RING_N; i++) {
        const a = (i / RING_N) * Math.PI * 2;
        p.set([Math.cos(a) * RING_R, 0, Math.sin(a) * RING_R], i * 3);
      }
    } else writeRing(l, k);
  }

  // 5. orbiters: the arm. parked when disarmed, slow when ready, quick while moving.
  const orbiters = [];
  const orbitMat = track(new THREE.LineBasicMaterial({ color: 0x8fa3be, transparent: true, opacity: 0.1, depthWrite: false }));
  const orbGeo = track(new THREE.OctahedronGeometry(0.075, 0));
  const orbMat = track(new THREE.MeshBasicMaterial({ color: HUES.ok }));
  for (let i = 0; i < 3; i++) {
    const pivot = new THREE.Group();
    pivot.rotation.set(0.3 + i * 0.55, i * 1.9, i * 0.4);
    const pts = [];
    for (let k = 0; k <= 96; k++) pts.push(new THREE.Vector3(Math.cos((k / 96) * Math.PI * 2) * 2.15, 0, Math.sin((k / 96) * Math.PI * 2) * 2.15));
    const line = new THREE.Line(track(new THREE.BufferGeometry().setFromPoints(pts)), orbitMat);
    const sat = new THREE.Mesh(orbGeo, orbMat);
    pivot.add(line, sat);
    core.add(pivot);
    orbiters.push({ sat, phase: i * 2.1, pivot });
  }

  // 6. shock / ripple rings (pooled)
  const ringGeo = track(new THREE.RingGeometry(0.99, 1, 128));
  const shocks = [];
  for (let i = 0; i < 8; i++) {
    const m = track(new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }));
    const mesh = new THREE.Mesh(ringGeo, m);
    mesh.visible = false;
    core.add(mesh);
    shocks.push({ mesh, t: 1, dur: 1, from: 1, to: 8, peak: 1, facing: true });
  }

  // ── portals ────────────────────────────────────────────────────────────────
  const hitGeo = track(new THREE.SphereGeometry(0.8, 12, 8));
  const hitMat = track(new THREE.MeshBasicMaterial({ visible: false }));
  const frameGeo = track(new THREE.TorusGeometry(0.74, 0.007, 6, 72));
  const tetherMat = track(new THREE.LineBasicMaterial({ color: 0x8fa3be, transparent: true, opacity: 0.16, blending: THREE.AdditiveBlending, depthWrite: false }));
  const emblems = buildEmblems(THREE, track, ids);
  const portals = emblems.map((em, i) => {
    const group = new THREE.Group();
    const tilt = new THREE.Group();
    const spin = new THREE.Group();
    group.add(tilt);
    tilt.add(spin);
    em.object.scale.multiplyScalar(1.22);
    spin.add(em.object);
    const frameMat = track(new THREE.MeshBasicMaterial({ color: 0xaab6c6, transparent: true, opacity: 0.3, blending: THREE.AdditiveBlending, depthWrite: false }));
    const frame = new THREE.Mesh(frameGeo, frameMat);
    group.add(frame);
    const h = halo(0xaab6c6, 3.1, 0.0);
    group.add(h);
    const hit = new THREE.Mesh(hitGeo, hitMat);
    hit.userData.portal = i;
    group.add(hit);
    world.add(group);

    // tether: a bent line from the core surface to the portal
    const base = new THREE.Vector3(...SLOTS[i]);
    const mid = base.clone().multiplyScalar(0.5).add(new THREE.Vector3(0, 0.55, 0.35));
    const curve = new THREE.QuadraticBezierCurve3(base.clone().normalize().multiplyScalar(1.2), mid, base.clone().sub(base.clone().normalize().multiplyScalar(0.95)));
    const tGeo = track(new THREE.BufferGeometry().setFromPoints(curve.getPoints(40)));
    const tMat = track(tetherMat.clone());
    const tether = new THREE.Line(tGeo, tMat);
    world.add(tether);
    const packet = halo(0xffffff, 0.55, 0);
    world.add(packet);

    return {
      i, em, group, tilt, spin, frame, frameMat, halo: h, tether, tMat, curve, packet,
      base, lift: 0, hover: 0, rotX: 0, rotY: 0, seed: i * 1.7, pk: 1, pkColor: new THREE.Color(0xffffff), accent: new THREE.Color(0xaab6c6),
    };
  });

  // ── lights-free; state ─────────────────────────────────────────────────────
  const sig = { effort: 0, extensor: 0, brace: 0, live: false, arm: "disarmed", armMoving: false, museBusy: 0 };
  const cur = { effort: 0, extensor: 0, brace: 0, intensity: 0.4, orbit: 0, live: 0, flash: 0, museAmt: 0, wave: -9, pulse: 0 };
  const color = new THREE.Color(HUES.base);
  const tmpC = new THREE.Color();
  let museSweepT = 1;
  let introT0 = performance.now() / 1000;
  let hoverIdx = -1;
  let labelHover = -1;
  let size = { w: 1, h: 1 };
  let mode = "scene";
  const ptr = { x: 0, y: 0, tx: 0, ty: 0, inside: false, cx: 0, cy: 0 };
  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const hitMeshes = portals.map((p) => p.group.children.find((c) => c.userData.portal === p.i));
  const proj = new THREE.Vector3();
  let labels = null; // (i, x, y, depth, visible) => void
  let lookY = 0.7;
  let camD = 18;

  function resize(w, h, m) {
    if (w < 2 || h < 2) return;
    mode = m;
    size = { w, h };
    renderer.setSize(w, h, false);
    const aspect = w / h;
    camera.aspect = aspect;
    const tanH = Math.tan((camera.fov * Math.PI) / 360);
    const visW = mode === "scene" ? 11.6 : 12.6;
    const needH = mode === "scene" ? 5.9 : 6.6;
    const vfrac = mode === "scene" ? 0.7 : 0.98;
    const dW = visW / 2 / (tanH * aspect);
    const dH = needH / vfrac / 2 / tanH;
    camD = Math.max(dW, dH);
    const visH = 2 * camD * tanH;
    const centerFrac = mode === "scene" ? 0.4 : 0.52; // where on screen the content's middle should sit
    lookY = 0.95 - (0.5 - centerFrac) * visH;
    cloudMat.uniforms.uK.value = (h / 836) * 82 * Math.min(devicePixelRatio || 1, 2);
    camera.updateProjectionMatrix();
  }

  function setPointer(cx, cy) {
    const r = canvas.getBoundingClientRect();
    ptr.cx = cx;
    ptr.cy = cy;
    ptr.tx = ((cx - r.left) / r.width) * 2 - 1;
    ptr.ty = -(((cy - r.top) / r.height) * 2 - 1);
    ptr.inside = ptr.tx >= -1 && ptr.tx <= 1 && ptr.ty >= -1 && ptr.ty <= 1;
  }
  const pointerLeave = () => {
    ptr.inside = false;
    ptr.tx = 0;
    ptr.ty = 0;
  };

  function setSignal(s) {
    const e = s.emg;
    sig.live = !!(e && e.live && !e.error);
    sig.effort = e ? e.effort || 0 : 0;
    sig.extensor = e ? e.extensor || 0 : 0;
    sig.brace = e ? e.brace || 0 : 0;
    sig.arm = s.arm?.state ?? "disarmed"; // replay with no simulation has no arm
    if (e?.trace?.length) {
      const tr = e.trace;
      for (let i = 0; i < RING_N; i++) {
        const k = (i / RING_N) * (tr.length - 1);
        const a = tr[Math.floor(k)], b = tr[Math.min(tr.length - 1, Math.ceil(k))];
        const f = k - Math.floor(k);
        ringTarget.grip[i] = a[1] + (b[1] - a[1]) * f;
        ringTarget.open[i] = a[2] + (b[2] - a[2]) * f;
      }
    } else {
      ringTarget.grip.fill(0);
      ringTarget.open.fill(0);
    }
    if (e?.brace_trace?.length) {
      const tr = e.brace_trace;
      for (let i = 0; i < RING_N; i++) {
        const k = (i / RING_N) * (tr.length - 1);
        const a = tr[Math.floor(k)], b = tr[Math.min(tr.length - 1, Math.ceil(k))];
        ringTarget.brace[i] = a[1] + (b[1] - a[1]) * (k - Math.floor(k));
      }
    } else ringTarget.brace.fill(0);
    for (const p of portals) p.em.update?.(s);
  }

  function spawnShock(hex, { dur = 1.1, from = 1.1, to = 8.5, peak = 0.9, facing = true, tiltX = 0 } = {}) {
    const sh = shocks.find((x) => x.t >= 1) || shocks[0];
    sh.t = 0; sh.dur = dur; sh.from = from; sh.to = to; sh.peak = peak; sh.facing = facing;
    sh.mesh.material.color.set(hex);
    sh.mesh.userData.tiltX = tiltX;
    sh.mesh.visible = true;
  }
  function shock() {
    cur.flash = 1;
    spawnShock(HUES.brace, { dur: 1.0, to: 9, peak: 1, facing: true });
    spawnShock(HUES.brace, { dur: 1.4, to: 11, peak: 0.55, facing: false, tiltX: -Math.PI / 2 + 0.22 });
  }
  function pulse(hex) {
    spawnShock(hex, { dur: 1.1, from: 1.2, to: 4.2, peak: 0.5, facing: false, tiltX: -Math.PI / 2 + 0.22 });
  }
  function museSweep() {
    museSweepT = 0;
    spawnShock(HUES.muse, { dur: 2.0, from: 1.2, to: 7, peak: 0.6, facing: false, tiltX: -Math.PI / 2 + 0.22 });
  }
  function packet(i, hex) {
    const p = portals[i];
    if (!p) return;
    p.pk = 0;
    p.pkColor.set(hex);
    p.em.fire?.(hex);
  }
  function enter() {
    introT0 = performance.now() / 1000;
  }

  function render(dt, t) {
    if (size.w < 2) return;
    // smoothing: signals rise fast (the body is quick) and fall slower
    const up = (a, b) => a + (b - a) * (1 - Math.exp(-(b > a ? 22 : 6) * dt));
    cur.effort = up(cur.effort, sig.effort);
    cur.extensor = up(cur.extensor, sig.extensor);
    cur.brace = up(cur.brace, sig.brace);
    cur.flash = Math.max(0, cur.flash - dt * 1.7);
    const armBase = { disarmed: 0.28, ready: 0.62, moving: 0.85 }[sig.arm] ?? 0.2;
    const act = Math.max(cur.effort, cur.extensor, cur.brace);
    const tgtI = (sig.live ? armBase + act * 0.5 : armBase * 0.55) + cur.flash * 0.5;
    cur.intensity = up(cur.intensity, tgtI);
    const tgtOrbit = { disarmed: 0, ready: 0.3, moving: 1.9 }[sig.arm] ?? 0;
    cur.orbit += (tgtOrbit - cur.orbit) * (1 - Math.exp(-3 * dt));

    // colour: neutral steel, pulled toward the dominant signal
    color.set(HUES.base);
    color.lerp(tmpC.set(HUES.grip), smooth(clamp(cur.effort * 1.5)));
    color.lerp(tmpC.set(HUES.open), smooth(clamp(cur.extensor * 1.5)));
    color.lerp(tmpC.set(HUES.brace), smooth(clamp((cur.brace - 0.12) * 3.2 + cur.flash)));
    if (sig.arm === "stopped" || sig.arm === "fault") color.lerp(tmpC.set(HUES.brace), 0.35);

    // intro choreography
    const it = t - introT0;
    const coreK = clamp((it - 0.1) / 0.9);
    const introScale = reduceIntro ? 1 : outBack(coreK);
    cloudMat.uniforms.uIntro.value = reduceIntro ? 1 : outExpo(clamp((it - 0.05) / 1.3));

    // heartbeat: rate follows effort; calm when nothing is happening
    const rate = 0.5 + 1.3 * Math.max(cur.effort, cur.extensor);
    cur.pulse += dt * rate;
    const beat = Math.max(0, Math.sin(cur.pulse * Math.PI * 2)) ** 4;
    const scaleCore = introScale * (1 + 0.035 * beat * (0.4 + cur.intensity) - 0.1 * cur.effort + 0.12 * cur.extensor);
    core.scale.setScalar(Math.max(0.001, scaleCore));
    core.rotation.y += dt * (0.1 + 0.22 * cur.intensity);
    shell.rotation.y -= dt * 0.12;
    shell.rotation.x += dt * 0.04;
    crystal.rotation.y += dt * 0.25;

    crystalMat.uniforms.uColor.value.copy(color);
    crystalMat.uniforms.uI.value = cur.intensity + 0.4 * beat;
    crystalMat.uniforms.uTime.value = t;
    crystalMat.uniforms.uFlash.value = cur.flash;
    shellMat.color.copy(color);
    shellMat.opacity = 0.1 + 0.3 * cur.intensity;
    const cu = cloudMat.uniforms;
    cu.uTime.value = t;
    cu.uColor.value.copy(color);
    cu.uEffort.value = cur.effort;
    cu.uExt.value = cur.extensor;
    cu.uBrace.value = clamp(cur.brace * 1.2);
    cu.uI.value = cur.intensity;
    cu.uFlash.value = cur.flash;
    coreHalo.material.color.copy(color);
    coreHalo.material.opacity = (0.12 + 0.35 * cur.intensity + 0.3 * cur.flash) * clamp(coreK * 2);
    coreHalo.scale.setScalar(6.5 + 3 * cur.intensity + beat * 0.5);

    // muse sweep: a violet plane passes through the cloud, top to bottom
    if (museSweepT < 1) {
      museSweepT = Math.min(1, museSweepT + dt / 1.9);
      cu.uWave.value = 2.1 - museSweepT * 4.2;
      cu.uMuse.value = Math.sin(museSweepT * Math.PI);
    } else cu.uMuse.value = 0;

    // signal ring
    for (const k of ["grip", "open", "brace"]) {
      const c = ringCur[k], tg = ringTarget[k];
      for (let i = 0; i < RING_N; i++) c[i] += (tg[i] - c[i]) * Math.min(1, dt * 14);
    }
    writeRing(ringGrip, "grip");
    writeRing(ringOpen, "open");
    writeRing(ringBrace, "brace");
    ringTilt.rotation.y -= dt * 0.04;
    const live = sig.live ? 1 : 0;
    cur.live += (live - cur.live) * Math.min(1, dt * 4);
    // a channel's line only shows where the body is actually driving it; idle = just the quiet base ring
    const peak = (a) => { let m = 0; for (let i = 0; i < RING_N; i++) if (a[i] > m) m = a[i]; return m; };
    ringGrip.material.opacity = clamp(peak(ringCur.grip) * 4) * 0.95 * cur.live;
    ringOpen.material.opacity = clamp(peak(ringCur.open) * 4) * 0.95 * cur.live;
    ringBrace.material.opacity = clamp(peak(ringCur.brace) * 4) * 0.95 * cur.live;
    ringBase.material.opacity = 0.16 + 0.18 * cur.intensity;

    // orbiters
    orbMat.color.set(sig.arm === "moving" ? HUES.ok : sig.arm === "ready" ? 0xcfe3ff : 0x55627a);
    orbitMat.opacity = 0.05 + 0.1 * clamp(cur.orbit / 1.9) + (sig.arm === "ready" ? 0.04 : 0);
    for (const o of orbiters) {
      o.phase += dt * cur.orbit;
      o.sat.position.set(Math.cos(o.phase) * 2.15, 0, Math.sin(o.phase) * 2.15);
      o.sat.rotation.y += dt * 2;
    }

    // shocks
    for (const sh of shocks) {
      if (sh.t >= 1) {
        sh.mesh.visible = false;
        continue;
      }
      sh.t = Math.min(1, sh.t + dt / sh.dur);
      const k = outExpo(sh.t);
      sh.mesh.scale.setScalar(sh.from + (sh.to - sh.from) * k);
      sh.mesh.material.opacity = sh.peak * (1 - sh.t) ** 1.6;
      if (sh.facing) sh.mesh.quaternion.copy(camera.quaternion).premultiply(core.quaternion.clone().invert());
      else sh.mesh.rotation.set(sh.mesh.userData.tiltX, 0, 0);
    }

    // camera: pointer parallax, damped
    ptr.x += (ptr.tx - ptr.x) * (1 - Math.exp(-4 * dt));
    ptr.y += (ptr.ty - ptr.y) * (1 - Math.exp(-4 * dt));
    camera.position.set(ptr.x * 0.9, lookY + 0.9 + ptr.y * 0.45, camD);
    camera.lookAt(ptr.x * 0.15, lookY, 0);
    world.rotation.y = ptr.x * 0.05;

    // hover: label hover wins, else raycast
    camera.updateMatrixWorld();
    let rayIdx = -1;
    if (ptr.inside && mode === "scene") {
      ndc.set(ptr.tx, ptr.ty);
      ray.setFromCamera(ndc, camera);
      const hit = ray.intersectObjects(hitMeshes, false)[0];
      if (hit) rayIdx = hit.object.userData.portal;
    }
    const nextHover = labelHover >= 0 ? labelHover : rayIdx;
    if (nextHover !== hoverIdx) {
      hoverIdx = nextHover;
      onHover?.(hoverIdx);
      canvas.style.cursor = hoverIdx >= 0 && mode === "scene" ? "pointer" : "";
    }

    // portals
    for (const p of portals) {
      const k = reduceIntro ? 1 : outExpo(clamp((it - 0.35 - p.i * 0.11) / 1.0));
      p.hover += ((p.i === hoverIdx ? 1 : 0) - p.hover) * (1 - Math.exp(-9 * dt));
      const h = p.hover;
      p.accent.lerp(tmpC.set(p.em.accent ?? 0xaab6c6), 1 - Math.exp(-6 * dt));
      const bob = Math.sin(t * 0.6 + p.seed) * 0.08;
      p.group.position.set(p.base.x * k, p.base.y * k + bob + h * 0.28, p.base.z * k);
      p.group.scale.setScalar((0.35 + 0.65 * k) * (1 + h * 0.2));
      // tilt toward the cursor
      proj.copy(p.group.position).project(camera);
      const dx = ptr.tx - proj.x, dy = ptr.ty - proj.y;
      const tx = h > 0.05 ? clamp(-dy, -0.7, 0.7) * 0.7 : 0;
      const ty = h > 0.05 ? clamp(dx, -0.7, 0.7) * 0.9 : 0;
      p.rotX += (tx - p.rotX) * (1 - Math.exp(-10 * dt));
      p.rotY += (ty - p.rotY) * (1 - Math.exp(-10 * dt));
      p.tilt.rotation.set(p.rotX, p.rotY, 0);
      p.spin.rotation.y += dt * (0.18 + h * 0.5);
      p.frame.quaternion.copy(camera.quaternion);
      p.halo.quaternion.copy(camera.quaternion);
      p.frameMat.opacity = (0.22 + 0.6 * h) * k;
      p.frameMat.color.copy(p.accent).lerp(tmpC.set(0xffffff), h * 0.5);
      p.halo.material.color.copy(p.accent);
      p.halo.material.opacity = (0.05 + 0.4 * h) * k;
      p.em.setGlow?.(h, t, k);

      // tether
      p.tMat.opacity = (0.1 + 0.5 * h) * k;
      p.tMat.color.copy(p.accent).lerp(tmpC.set(0x8fa3be), 0.5 - h * 0.4);
      p.tether.scale.setScalar(1);
      if (p.pk < 1) {
        p.pk = Math.min(1, p.pk + dt / 0.9);
        p.curve.getPoint(smooth(p.pk), p.packet.position);
        p.packet.material.color.copy(p.pkColor);
        p.packet.material.opacity = Math.sin(p.pk * Math.PI) * 0.95;
      } else p.packet.material.opacity = 0;

      // HTML label anchor: just under the emblem
      if (labels) {
        proj.set(p.group.position.x, p.group.position.y - 0.88 * p.group.scale.x, p.group.position.z).project(camera);
        labels(p.i, (proj.x * 0.5 + 0.5) * size.w, (-proj.y * 0.5 + 0.5) * size.h, k, mode);
      }
    }

    renderer.render(scene, camera);
  }
  let reduceIntro = false;

  const api = {
    renderer,
    resize,
    setSignal,
    setPointer,
    pointerLeave,
    render,
    enter,
    shock,
    pulse,
    museSweep,
    packet,
    HUES,
    set labels(fn) { labels = fn; },
    set labelHover(i) { labelHover = i; },
    get hover() { return hoverIdx; },
    setAccent(i, hex) { portals[i]?.accent.set(hex); },
    pick() {
      if (mode === "scene" && hoverIdx >= 0) onPick?.(hoverIdx);
      return hoverIdx;
    },
    dispose() {
      scene.traverse((o) => {
        o.geometry?.dispose?.();
        const m = o.material;
        if (Array.isArray(m)) m.forEach((x) => x.dispose());
        else m?.dispose?.();
      });
      disposables.forEach((d) => d.dispose?.());
      emblems.forEach((e) => e.dispose?.());
      renderer.dispose();
      renderer.forceContextLoss?.();
    },
  };
  return api;
}
