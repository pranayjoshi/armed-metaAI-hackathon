// Signal terrain: the muscle signal as a scrolling 3D surface.
// time flows toward the viewer (newest at the front), height = normalised level (0..1 of the
// calibrated maximum). Three lanes, one hue each: grip (flexors), open hand (extensors), brace.
// Everything drawn comes from SignalBuffer (accumulated emg.trace / emg.brace_trace rows) and from
// runtime events; nothing is simulated.
import { WINDOW_S } from "./buffer.js";

const H = 4.8; // world height of level 1.0
const ZS = 0.85; // world units per second of history
const DEPTH = WINDOW_S * ZS;
const LANE_W = 2.9;
const LANES = [
  { key: "g", id: "grip", x: -5.6, hue: "#4fd8ff", title: "GRIP", sub: "flexors", thr: "confirm", thrName: "CONFIRM" },
  { key: "o", id: "open", x: 0, hue: "#ffc15a", title: "OPEN HAND", sub: "extensors", thr: "stop", thrName: "STOP" },
  { key: "b", id: "brace", x: 5.6, hue: "#ff4d5e", title: "BRACE", sub: "co-contraction", thr: "brace", thrName: "BRACE" },
];
const MAXN = 1800; // vertex slots per strip (30 s at up to 60 Hz)
const EVENT_STYLE = {
  "emg.squeeze": { text: "SQUEEZE", hue: "#4fd8ff", lift: 0.0 },
  "emg.open_hand": { text: "OPEN HAND", hue: "#ffc15a", lift: 0.6 },
  "emg.brace": { text: "BRACE", hue: "#ff4d5e", lift: 1.2 },
  "arm.stopped": { text: "ARM STOPPED", hue: "#eaf0f8", lift: 1.8 },
};

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));

