// Pure data shaping for the Learning section: incident meta -> replay model, rule wording.
// Nothing here draws. Everything is derived from /api/incident/<id>, the runtime event log and /api/rules.

export const HOLD = new Set(["grasp", "carry", "release"]); // stages where the gripper holds the object
export const WORLD = 3.2; // 3D world units across the table (normalized 0..1)
const rad = (d) => (d * Math.PI) / 180;

// Schematic SO-101 kinematics (planar two-link + pan). Link lengths and the shoulder height are
// approximations: the joint DEGREES are real, the metres are not calibrated. The UI says so.
const L1 = 0.116;
const L2 = 0.135;
export const SHOULDER_H = 0.09;
const H0 = SHOULDER_H;
const DEFAULT_SCALE = 2.06; // table units per metre when there is no grasp frame to anchor on

export function kin(q) {
  const e1 = rad(-q[1]);
  const e2 = e1 - rad(q[2]);
  return { pan: rad(q[0]), r1: L1 * Math.cos(e1), h1: H0 + L1 * Math.sin(e1), r: L1 * Math.cos(e1) + L2 * Math.cos(e2), h: H0 + L1 * Math.sin(e1) + L2 * Math.sin(e2) };
}

const lerp = (a, b, t) => a + (b - a) * t;
const num = (v) => (Number.isFinite(v) ? v : null);

/** Find the runtime events that belong to an incident (matched by time, they share the freeze moment). */
export function findEvents(history, t) {
  const near = (e, kind, w = 1.2) => e.kind === kind && Math.abs(e.t - t) < w;
  const brace = [...history].reverse().find((e) => near(e, "emg.brace"));
  const open = [...history].reverse().find((e) => near(e, "emg.open_hand"));
  const stopped = [...history].reverse().find((e) => near(e, "arm.stopped"));
  const triggered = [...history].reverse().find((e) => near(e, "incident.triggered"));
  const after = (kind) => history.find((e) => e.kind === kind && e.t >= t - 0.2);
  return { brace, open, stopped, triggered, saved: after("incident.saved"), explained: after("incident.explained") };
}

/**
 * Build the replay model.
 * meta:  GET /api/incident/<id> .incident  (joints, contracts, objects, held, t_freeze)
 * ev:    findEvents() result (brace trace, onset, stop)
 */
