// The five portal emblems. Small wireframe objects, each its own shape, each wired to a little real data:
//   stage    a table + 2-link arm whose joints follow arm.joints
//   flow     heard -> gate -> arm; a packet runs when a decision lands (fire())
//   signals  a mini oscilloscope of the real EMG trace
//   learn    a knot that quickens while a rule waits for approval
//   lab      a flask; a red dot while a session is recording
//   replay   (replay mode only, in Lab's slot) a play triangle in a ring; the ring fills with the scenario's finished steps
// Each emblem returns { object, update(state), setGlow(hover, t, k), fire?(), accent, dispose }.

const INK = 0xaab6c6;
const GRIP = 0x4fd8ff, OPEN = 0xffc15a, BRACE = 0xff4d5e, MUSE = 0xa58bff, OK = 0x5af0a4;
const rad = (d) => (d * Math.PI) / 180;
const clamp = (v, lo = 0, hi = 1) => Math.min(hi, Math.max(lo, v));

export function buildEmblems(THREE, track, ids = ["stage", "flow", "signals", "learn", "lab"]) {
  const col = (hex) => new THREE.Color(hex);

  function kit() {
    const mats = [];
    const line = (hex, o) => {
      const m = track(new THREE.LineBasicMaterial({ color: hex, transparent: true, opacity: o, blending: THREE.AdditiveBlending, depthWrite: false }));
      mats.push({ m, o, base: col(hex), hex });
      return m;
    };
    const edges = (geo, hex, o = 0.85, thr = 20) => {
      track(geo);
      const eg = track(new THREE.EdgesGeometry(geo, thr));
      return new THREE.LineSegments(eg, line(hex, o));
    };
    const wire = (geo, hex, o = 0.5) => {
      track(geo);
      const wg = track(new THREE.WireframeGeometry(geo));
      return new THREE.LineSegments(wg, line(hex, o));
    };
    const glow = (h, k, boost = 1) => {
      for (const x of mats) {
        x.m.opacity = x.o * (0.7 + 0.9 * h) * k * boost;
        x.m.color.copy(x.base).lerp(WHITE, h * 0.35);
      }
    };
    const recolor = (entry, hex) => {
      entry.base.set(hex);
    };
    return { mats, line, edges, wire, glow, recolor };
  }
  const WHITE = new THREE.Color(0xffffff);

  // ── stage ──────────────────────────────────────────────────────────────────
  function stage() {
    const k = kit();
    const object = new THREE.Group();
    const disc = k.edges(new THREE.CylinderGeometry(0.66, 0.66, 0.05, 40), INK, 0.7, 10);
    disc.position.y = -0.34;
    object.add(disc);
    const cube = k.edges(new THREE.BoxGeometry(0.17, 0.17, 0.17), OPEN, 0.9);
    cube.position.set(0.3, -0.22, 0.12);
    object.add(cube);

    const armMats = [];
    const base = new THREE.Group();
    base.position.set(-0.36, -0.32, 0);
    const joint = (parent, len, w, y) => {
      const g = new THREE.Group();
      g.position.y = y;
      const l = k.edges(new THREE.BoxGeometry(w, len, w), INK, 0.95);
      l.position.y = len / 2;
      g.add(l);
      parent.add(g);
      armMats.push(l.material);
      return g;
    };
    const j1 = joint(base, 0.5, 0.09, 0);
    const j2 = joint(j1, 0.4, 0.075, 0.5);
    const j3 = joint(j2, 0.14, 0.12, 0.4);
    const tip = k.wire(new THREE.OctahedronGeometry(0.06, 0), INK, 0.9);
    tip.position.y = 0.18;
    j3.add(tip);
    object.add(base);
    object.scale.setScalar(0.95);
    object.position.y = 0.05;

    const tgt = { a1: -0.35, a2: -1.2, a3: 0 };
    const cur = { a1: -0.35, a2: -1.2, a3: 0 };
    const armEntries = k.mats.filter((x) => armMats.includes(x.m));
    const em = {
      object,
      accent: INK,
      update(s) {
        if (!s.arm) return; // replay with no simulation: keep the last pose
        const j = s.arm.joints || {};
        // schematic mapping of the real joint readings (degrees) onto the 2-link silhouette
        tgt.a1 = clamp(-(0.35 + rad((j.shoulder_lift ?? -90) + 90) * 0.9), -1.6, 0.5);
        tgt.a2 = clamp(-(1.15 - rad((j.elbow_flex ?? 90) - 90) * 0.9), -2.2, 0.4);
        tgt.a3 = rad((j.wrist_flex ?? 60) - 60) * 0.5;
        const st = s.arm.state;
        const hex = st === "moving" || st === "ready" ? OK : st === "disarmed" ? 0x6c7a8d : BRACE;
        armEntries.forEach((e) => k.recolor(e, hex));
        em.accent = st === "stopped" || st === "fault" ? BRACE : st === "disarmed" ? INK : OK;
      },
      setGlow(h, t, kk) {
        const d = 1 - Math.exp(-0.15 * 60 * 0.016);
        cur.a1 += (tgt.a1 - cur.a1) * d;
        cur.a2 += (tgt.a2 - cur.a2) * d;
        cur.a3 += (tgt.a3 - cur.a3) * d;
        j1.rotation.z = cur.a1;
        j2.rotation.z = cur.a2;
        j3.rotation.z = cur.a3;
        k.glow(h, kk);
      },
    };
    return em;
  }

  // ── flow ───────────────────────────────────────────────────────────────────
  function flow() {
    const k = kit();
    const object = new THREE.Group();
    const A = new THREE.Vector3(-0.5, 0, 0), G = new THREE.Vector3(0, 0, 0), P = new THREE.Vector3(0.5, 0.22, 0), Q = new THREE.Vector3(0.5, -0.22, 0);
    const heard = k.edges(new THREE.BoxGeometry(0.2, 0.2, 0.2), INK, 0.9);
    heard.position.copy(A);
    const gate = k.edges(new THREE.OctahedronGeometry(0.17, 0), INK, 0.95);
    gate.position.copy(G);
    const arm = k.edges(new THREE.BoxGeometry(0.2, 0.2, 0.2), INK, 0.9);
    arm.position.copy(P);
    const hold = k.edges(new THREE.BoxGeometry(0.14, 0.14, 0.14), 0x6c7a8d, 0.6);
    hold.position.copy(Q);
    const link = (a, b, o = 0.5) => {
      const g = track(new THREE.BufferGeometry().setFromPoints([a, b]));
      return new THREE.Line(g, k.line(INK, o));
    };
    object.add(heard, gate, arm, hold, link(A, G), link(G, P), link(G, Q, 0.28));
    const dot = new THREE.Mesh(track(new THREE.SphereGeometry(0.055, 10, 8)), track(new THREE.MeshBasicMaterial({ color: OK, transparent: true, opacity: 0, blending: THREE.AdditiveBlending })));
    object.add(dot);
    let ft = 1;
    let fcol = OK;
    const path = [A, G, P];
    return {
      object,
      accent: INK,
      update() {},
      fire(hex = OK) {
        ft = 0;
        fcol = hex;
      },
      setGlow(h, t, kk) {
        k.glow(h, kk);
        if (ft < 1) {
          ft = Math.min(1, ft + 0.016 / 1.1);
          const u = ft * 2;
          const i = Math.min(1, Math.floor(u));
          dot.position.lerpVectors(path[i], path[i + 1], u - i);
          dot.material.color.set(fcol);
          dot.material.opacity = 0.95;
          gate.scale.setScalar(1 + 0.4 * Math.max(0, 1 - Math.abs(u - 1) * 3));
        } else dot.material.opacity = 0;
      },
    };
  }

  // ── signals ────────────────────────────────────────────────────────────────
  function signals() {
    const k = kit();
    const object = new THREE.Group();
    const W = 1.12, H = 0.7;
    const frame = new THREE.LineLoop(
      track(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-W / 2, -H / 2, 0), new THREE.Vector3(W / 2, -H / 2, 0), new THREE.Vector3(W / 2, H / 2, 0), new THREE.Vector3(-W / 2, H / 2, 0)])),
      k.line(INK, 0.45),
    );
    const grid = new THREE.LineSegments(
      track(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-W / 2, 0, 0), new THREE.Vector3(W / 2, 0, 0)])),
      k.line(INK, 0.18),
    );
    object.add(frame, grid);
    const M = 56;
    const mk = (hex, o) => {
      const g = track(new THREE.BufferGeometry());
      g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(M * 3), 3));
      const l = new THREE.Line(g, k.line(hex, o));
      l.frustumCulled = false;
      object.add(l);
      return l;
    };
    const lg = mk(GRIP, 1), lo = mk(OPEN, 1), lb = mk(BRACE, 0.95);
    const fill = (line, sel, y0, amp) => (tr) => {
      const p = line.geometry.attributes.position.array;
      for (let i = 0; i < M; i++) {
        const idx = tr.length ? Math.round((i / (M - 1)) * (tr.length - 1)) : 0;
        const v = tr.length ? sel(tr[idx]) : 0;
        p[i * 3] = -W / 2 + 0.04 + (i / (M - 1)) * (W - 0.08);
        p[i * 3 + 1] = y0 + clamp(v) * amp;
        p[i * 3 + 2] = 0.01;
      }
      line.geometry.attributes.position.needsUpdate = true;
    };
    const fg = fill(lg, (r) => r[1], -H / 2 + 0.04, H - 0.1);
    const fo = fill(lo, (r) => r[2], -H / 2 + 0.04, H - 0.1);
    const fb = fill(lb, (r) => r[1], -H / 2 + 0.04, H - 0.1);
    fg([]); fo([]); fb([]);
    object.scale.setScalar(0.95);
    const em = {
      object,
      accent: INK,
      update(s) {
        const e = s.emg;
        fg(e?.trace || []);
        fo(e?.trace || []);
        fb((e?.brace_trace || []).map((r) => [r[0], r[1]]));
        em.accent = !e ? INK : e.brace >= (e.brace_effort || 0.35) ? BRACE : e.effort > 0.05 ? GRIP : e.extensor > 0.05 ? OPEN : INK;
      },
      setGlow(h, t, kk) {
        k.glow(h, kk);
      },
    };
    return em;
  }

  // ── learn ──────────────────────────────────────────────────────────────────
  function learn() {
    const k = kit();
    const object = new THREE.Group();
    const knot = k.wire(new THREE.TorusKnotGeometry(0.3, 0.075, 90, 7, 2, 3), MUSE, 0.5);
    object.add(knot);
    const core = k.edges(new THREE.IcosahedronGeometry(0.1, 0), MUSE, 0.95);
    object.add(core);
    let waiting = false;
    return {
      object,
      accent: MUSE,
      update(s) {
        waiting = !!s.learning?.proposal;
      },
      setGlow(h, t, kk) {
        k.glow(h, kk, waiting ? 1.5 : 1);
        knot.rotation.x += waiting ? 0.03 : 0.004;
        knot.rotation.z += waiting ? 0.02 : 0.003;
        const s = waiting ? 1 + 0.08 * Math.sin(t * 6) : 1;
        object.scale.setScalar(s);
      },
    };
  }

  // ── lab ────────────────────────────────────────────────────────────────────
  function lab() {
    const k = kit();
    const object = new THREE.Group();
    const prof = [[0.09, 0.5], [0.09, 0.2], [0.42, -0.4], [0.44, -0.46], [0.4, -0.5], [0, -0.5]].map(([x, y]) => new THREE.Vector2(x, y));
    const flask = k.wire(new THREE.LatheGeometry(prof, 18), INK, 0.5);
    const rim = k.edges(new THREE.CylinderGeometry(0.13, 0.13, 0.04, 16), INK, 0.9);
    rim.position.y = 0.52;
    const liquid = k.wire(new THREE.LatheGeometry([[0.26, -0.18], [0.41, -0.4], [0.43, -0.45], [0.38, -0.47], [0, -0.47]].map(([x, y]) => new THREE.Vector2(x, y)), 14), GRIP, 0.55);
    const rec = new THREE.Mesh(track(new THREE.SphereGeometry(0.07, 12, 10)), track(new THREE.MeshBasicMaterial({ color: BRACE, transparent: true, opacity: 0 })));
    rec.position.set(0.0, 0.7, 0);
    object.add(flask, rim, liquid, rec);
    object.position.y = -0.02;
    let recording = false;
    const em = {
      object,
      accent: INK,
      update(s) {
        recording = !!s.session?.session_id;
        em.accent = recording ? BRACE : s.emg?.calibrated ? OK : INK;
      },
      setGlow(h, t, kk) {
        k.glow(h, kk);
        rec.material.opacity = recording ? (0.5 + 0.5 * Math.sin(t * 5)) * kk : 0;
      },
    };
    return em;
  }

  // ── replay ─────────────────────────────────────────────────────────────────
  function replay() {
    const k = kit();
    const object = new THREE.Group();
    const R = 0.52, N = 72;
    const circle = (r) => Array.from({ length: N }, (_, i) => new THREE.Vector3(r * Math.cos((i / N) * Math.PI * 2), r * Math.sin((i / N) * Math.PI * 2), 0));
    object.add(new THREE.LineLoop(track(new THREE.BufferGeometry().setFromPoints(circle(R))), k.line(INK, 0.42)));
    // progress arc: clockwise from 12 o'clock, drawn only as far as the scenario's finished steps
    const pos = new Float32Array((N + 1) * 3);
    for (let i = 0; i <= N; i++) {
      const th = Math.PI / 2 - (i / N) * Math.PI * 2;
      pos.set([R * Math.cos(th), R * Math.sin(th), 0.01], i * 3);
    }
    const ag = track(new THREE.BufferGeometry());
    ag.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    ag.setDrawRange(0, 0);
    const arcEntry = k.mats.length;
    const arc = new THREE.Line(ag, k.line(OK, 1));
    arc.frustumCulled = false;
    object.add(arc);
    const tri = new THREE.LineLoop(track(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-0.13, -0.22, 0), new THREE.Vector3(-0.13, 0.22, 0), new THREE.Vector3(0.24, 0, 0)])), k.line(INK, 0.95));
    object.add(tri);
    object.scale.setScalar(1.05);
    const triEntry = k.mats[k.mats.length - 1];
    const em = {
      object,
      accent: INK,
      update(s) {
        const sc = s.scenario;
        const total = sc?.steps?.length || 0;
        const done = sc ? (sc.state === "done" ? total : sc.steps.filter((x) => x.status === "done").length) : 0;
        ag.setDrawRange(0, total ? Math.max(0, Math.round((done / total) * N) + (done ? 1 : 0)) : 0);
        const hex = !sc ? INK : sc.state === "running" ? OK : sc.state === "done" ? MUSE : sc.state === "idle" ? INK : BRACE;
        k.recolor(k.mats[arcEntry], hex);
        k.recolor(triEntry, sc?.state === "running" ? OK : INK);
        em.accent = hex;
      },
      setGlow(h, t, kk) {
        k.glow(h, kk);
      },
    };
    return em;
  }

  const BUILD = { stage, flow, signals, learn, lab, replay };
  return ids.map((id) => BUILD[id]());
}
