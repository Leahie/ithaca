/**
 * Frame math (I-G9 .. I-G12).
 *
 * Interior frame: Matterport Showcase SDK frame, right-handed, Y up, meters.
 * World frame: local ENU, X east, Y north, Z up, meters.
 * `interior_to_world` is a 4x4 matrix in column-major order.
 * Compass angles are degrees clockwise from true north.
 */
export type V3 = readonly [number, number, number];
export type Mat4 = readonly number[];

const RAD = Math.PI / 180;

export function applyPoint(m: Mat4, [x, y, z]: V3): [number, number, number] {
  return [
    m[0]! * x + m[4]! * y + m[8]! * z + m[12]!,
    m[1]! * x + m[5]! * y + m[9]! * z + m[13]!,
    m[2]! * x + m[6]! * y + m[10]! * z + m[14]!,
  ];
}

export function applyDirection(m: Mat4, [x, y, z]: V3): [number, number, number] {
  return [
    m[0]! * x + m[4]! * y + m[8]! * z,
    m[1]! * x + m[5]! * y + m[9]! * z,
    m[2]! * x + m[6]! * y + m[10]! * z,
  ];
}

/** Inverse of a rigid transform (rotation + translation, no scale). */
export function invertRigid(m: Mat4): number[] {
  const r = [m[0]!, m[4]!, m[8]!, m[1]!, m[5]!, m[9]!, m[2]!, m[6]!, m[10]!]; // transpose, column-major
  const t: V3 = [m[12]!, m[13]!, m[14]!];
  const tx = -(r[0]! * t[0] + r[3]! * t[1] + r[6]! * t[2]);
  const ty = -(r[1]! * t[0] + r[4]! * t[1] + r[7]! * t[2]);
  const tz = -(r[2]! * t[0] + r[5]! * t[1] + r[8]! * t[2]);
  return [r[0]!, r[1]!, r[2]!, 0, r[3]!, r[4]!, r[5]!, 0, r[6]!, r[7]!, r[8]!, 0, tx, ty, tz, 1];
}

/**
 * interior_to_world for a model whose interior -Z axis points `headingDeg` clockwise from
 * true north. Heading 0 maps interior (x, y, z) to ENU (x, -z, y).
 */
export function interiorToWorldFromHeading(headingDeg: number, origin: V3 = [0, 0, 0]): number[] {
  const c = Math.cos(headingDeg * RAD);
  const s = Math.sin(headingDeg * RAD);
  // Columns are the ENU images of interior +X, +Y, +Z.
  return [c, -s, 0, 0, 0, 0, 1, 0, -s, -c, 0, 0, origin[0], origin[1], origin[2], 1];
}

/** Unit ENU vector that points at a compass azimuth and elevation. */
export function compassToEnu(azimuthDeg: number, elevationDeg: number): [number, number, number] {
  const az = azimuthDeg * RAD;
  const el = elevationDeg * RAD;
  return [Math.sin(az) * Math.cos(el), Math.cos(az) * Math.cos(el), Math.sin(el)];
}

/** Compass azimuth in [0, 360) of an ENU direction projected on the ground. */
export function enuToAzimuth([e, n]: V3): number {
  return ((Math.atan2(e, n) / RAD) % 360 + 360) % 360;
}

/** Compass azimuth of a direction given in the interior frame. */
export function interiorDirectionToAzimuth(interiorToWorld: Mat4, dir: V3): number {
  return enuToAzimuth(applyDirection(interiorToWorld, dir));
}

/** Direction, in the interior frame, that points from the scene toward a compass azimuth / elevation. */
export function compassToInterior(interiorToWorld: Mat4, azimuthDeg: number, elevationDeg: number): [number, number, number] {
  return applyDirection(invertRigid(interiorToWorld), compassToEnu(azimuthDeg, elevationDeg));
}

export function normalize([x, y, z]: V3): [number, number, number] {
  const l = Math.hypot(x, y, z) || 1;
  return [x / l, y / l, z / l];
}