export function buildModel(meta, ev = {}) {
  if (!meta || !Array.isArray(meta.joints)) return null;
  const tF = Number(meta.t_freeze);
  const stageAt = new Map((meta.contracts || []).map((c) => [c.t, c.contract]));
  const objects = (meta.objects || []).filter((o) => o.table_xy).map((o) => ({ id: o.id, name: o.name || o.id, x: o.table_xy[0], y: o.table_xy[1] }));
  const zones = (meta.objects || []).filter((o) => o.kind === "avoid_zone" && Array.isArray(o.table_box)).map((o) => ({ name: o.name || o.id, box: o.table_box }));

  // raw samples, stage carried forward once seen (contracts are only recorded while moving)
  let lastStage = null;
  let lastContract = null;
  const raw = meta.joints
    .filter((j) => Array.isArray(j.q) && j.q.length >= 6)
    .map((j) => {
      const c = stageAt.get(j.t);
      if (c) {
        lastStage = c.stage || lastStage;
        lastContract = c;
      }
      return { abs: j.t, t: j.t - tF, q: j.q, stage: c ? c.stage || null : null, held: lastStage, contract: c || lastContract };
    });
  if (raw.length < 2) return { id: meta.id, empty: true, zones, objects, held: meta.held, tFreeze: tF, samples: [] };

  // anchor the schematic arm to the picked object so the path lands where the object really is
  let base = { x: 0.5, y: 0.1 };
  let scale = DEFAULT_SCALE;
  let anchored = false;
  const g = raw.find((s) => s.stage === "grasp" && s.contract?.object_id);
  const pick = g ? objects.find((o) => o.id === g.contract.object_id) : null;
  if (g && pick) {
    const k = kin(g.q);
    const sp = Math.sin(k.pan);
    if (Math.abs(sp) > 0.15 && k.r > 0.05) {
      const s = (pick.x - 0.5) / (k.r * sp);
      if (s > 1.2 && s < 4) scale = s;
    }
    base = { x: 0.5, y: pick.y - scale * k.r * Math.cos(k.pan) };
    anchored = true;
  }
  const at = (q) => {
    const k = kin(q);
    const sx = Math.sin(k.pan);
    const cy = Math.cos(k.pan);
    return {
      x: base.x + scale * k.r * sx, y: base.y + scale * k.r * cy, h: scale * k.h,
      ex: base.x + scale * k.r1 * sx, ey: base.y + scale * k.r1 * cy, eh: scale * k.h1,
    };
  };
  const samples = raw.map((s, i) => {
    const p = at(s.q);
    const prev = i ? raw[i - 1] : null;
    let v = 0;
    if (prev && s.t > prev.t) {
      let d = 0;
      for (let j = 0; j < 5; j++) d += Math.abs(s.q[j] - prev.q[j]);
      v = d / (s.t - prev.t);
    }
    return { ...s, ...p, v };
  });
  // smooth the joint-speed trace a touch (3-tap)
  const speed = samples.map((s, i) => ({ t: s.t, v: (samples[Math.max(0, i - 1)].v + s.v + samples[Math.min(samples.length - 1, i + 1)].v) / 3 }));
  let L = 0;
  samples.forEach((s, i) => {
    if (i) L += Math.hypot(s.x - samples[i - 1].x, s.y - samples[i - 1].y, s.h - samples[i - 1].h);
    s.L = L;
  });

  // stage bands
  const bands = [];
  samples.forEach((s, i) => {
    if (!s.stage) return;
    const last = bands[bands.length - 1];
    const end = samples[i + 1] ? samples[i + 1].t : s.t;
    if (last && last.stage === s.stage && s.t - last.t1 < 0.6) last.t1 = end;
    else bands.push({ stage: s.stage, t0: s.t, t1: end });
  });

  // brace event: onset, fast-window trace, real software stop latency
  const b = ev.brace;
  const trig = ev.triggered;
  const onsetAbs = num(b?.onset_t) ?? num(trig?.onset_t);
  const emg = b && Array.isArray(b.trace) && b.trace.length ? { rows: b.trace.map((r) => ({ t: r[0] - tF, f: r[1], e: r[2], b: r[3] })), detectMs: b.detect_ms, peaks: { f: b.peak_flexor, e: b.peak_extensor, b: b.peak_brace }, trigger: b.trigger } : null;
  const stop = ev.stopped ? { t: ev.stopped.t - tF, latencyMs: onsetAbs != null ? Math.max(0, Math.round((ev.stopped.t - onsetAbs) * 1000)) : null, stage: ev.stopped.stage, reason: ev.stopped.reason } : null;
  const onset = onsetAbs != null ? onsetAbs - tF : null;

  const t0 = Math.min(samples[0].t, emg ? emg.rows[0].t : 0, -0.5);
  const t1 = Math.max(samples[samples.length - 1].t, 0.4);

  // distinct path points for the ribbon
  const pts = [];
  samples.forEach((s) => {
    const l = pts[pts.length - 1];
    if (!l || Math.hypot(s.x - l.x, s.y - l.y, s.h - l.h) > 0.006) pts.push({ x: s.x, y: s.y, h: s.h });
  });

  const model = {
    id: meta.id, tFreeze: tF, held: meta.held, objects, zones, samples, speed, bands, range: [t0, t1], base, scale, anchored, pick: pick?.id || null,
    emg, stop, onset, pts, length: L, route: samples.find((s) => s.contract?.route)?.contract.route || null, tags: samples.find((s) => s.contract?.tags)?.contract.tags || [],
    objectName: samples.find((s) => s.contract?.object_name)?.contract.object_name || null,
    pickId: samples.find((s) => s.contract?.object_id)?.contract.object_id || null,
  };

  /** Pose of the arm at relative time t (seconds, 0 = freeze). */
  model.poseAt = (t) => {
    const S = samples;
    let i = 0;
    let lo = 0;
    let hi = S.length - 1;
    if (t <= S[0].t) i = 0;
    else if (t >= S[hi].t) i = hi;
    else {
      while (hi - lo > 1) {
        const m = (lo + hi) >> 1;
        if (S[m].t <= t) lo = m;
        else hi = m;
      }
      i = lo;
    }
    const a = S[i];
    const bb = S[Math.min(S.length - 1, i + 1)];
    const k = bb.t > a.t ? Math.min(1, Math.max(0, (t - a.t) / (bb.t - a.t))) : 0;
    const q = a.q.map((v, j) => lerp(v, bb.q[j], k));
    const p = at(q);
    return { ...p, q, stage: a.held, holding: HOLD.has(a.held), moving: a.stage != null, L: lerp(a.L, bb.L, k), t };
  };
  model.freezePose = model.poseAt(0);
  model.onsetPose = onset != null ? model.poseAt(onset) : null;
  return model;
}

// ── rules: wording ──────────────────────────────────────────────────────────
const RESP = {
  route_around: { kind: "Route around", effect: (r) => `Blocks any route that crosses the ${r.avoid}`, does: (r) => `take a route that stays clear of the ${r.avoid}` },
  lift_higher: { kind: "Lift higher", effect: () => "Only routes taught as high routes stay allowed", does: (r) => `carry it higher, over the ${r.avoid}` },
  slow_down: { kind: "Slow down", effect: () => "Caps the arm speed for the whole carry", does: (r) => `move slower (learned near the ${r.avoid})` },
  grip_gentler: { kind: "Grip gentler", effect: () => "Caps the grip force for this object", does: (r) => `hold it more gently (learned near the ${r.avoid})` },
};
export const respInfo = (r) => RESP[r.response] || { kind: r.response, effect: () => "Restricts the carry", does: () => r.response };
export const tagsPhrase = (tags) => (tags || []).join(" + ");
export function ruleSentence(r) {
  return `When carrying something ${tagsPhrase(r.when_holding)}, ${respInfo(r).does(r)}.`;
}
export const shortId = (id) => (id ? String(id).slice(-6) : "—");

export function ago(state, t) {
  if (t == null || !state) return null;
  const s = state.t - t;
  if (s < 0) return "earlier session";
  if (s < 5) return "just now";
  if (s < 60) return `${Math.round(s)} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  return `${(s / 3600).toFixed(1)} h ago`;
}

export const fmtT = (t, d = 2) => `${t < 0 ? "−" : t > 0 ? "+" : ""}${Math.abs(t).toFixed(d)} s`;
