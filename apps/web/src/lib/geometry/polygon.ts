/**
 * 2D polygon helpers on the interior floor plane.
 * Points are (x, z) in the interior frame (I-G9). Angles here are radians (math internals, I-G8).
 */
export type Pt = readonly [number, number];

/** Shoelace area in (x, z). Negative when the polygon is CCW seen from above (+Y looking down). */
export function signedAreaXZ(poly: readonly Pt[]): number {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const p = poly[i]!;
    const q = poly[(i + 1) % poly.length]!;
    a += p[0] * q[1] - q[0] * p[1];
  }
  return a / 2;
}

export function area(poly: readonly Pt[]): number {
  return Math.abs(signedAreaXZ(poly));
}

/** Seen from above, +X is right and +Z is toward the viewer, so CCW from above is negative in (x, z). */
export function isCcwFromAbove(poly: readonly Pt[]): boolean {
  return signedAreaXZ(poly) < 0;
}

export function ensureCcwFromAbove<T extends Pt>(poly: readonly T[]): T[] {
  return isCcwFromAbove(poly) ? [...poly] : [...poly].reverse();
}

export function centroid(poly: readonly Pt[]): [number, number] {
  let a = 0;
  let cx = 0;
  let cz = 0;
  for (let i = 0; i < poly.length; i++) {
    const p = poly[i]!;
    const q = poly[(i + 1) % poly.length]!;
    const f = p[0] * q[1] - q[0] * p[1];
    a += f;
    cx += (p[0] + q[0]) * f;
    cz += (p[1] + q[1]) * f;
  }
  if (Math.abs(a) < 1e-12) {
    const n = poly.length || 1;
    return [poly.reduce((s, p) => s + p[0], 0) / n, poly.reduce((s, p) => s + p[1], 0) / n];
  }
  return [cx / (3 * a), cz / (3 * a)];
}

export interface Bounds {
  minX: number;
  minZ: number;
  maxX: number;
  maxZ: number;
}

export function bounds(points: readonly Pt[]): Bounds {
  let minX = Infinity;
  let minZ = Infinity;
  let maxX = -Infinity;
  let maxZ = -Infinity;
  for (const [x, z] of points) {
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
  }
  return { minX, minZ, maxX, maxZ };
}

export function pointInPolygon(pt: Pt, poly: readonly Pt[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i]!;
    const b = poly[j]!;
    if (a[1] > pt[1] !== b[1] > pt[1] && pt[0] < ((b[0] - a[0]) * (pt[1] - a[1])) / (b[1] - a[1]) + a[0]) {
      inside = !inside;
    }
  }
  return inside;
}

function cross(o: Pt, a: Pt, b: Pt): number {
  return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
}

/** Proper intersection of open segments (shared endpoints and touching do not count). */
export function segmentsCross(a: Pt, b: Pt, c: Pt, d: Pt): boolean {
  const d1 = cross(c, d, a);
  const d2 = cross(c, d, b);
  const d3 = cross(a, b, c);
  const d4 = cross(a, b, d);
  return d1 * d2 < 0 && d3 * d4 < 0;
}

/** True when every corner of `inner` is inside `outer` and no edges cross. */
export function polygonInside(inner: readonly Pt[], outer: readonly Pt[]): boolean {
  if (!inner.every((p) => pointInPolygon(p, outer))) return false;
  for (let i = 0; i < inner.length; i++) {
    for (let j = 0; j < outer.length; j++) {
      if (segmentsCross(inner[i]!, inner[(i + 1) % inner.length]!, outer[j]!, outer[(j + 1) % outer.length]!)) {
        return false;
      }
    }
  }
  return true;
}

/** Separating-axis overlap test for two convex polygons. Touching edges do not overlap. */
export function convexOverlap(a: readonly Pt[], b: readonly Pt[], eps = 1e-6): boolean {
  for (const poly of [a, b]) {
    for (let i = 0; i < poly.length; i++) {
      const p = poly[i]!;
      const q = poly[(i + 1) % poly.length]!;
      const nx = q[1] - p[1];
      const nz = p[0] - q[0];
      let minA = Infinity;
      let maxA = -Infinity;
      let minB = Infinity;
      let maxB = -Infinity;
      for (const v of a) {
        const s = v[0] * nx + v[1] * nz;
        minA = Math.min(minA, s);
        maxA = Math.max(maxA, s);
      }
      for (const v of b) {
        const s = v[0] * nx + v[1] * nz;
        minB = Math.min(minB, s);
        maxB = Math.max(maxB, s);
      }
      if (maxA <= minB + eps || maxB <= minA + eps) return false;
    }
  }
  return true;
}

/**
 * Floor footprint of an item (I-G12, I-G15): a w (X) by d (Z) rectangle centered on (x, z),
 * rotated by yaw about +Y, counter-clockwise seen from above. Yaw 0 puts the item front along +Z.
 */
export function footprint(x: number, z: number, w: number, d: number, yawDeg: number): [number, number][] {
  const t = (yawDeg * Math.PI) / 180;
  const c = Math.cos(t);
  const s = Math.sin(t);
  const corners: Pt[] = [
    [-w / 2, -d / 2],
    [w / 2, -d / 2],
    [w / 2, d / 2],
    [-w / 2, d / 2],
  ];
  // Rotation about +Y by t: x' = x cos t + z sin t, z' = -x sin t + z cos t.
  return corners.map(([lx, lz]) => [x + lx * c + lz * s, z - lx * s + lz * c]);
}

/** Unit vector of an item's front (+Z in item space) on the floor plane, for a given yaw. */
export function frontDirection(yawDeg: number): [number, number] {
  const t = (yawDeg * Math.PI) / 180;
  return [Math.sin(t), Math.cos(t)];
}
