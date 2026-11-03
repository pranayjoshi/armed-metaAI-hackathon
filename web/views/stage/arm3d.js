// Procedural SO-101 for the Stage twin: base, pan column, upper arm, forearm, wrist, gripper jaws.
// Built from rounded boxes, cylinders and capsules. One builder, two skins: the real arm and a red ghost.
import { DIM, rad } from "./kin.js";

/** Rounded box: extruded rounded rectangle, centred on the origin. */
export function rbox(T, w, h, d, r = 0.6) {
  const b = Math.min(0.25, r * 0.5, w / 4, h / 4, d / 4);
  const ww = w - 2 * b, hh = h - 2 * b, rr = Math.max(0.01, Math.min(r - b, ww / 2, hh / 2));
  const x = -ww / 2, y = -hh / 2;
  const s = new T.Shape();
  s.moveTo(x + rr, y);
  s.lineTo(x + ww - rr, y); s.quadraticCurveTo(x + ww, y, x + ww, y + rr);
  s.lineTo(x + ww, y + hh - rr); s.quadraticCurveTo(x + ww, y + hh, x + ww - rr, y + hh);
  s.lineTo(x + rr, y + hh); s.quadraticCurveTo(x, y + hh, x, y + hh - rr);
  s.lineTo(x, y + rr); s.quadraticCurveTo(x, y, x + rr, y);
  const depth = Math.max(0.01, d - 2 * b);
  const g = new T.ExtrudeGeometry(s, { depth, bevelEnabled: true, bevelThickness: b, bevelSize: b, bevelSegments: 2, curveSegments: 5 });
  g.translate(0, 0, -depth / 2);
  return g;
}

/** Skins. `edge` is optional: a LineBasicMaterial for the technical outline. */
export function armSkins(T, kind = "real") {
  if (kind === "ghost") {
    const m = (o) => new T.MeshBasicMaterial({ color: 0xff3347, transparent: true, opacity: o, depthWrite: false, side: T.DoubleSide });
    return {
      body: m(0.2), servo: m(0.3), accent: m(0.45), hub: m(0.35), pad: m(0.4),
      edge: new T.LineBasicMaterial({ color: 0xff6a78, transparent: true, opacity: 0.85, blending: T.AdditiveBlending, depthWrite: false }),
      ghost: true,
    };
  }
  return {
    body: new T.MeshStandardMaterial({ color: 0x44597a, metalness: 0.45, roughness: 0.4 }),
    servo: new T.MeshStandardMaterial({ color: 0x0c1119, metalness: 0.4, roughness: 0.5 }),
    accent: new T.MeshStandardMaterial({ color: 0x4fd8ff, emissive: 0x4fd8ff, emissiveIntensity: 0.9, metalness: 0, roughness: 0.4 }),
    hub: new T.MeshStandardMaterial({ color: 0x8aa0bb, metalness: 0.85, roughness: 0.28 }),
    pad: new T.MeshStandardMaterial({ color: 0x151c28, metalness: 0.1, roughness: 0.9 }),
    edge: new T.LineBasicMaterial({ color: 0x9cc4f0, transparent: true, opacity: 0.4 }),
    ghost: false,
  };
}

/**
 * Build the arm. Returns { root, set(joints), tip, jaw, materials, geometries }.
 * `set` takes degrees (gripper 0-100 %), the same dict the runtime publishes.
 */
