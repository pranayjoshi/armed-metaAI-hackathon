// Forward kinematics + table mapping for the Stage digital twin. Pure maths, no DOM, no three.js.
//
// Conventions (read off configs/poses.yaml, whose numbers must put the slots at the table_xy they claim):
//   shoulder_lift -90 = upper arm vertical, 0 = horizontal forward; elbow_flex 0 = forearm straight on;
//   wrist_flex 0 = hand straight on; shoulder_pan +  swings toward +x of the table (right in the overhead image).
// With those, slot_2 (pan 0, lift 0, elbow 20, wrist 75) lands 23 cm out at table height, which at the
// 54 cm workspace puts it at table_xy (0.5, 0.55) when the base sits at (0.5, 0.117). So that is the layout.
// Units are centimetres. World axes: x right, y up, z toward the viewer (= table y increasing).

export const DIM = { H: 17, L1: 11.6, L2: 13.5, L3: 12.2, S: 54, BASE_Y: 0.117 };
export const BASE = { x: 0, z: (DIM.BASE_Y - 0.5) * DIM.S };
export const rad = (d) => (d * Math.PI) / 180;

/** Normalised table coords (0-1, y down in the overhead image) -> world x,z in cm. */
export const tableToWorld = (x, y) => [(x - 0.5) * DIM.S, (y - 0.5) * DIM.S];
export const worldToTable = (x, z) => [x / DIM.S + 0.5, z / DIM.S + 0.5];

/** Joint angles (degrees) -> points of the arm in the world frame. */
export function fk(j) {
  const a1 = rad((j.shoulder_lift ?? -90) + 90);
  const a2 = a1 + rad(j.elbow_flex ?? 0);
  const a3 = a2 + rad(j.wrist_flex ?? 0);
  const pan = rad(j.shoulder_pan ?? 0);
  const planar = [[0, 0], [0, DIM.H]];
  for (const [len, a] of [[DIM.L1, a1], [DIM.L2, a2], [DIM.L3, a3]]) {
    const [r, h] = planar[planar.length - 1];
    planar.push([r + len * Math.sin(a), h + len * Math.cos(a)]);
  }
  const pts = planar.map(([r, h]) => ({ x: BASE.x + r * Math.sin(pan), y: h, z: BASE.z + r * Math.cos(pan), r }));
  return { pts, base: pts[0], shoulder: pts[1], elbow: pts[2], wrist: pts[3], tip: pts[4], pan, reach: pts[4].r };
}