export async function createTerrain(host, ctx, buf, getEvents) {
  const THREE = await ctx.loadThree();
  const { reduced, damp, loop } = ctx;

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  renderer.setClearColor(0x000000, 0);
  renderer.domElement.className = "terrain-canvas";
  host.append(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 160);
  const world = new THREE.Group();
  scene.add(world);

  // ── camera orbit (own, small: pointer drag + wheel + double-click reset) ────
  const home = { az: -0.08, pol: 1.17, rad: 24 };
  const cam = { ...home, tAz: home.az, tPol: home.pol, tRad: home.rad };
  const target = new THREE.Vector3(0.7, -0.7, -9.4);
  let dragging = false;
  let lastInteract = -99;
  const dom = renderer.domElement;
  const onDown = (e) => {
    dragging = true;
    dom.setPointerCapture?.(e.pointerId);
    dom.classList.add("grab");
    cam._x = e.clientX;
    cam._y = e.clientY;
  };
  const onMove = (e) => {
    if (!dragging) return;
    cam.tAz -= (e.clientX - cam._x) * 0.0062;
    cam.tPol = clamp(cam.tPol - (e.clientY - cam._y) * 0.0048, 0.55, 1.5);
    cam.tAz = clamp(cam.tAz, -1.25, 1.25);
    cam._x = e.clientX;
    cam._y = e.clientY;
    lastInteract = performance.now();
  };
  const onUp = () => {
    dragging = false;
    dom.classList.remove("grab");
  };
  const onWheel = (e) => {
    e.preventDefault();
    cam.tRad = clamp(cam.tRad * (1 + Math.sign(e.deltaY) * 0.07), 14, 38);
    lastInteract = performance.now();
  };
  const onReset = () => Object.assign(cam, { tAz: home.az, tPol: home.pol, tRad: home.rad });
  dom.addEventListener("pointerdown", onDown);
  dom.addEventListener("pointermove", onMove);
  dom.addEventListener("pointerup", onUp);
  dom.addEventListener("pointercancel", onUp);
  dom.addEventListener("wheel", onWheel, { passive: false });
  dom.addEventListener("dblclick", onReset);

  // ── sprites for text ────────────────────────────────────────────────────────
  try {
    await Promise.race([document.fonts.load('500 28px "JetBrains Mono"'), new Promise((r) => setTimeout(r, 700))]);
  } catch {}
  function makeLabel(text, { color = "#aab6c6", size = 26, weight = 500, align = "center", bg = "rgba(5,7,11,.55)" } = {}) {
    const canvas = document.createElement("canvas");
    const ctx2 = canvas.getContext("2d");
    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    tex.anisotropy = 4;
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true, depthTest: false, depthWrite: false });
    const sprite = new THREE.Sprite(mat);
    sprite.renderOrder = 20;
    sprite.userData.set = (t, c = color) => {
      if (sprite.userData.text === t && sprite.userData.color === c) return;
      sprite.userData.text = t;
      sprite.userData.color = c;
      const font = `${weight} ${size}px "JetBrains Mono", ui-monospace, monospace`;
      ctx2.font = font;
      const padX = 12;
      const w = Math.ceil(ctx2.measureText(t).width) + padX * 2;
      const h = Math.ceil(size * 1.65);
      canvas.width = w;
      canvas.height = h;
      ctx2.font = font;
      if (bg) {
        ctx2.fillStyle = bg;
        ctx2.beginPath();
        ctx2.roundRect(0, 0, w, h, 8);
        ctx2.fill();
      }
      ctx2.fillStyle = c;
      ctx2.textBaseline = "middle";
      ctx2.textAlign = "left";
      ctx2.fillText(t, padX, h / 2 + 1);
      tex.needsUpdate = true;
      const k = 0.0105; // world units per canvas pixel
      sprite.scale.set(w * k, h * k, 1);
    };
    sprite.userData.set(text, color);
    sprite.center.set(align === "left" ? 0 : align === "right" ? 1 : 0.5, 0.5);
    return sprite;
  }

  // ── static furniture ────────────────────────────────────────────────────────
  const disposables = [];
  const track = (o) => (disposables.push(o), o);
  const lineMat = (color, opacity, dashed = false) =>
    track(
      dashed
        ? new THREE.LineDashedMaterial({ color, transparent: true, opacity, dashSize: 0.28, gapSize: 0.22, depthWrite: false })
        : new THREE.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false }),
    );
  const segs = (pts, mat) => {
    const g = track(new THREE.BufferGeometry().setFromPoints(pts));
    return new THREE.LineSegments(g, mat);
  };

  const X0 = -8;
  const X1 = 8;
  // floor border + faint plates per lane
  for (const L of LANES) {
    const plate = new THREE.Mesh(
      track(new THREE.PlaneGeometry(LANE_W + 0.5, DEPTH)),
      track(new THREE.MeshBasicMaterial({ color: L.hue, transparent: true, opacity: 0.035, depthWrite: false, side: THREE.DoubleSide })),
    );
    plate.rotation.x = -Math.PI / 2;
    plate.position.set(L.x, 0, -DEPTH / 2);
    world.add(plate);
    const hw = (LANE_W + 0.5) / 2;
    world.add(
      segs(
        [
          new THREE.Vector3(L.x - hw, 0, 0), new THREE.Vector3(L.x - hw, 0, -DEPTH),
          new THREE.Vector3(L.x + hw, 0, 0), new THREE.Vector3(L.x + hw, 0, -DEPTH),
        ],
        lineMat(L.hue, 0.22),
      ),
    );
    const t = makeLabel(L.title, { color: L.hue, size: 28, weight: 600 });
    t.position.set(L.x, 0.02, 1.0);
    world.add(t);
    const s = makeLabel(L.sub, { color: "#6c7a8d", size: 18, bg: null });
    s.position.set(L.x, 0.02, 1.7);
    world.add(s);
  }

  // side wall: 5 faint level gridlines + labels (honest scale: 0..100 % of calibrated max)
  {
    const pts = [];
    for (const lv of [0, 0.25, 0.5, 0.75, 1]) pts.push(new THREE.Vector3(X0, lv * H, 0), new THREE.Vector3(X0, lv * H, -DEPTH));
    world.add(segs(pts, lineMat("#a0bee6", 0.1)));
    for (const lv of [0, 0.5, 1]) {
      const l = makeLabel(`${Math.round(lv * 100)}%`, { color: "#6c7a8d", size: 22, align: "right", bg: null });
      l.position.set(X0 - 0.15, lv * H, 0.1);
      world.add(l);
    }
  }
  // age ticks across the floor
  {
    const pts = [];
    for (const s of [10, 20, 30]) pts.push(new THREE.Vector3(X0, 0, -s * ZS), new THREE.Vector3(X1, 0, -s * ZS));
    world.add(segs(pts, lineMat("#a0bee6", 0.13)));
    for (const s of [10, 20, 30]) {
      const l = makeLabel(`-${s} s`, { color: "#6c7a8d", size: 22, align: "right", bg: null });
      l.position.set(X0 - 0.15, 0.02, -s * ZS);
      world.add(l);
    }
    const nowLine = segs([new THREE.Vector3(X0, 0.01, 0), new THREE.Vector3(X1, 0.01, 0)], lineMat("#eaf0f8", 0.5));
    world.add(nowLine);
    const nowL = makeLabel("now", { color: "#eaf0f8", size: 22, align: "right", bg: null });
    nowL.position.set(X0 - 0.15, 0.02, 0.1);
    world.add(nowL);
  }
  // 1 s floor lines that scroll with the data (so a flat signal still visibly flows)
  const NSEC = WINDOW_S + 2;
  const secGeo = track(new THREE.BufferGeometry());
  const secPos = new Float32Array(NSEC * 2 * 3);
  secGeo.setAttribute("position", new THREE.BufferAttribute(secPos, 3));
  const secLines = new THREE.LineSegments(secGeo, lineMat("#a0bee6", 0.07));
  secLines.frustumCulled = false;
  world.add(secLines);

  // ── lanes: curtain walls + top face + edge wires + threshold plane ──────────
  const COL = {};
  for (const L of LANES) COL[L.key] = new THREE.Color(L.hue);
  const WHITE = new THREE.Color(1, 1, 1);
  const tmp = new THREE.Color();

  function strip(vertsPerSample, material) {
    const geo = track(new THREE.BufferGeometry());
    const pos = new Float32Array(MAXN * vertsPerSample * 3);
    const col = new Float32Array(MAXN * vertsPerSample * 4);
    geo.setAttribute("position", new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage));
    geo.setAttribute("color", new THREE.BufferAttribute(col, 4).setUsage(THREE.DynamicDrawUsage));
    const idx = [];
    for (let i = 0; i < MAXN - 1; i++) {
      const a = i * 2;
      const b = a + 2;
      idx.push(a, a + 1, b, a + 1, b + 1, b);
    }
    geo.setIndex(idx);
    geo.setDrawRange(0, 0);
    const mesh = new THREE.Mesh(geo, material);
    mesh.frustumCulled = false;
    return { mesh, pos, col, geo };
  }
  const meshMat = (order) =>
    track(new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, side: THREE.DoubleSide, depthWrite: false }));

  const lanes = LANES.map((L) => {
    // top face needs L/R per sample (2 verts), each curtain needs top/bottom (2 verts)
    const top = strip(2, meshMat());
    const wallL = strip(2, meshMat());
    const wallR = strip(2, meshMat());
    top.mesh.renderOrder = 6;
    wallL.mesh.renderOrder = 4;
    wallR.mesh.renderOrder = 4;
    world.add(wallL.mesh, wallR.mesh, top.mesh);

    const wire = (offsetX) => {
      const g = track(new THREE.BufferGeometry());
      const p = new Float32Array(MAXN * 3);
      const c = new Float32Array(MAXN * 4);
      g.setAttribute("position", new THREE.BufferAttribute(p, 3).setUsage(THREE.DynamicDrawUsage));
      g.setAttribute("color", new THREE.BufferAttribute(c, 4).setUsage(THREE.DynamicDrawUsage));
      g.setDrawRange(0, 0);
      const line = new THREE.Line(g, track(new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false })));
      line.frustumCulled = false;
      line.renderOrder = 8;
      world.add(line);
      return { line, p, c, g, offsetX };
    };
    const wires = [wire(-LANE_W / 2), wire(LANE_W / 2), wire(0)];

    // threshold plane
    const plane = new THREE.Mesh(
      track(new THREE.PlaneGeometry(LANE_W + 0.5, DEPTH)),
      track(new THREE.MeshBasicMaterial({ color: L.hue, transparent: true, opacity: 0.1, depthWrite: false, side: THREE.DoubleSide })),
    );
    plane.rotation.x = -Math.PI / 2;
    plane.renderOrder = 2;
    world.add(plane);
    const hw = (LANE_W + 0.5) / 2;
    const outline = new THREE.LineLoop(
      track(
        new THREE.BufferGeometry().setFromPoints([
          new THREE.Vector3(-hw, 0, 0), new THREE.Vector3(hw, 0, 0), new THREE.Vector3(hw, 0, -DEPTH), new THREE.Vector3(-hw, 0, -DEPTH),
        ]),
      ),
      lineMat(L.hue, 0.55, true),
    );
    outline.computeLineDistances();
    world.add(outline);
    const tag = makeLabel("", { color: L.hue, size: 24, weight: 600, align: "left" });
    world.add(tag);

    return { L, top, wallL, wallR, wires, plane, outline, tag, flash: 0, thrShown: null };
  });

  // ── rings: expanding bursts + small pins on crossing points ─────────────────
  const ringGeo = track(new THREE.RingGeometry(0.5, 0.58, 64));
  ringGeo.rotateX(-Math.PI / 2);
  const pinGeo = track(new THREE.RingGeometry(0.16, 0.26, 28));
  pinGeo.rotateX(-Math.PI / 2);
  const mkRing = (geo, hue) => {
    const m = new THREE.Mesh(
      geo,
      track(new THREE.MeshBasicMaterial({ color: hue, transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide })),
    );
    m.visible = false;
    m.renderOrder = 9;
    world.add(m);
    return m;
  };
  const bursts = Array.from({ length: 10 }, () => ({ mesh: mkRing(ringGeo, "#ffffff"), on: false }));
  const pins = Array.from({ length: 24 }, () => ({ mesh: mkRing(pinGeo, "#ffffff"), on: false }));
  const seenCross = new WeakSet();
  let pinIx = 0;
  let burstIx = 0;

  function spawnCross(c, now) {
    const lane = lanes.find((l) => l.L.key === c.lane);
    if (!lane) return;
    const thr = buf.thr[lane.L.thr];
    const y = thr * H;
    const b = bursts[burstIx++ % bursts.length];
    b.on = true;
    b.born = performance.now();
    b.t0 = c.t;
    b.lane = lane;
    b.y = y;
    b.mesh.material.color.set(lane.L.hue);
    b.mesh.visible = true;
    const p = pins[pinIx++ % pins.length];
    p.on = true;
    p.t0 = c.t;
    p.lane = lane;
    p.y = y;
    p.mesh.material.color.set(lane.L.hue);
    p.mesh.visible = true;
    lane.flash = 1;
  }

  // ── event curtains (squeeze / open hand / brace / arm stopped, from runtime events) ──
  const evMarks = new Map(); // seq -> { group, label, t }
  function syncEvents() {
    const events = getEvents();
    const keep = new Set();
    for (const e of events) {
      keep.add(e.seq);
      if (evMarks.has(e.seq)) continue;
      const st = EVENT_STYLE[e.kind];
      if (!st) continue;
      const g = new THREE.Group();
      // a bright line across the floor at the event's time, a stem up the left wall, and a flag
      const edge = new THREE.Line(
        track(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(X0, 0.02, 0), new THREE.Vector3(X1, 0.02, 0)])),
        lineMat(st.hue, 0.85),
      );
      const top = H * 0.62 + st.lift * 0.9;
      const stem = new THREE.Line(
        track(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(X0, 0.02, 0), new THREE.Vector3(X0, top, 0)])),
        lineMat(st.hue, 0.7),
      );
      const label = makeLabel(st.text, { color: st.hue, size: 20, weight: 600, align: "left" });
      label.position.set(X0 + 0.15, top, 0);
      g.add(edge, stem, label);
      g.renderOrder = 3;
      world.add(g);
      evMarks.set(e.seq, { g, t: e.t, label });
    }
    for (const [seq, m] of evMarks) {
      if (!keep.has(seq)) {
        world.remove(m.g);
        m.label.material.map.dispose();
        m.label.material.dispose();
        evMarks.delete(seq);
      }
    }
  }

  // ── per-frame fill ──────────────────────────────────────────────────────────
  function fillLane(lane, rows, now, bright) {
    const key = lane.L.key;
    const base = COL[key];
    const thr = buf.thr[lane.L.thr];
    const n = Math.min(rows.length, MAXN);
    const { top, wallL, wallR, wires } = lane;
    let kEff = 0;
    for (let i = 0; i < n; i++) {
      const r = rows[i];
      const age = now - r.t;
      const z = -age * ZS;
      const lv = r[key];
      const y = Math.min(lv, 1.3) * H;
      const fade = Math.pow(clamp(1 - age / WINDOW_S), 0.75);
      const over = !r.gap && lv >= thr;
      // top face
      const xl = lane.L.x - LANE_W / 2;
      const xr = lane.L.x + LANE_W / 2;
      let o = i * 6;
      top.pos[o] = xl; top.pos[o + 1] = y; top.pos[o + 2] = z;
      top.pos[o + 3] = xr; top.pos[o + 4] = y; top.pos[o + 5] = z;
      tmp.copy(base).lerp(WHITE, over ? 0.5 : 0.12 + lv * 0.2);
      o = i * 8;
      const a = (over ? 0.8 : 0.5) * fade;
      top.col[o] = tmp.r; top.col[o + 1] = tmp.g; top.col[o + 2] = tmp.b; top.col[o + 3] = a;
      top.col[o + 4] = tmp.r; top.col[o + 5] = tmp.g; top.col[o + 6] = tmp.b; top.col[o + 7] = a;
      // curtains (top vertex, bottom vertex)
      for (const [w, x] of [[wallL, xl], [wallR, xr]]) {
        const p = i * 6;
        w.pos[p] = x; w.pos[p + 1] = y; w.pos[p + 2] = z;
        w.pos[p + 3] = x; w.pos[p + 4] = 0; w.pos[p + 5] = z;
        const c = i * 8;
        w.col[c] = base.r; w.col[c + 1] = base.g; w.col[c + 2] = base.b; w.col[c + 3] = (0.34 + lv * 0.3) * fade;
        w.col[c + 4] = base.r; w.col[c + 5] = base.g; w.col[c + 6] = base.b; w.col[c + 7] = 0;
      }
      // edge wires
      for (const wr of wires) {
        const p = i * 3;
        wr.p[p] = lane.L.x + wr.offsetX; wr.p[p + 1] = y; wr.p[p + 2] = z;
        const c = i * 4;
        const centre = wr.offsetX === 0;
        tmp.copy(base).lerp(WHITE, over ? 0.7 : centre ? 0.35 : 0.1);
        wr.c[c] = tmp.r; wr.c[c + 1] = tmp.g; wr.c[c + 2] = tmp.b; wr.c[c + 3] = (centre ? 1 : 0.7) * fade;
      }
      kEff = i + 1;
    }
    const cnt = Math.max(0, (kEff - 1) * 6);
    for (const s of [top, wallL, wallR]) {
      s.geo.setDrawRange(0, cnt);
      s.geo.attributes.position.needsUpdate = true;
      s.geo.attributes.color.needsUpdate = true;
    }
    for (const wr of wires) {
      wr.g.setDrawRange(0, kEff);
      wr.g.attributes.position.needsUpdate = true;
      wr.g.attributes.color.needsUpdate = true;
    }
    // threshold plane + label follow the (possibly recalibrated) threshold
    const y = thr * H;
    lane.plane.position.set(lane.L.x, y, -DEPTH / 2);
    lane.outline.position.set(lane.L.x, y, 0);
    const shown = `${lane.L.thrName} ${Math.round(thr * 100)}%`;
    lane.tag.userData.set(shown, lane.L.hue);
    lane.tag.position.set(lane.L.x + LANE_W / 2 + 0.22, y + 0.02, -0.25);
    lane.tag.center.set(0, 0.5);
    const isOver = buf.over[key];
    const target = (isOver ? 0.2 : 0.085) + lane.flash * 0.22;
    lane.plane.material.opacity = damp(lane.plane.material.opacity, target, 14, bright.dt);
    lane.outline.material.opacity = isOver ? 0.95 : 0.5;
    lane.flash = Math.max(0, lane.flash - bright.dt * 1.6);
  }

  // ── resize ──────────────────────────────────────────────────────────────────
  let W = 1;
  let Hh = 1;
  function resize() {
    const r = host.getBoundingClientRect();
    W = Math.max(2, Math.round(r.width));
    Hh = Math.max(2, Math.round(r.height));
    renderer.setSize(W, Hh, false);
    camera.aspect = W / Hh;
    // portrait panels: a wider lens keeps all three lanes in frame without standing miles away
    camera.fov = camera.aspect < 1.5 ? Math.min(64, 36 + (1.5 - camera.aspect) * 36) : 36;
    camera.updateProjectionMatrix();
  }
  const ro = new ResizeObserver(resize);
  ro.observe(host);
  resize();

  // ── frame ───────────────────────────────────────────────────────────────────
  let remove = null;
  let t0 = performance.now();
  function frame(dt, tSec) {
    const now = buf.clock();
    // camera
    const idle = performance.now() - lastInteract > 2500 && !dragging;
    const drift = reduced || !idle ? 0 : Math.sin(tSec * 0.17) * 0.05;
    cam.az = damp(cam.az, cam.tAz, 8, dt);
    cam.pol = damp(cam.pol, cam.tPol, 8, dt);
    cam.rad = damp(cam.rad, cam.tRad, 8, dt);
    // narrow panels need to stand further back to keep the three lanes in frame
    const fitRad = cam.rad * (camera.aspect < 1.5 ? 1.12 : 1);
    const az = cam.az + drift;
    camera.position.set(
      target.x + fitRad * Math.sin(cam.pol) * Math.sin(az),
      target.y + fitRad * Math.cos(cam.pol),
      target.z + fitRad * Math.sin(cam.pol) * Math.cos(az),
    );
    camera.lookAt(target);

    // data
    for (const lane of lanes) fillLane(lane, buf.rows, now, { dt });

    // scrolling 1 s floor lines
    const top = Math.floor(now);
    for (let i = 0; i < NSEC; i++) {
      const z = -(now - (top - i)) * ZS;
      const o = i * 6;
      secPos[o] = X0; secPos[o + 1] = 0.003; secPos[o + 2] = z;
      secPos[o + 3] = X1; secPos[o + 4] = 0.003; secPos[o + 5] = z;
    }
    secGeo.attributes.position.needsUpdate = true;

    // crossings -> rings
    for (const c of buf.crossings) {
      if (seenCross.has(c)) continue;
      seenCross.add(c);
      spawnCross(c, now);
    }
    const perf = performance.now();
    for (const b of bursts) {
      if (!b.on) continue;
      const k = (perf - b.born) / (reduced ? 500 : 1300);
      if (k >= 1) {
        b.on = false;
        b.mesh.visible = false;
        continue;
      }
      const e = 1 - (1 - k) ** 3;
      const s = 1 + e * 3.2;
      b.mesh.scale.set(s, 1, s);
      b.mesh.position.set(b.lane.L.x, b.y, -(now - b.t0) * ZS);
      b.mesh.material.opacity = (1 - k) * 0.85;
    }
    for (const p of pins) {
      if (!p.on) continue;
      const age = now - p.t0;
      if (age > WINDOW_S) {
        p.on = false;
        p.mesh.visible = false;
        continue;
      }
      p.mesh.position.set(p.lane.L.x, p.y, -age * ZS);
      p.mesh.material.opacity = 0.9 * Math.pow(clamp(1 - age / WINDOW_S), 0.75);
    }
    for (const m of evMarks.values()) {
      const age = now - m.t;
      m.g.position.z = -age * ZS;
      m.g.visible = age >= -0.2 && age <= WINDOW_S;
    }
    renderer.render(scene, camera);
  }

  return {
    syncEvents,
    enter() {
      resize();
      syncEvents();
      remove ||= loop.add(frame);
    },
    leave() {
      remove?.();
      remove = null;
    },
    resize,
    dispose() {
      remove?.();
      ro.disconnect();
      dom.removeEventListener("pointerdown", onDown);
      dom.removeEventListener("pointermove", onMove);
      dom.removeEventListener("pointerup", onUp);
      dom.removeEventListener("pointercancel", onUp);
      dom.removeEventListener("wheel", onWheel);
      dom.removeEventListener("dblclick", onReset);
      scene.traverse((o) => {
        if (o.material?.map) o.material.map.dispose();
        if (o.material?.dispose) o.material.dispose();
        if (o.geometry?.dispose) o.geometry.dispose();
      });
      for (const d of disposables) d.dispose?.();
      renderer.dispose();
      renderer.forceContextLoss?.();
      dom.remove();
    },
  };
}