export function buildArm(T, skins, shared = null) {
  const geos = shared?.geos || [];
  const geo = (g) => (shared ? g : (geos.push(g), g));
  const root = new T.Group();
  const add = (parent, g, mat, x = 0, y = 0, z = 0, { edges = true, shadow = true } = {}) => {
    const m = new T.Mesh(geo(g), mat);
    m.position.set(x, y, z);
    m.castShadow = shadow && !skins.ghost;
    parent.add(m);
    if (edges && skins.edge) {
      const e = new T.LineSegments(geo(new T.EdgesGeometry(g, 35)), skins.edge);
      m.add(e);
    }
    return m;
  };
  const cylX = (r, len, seg = 28) => { const g = new T.CylinderGeometry(r, r, len, seg); g.rotateZ(Math.PI / 2); return g; };

  // base: weighted disc + collar
  add(root, new T.CylinderGeometry(8.4, 9, 1.2, 48), skins.body, 0, 0.6, 0);
  add(root, new T.TorusGeometry(7.6, 0.16, 8, 64).rotateX(Math.PI / 2), skins.accent, 0, 1.25, 0, { edges: false, shadow: false });
  add(root, new T.CylinderGeometry(5.2, 6, 1.6, 40), skins.servo, 0, 2.0, 0);

  // pan stage rotates about Y
  const pan = new T.Group();
  pan.position.y = 2.8;
  root.add(pan);
  add(pan, new T.CylinderGeometry(4.6, 4.6, 0.9, 40), skins.hub, 0, 0.45, 0, { edges: false });
  add(pan, rbox(T, 5.4, 7.4, 4.6, 1), skins.servo, 0, 4.6, 0.1);
  add(pan, rbox(T, 4.2, 6.6, 3.2, 0.9), skins.body, 0, 11.4, -0.4);
  add(pan, rbox(T, 6.8, 4.6, 4.4, 1), skins.servo, 0, DIM.H - 2.8, 0);   // shoulder servo, axis along x
  add(pan, cylX(2.2, 8.4), skins.hub, 0, DIM.H - 2.8, 0, { edges: false });
  add(pan, new T.BoxGeometry(0.25, 2.4, 0.9), skins.accent, 3.45, 4.6, 2.3, { edges: false, shadow: false });

  // upper arm
  const shoulder = new T.Group();
  shoulder.position.y = DIM.H - 2.8;
  pan.add(shoulder);
  add(shoulder, rbox(T, 3.2, DIM.L1 + 3.6, 4.4, 1.2), skins.body, 0, DIM.L1 / 2, 0);
  add(shoulder, new T.BoxGeometry(0.22, DIM.L1 * 0.55, 0.6), skins.accent, 1.68, DIM.L1 * 0.5, 1.4, { edges: false, shadow: false });
  add(shoulder, cylX(2.6, 7.4), skins.hub, 0, DIM.L1, 0, { edges: false });

  // forearm
  const elbow = new T.Group();
  elbow.position.y = DIM.L1;
  shoulder.add(elbow);
  add(elbow, rbox(T, 3.0, DIM.L2 + 2.4, 3.8, 1.1), skins.body, 0, DIM.L2 / 2, 0);
  add(elbow, rbox(T, 3.9, 4.8, 3.4, 0.9), skins.servo, 0, DIM.L2 - 3.2, 0);
  add(elbow, new T.BoxGeometry(0.22, DIM.L2 * 0.5, 0.6), skins.accent, 1.58, DIM.L2 * 0.45, 1.2, { edges: false, shadow: false });
  add(elbow, cylX(2.1, 6.2), skins.hub, 0, DIM.L2, 0, { edges: false });

  // wrist flex then roll
  const wrist = new T.Group();
  wrist.position.y = DIM.L2;
  elbow.add(wrist);
  const roll = new T.Group();
  wrist.add(roll);
  add(roll, rbox(T, 3.6, 4.4, 3.6, 0.9), skins.servo, 0, 2.2, 0);
  add(roll, new T.CylinderGeometry(1.5, 1.5, 0.8, 24), skins.hub, 0, 4.6, 0, { edges: false });
  add(roll, rbox(T, 4.6, 3.4, 3.2, 0.8), skins.body, 0, 6.2, 0);

  // jaws: fixed on -x, moving jaw pivots at +x and opens away
  const jawLen = DIM.L3 - 6.9;
  const fixedJaw = new T.Group();
  fixedJaw.position.set(-1.7, 7.8, 0);
  roll.add(fixedJaw);
  add(fixedJaw, rbox(T, 0.9, jawLen, 2.4, 0.3), skins.body, 0, jawLen / 2, 0);
  add(fixedJaw, new T.BoxGeometry(0.5, 1.7, 2.2), skins.pad, 0.6, jawLen - 0.9, 0, { edges: false });
  const jaw = new T.Group();
  jaw.position.set(1.7, 7.8, 0);
  roll.add(jaw);
  add(jaw, rbox(T, 0.9, jawLen, 2.4, 0.3), skins.body, 0, jawLen / 2, 0);
  add(jaw, new T.BoxGeometry(0.5, 1.7, 2.2), skins.pad, -0.6, jawLen - 0.9, 0, { edges: false });

  const tip = new T.Object3D();
  tip.position.set(0, DIM.L3, 0);
  roll.add(tip);
  const heldAnchor = new T.Object3D();   // between the jaws, for the held object's world position
  heldAnchor.position.set(0, DIM.L3 - 2.2, 0);
  roll.add(heldAnchor);

  const set = (j) => {
    pan.rotation.y = rad(j.shoulder_pan ?? 0);
    shoulder.rotation.x = rad((j.shoulder_lift ?? -90) + 90);
    elbow.rotation.x = rad(j.elbow_flex ?? 0);
    wrist.rotation.x = rad(j.wrist_flex ?? 0);
    roll.rotation.y = rad(j.wrist_roll ?? 0);
    jaw.rotation.z = -rad(((j.gripper ?? 5) / 100) * 58);
  };
  return { root, set, tip, heldAnchor, jaw, geos, skins };
}

export function disposeSkins(skins) {
  Object.values(skins).forEach((m) => m?.dispose?.());
}
