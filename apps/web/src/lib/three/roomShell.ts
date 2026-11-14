import type { Room } from '@ithaca/schema';
import { BufferAttribute, BufferGeometry } from 'three';
import { pointInPolygon } from '@/lib/geometry/polygon';

/** Floor of a room as a triangle fan-free mesh (ear clipping), in the interior frame. */
export function floorGeometry(room: Room): BufferGeometry {
  const pts = room.polygon.map(([x, z]) => ({ x, z }));
  const idx = pts.map((_, i) => i);
  const tris: number[] = [];
  const area2 = (a: number, b: number, c: number) => (pts[b]!.x - pts[a]!.x) * (pts[c]!.z - pts[a]!.z) - (pts[b]!.z - pts[a]!.z) * (pts[c]!.x - pts[a]!.x);
  const total = idx.reduce((s, _, i) => s + pts[i]!.x * pts[(i + 1) % pts.length]!.z - pts[(i + 1) % pts.length]!.x * pts[i]!.z, 0);
  const sign = total >= 0 ? 1 : -1;
  const inside = (p: number, a: number, b: number, c: number) => {
    const d1 = area2(a, b, p) * sign;
    const d2 = area2(b, c, p) * sign;
    const d3 = area2(c, a, p) * sign;
    return d1 > 1e-9 && d2 > 1e-9 && d3 > 1e-9;
  };
  for (let guard = 0; idx.length > 3 && guard < 10_000; guard++) {
    let cut = false;
    for (let i = 0; i < idx.length; i++) {
      const a = idx[(i + idx.length - 1) % idx.length]!;
      const b = idx[i]!;
      const c = idx[(i + 1) % idx.length]!;
      if (area2(a, b, c) * sign <= 1e-9) continue; // reflex or flat corner
      if (idx.some((p) => p !== a && p !== b && p !== c && inside(p, a, b, c))) continue;
      tris.push(a, b, c);
      idx.splice(i, 1);
      cut = true;
      break;
    }
    if (!cut) break; // degenerate outline: keep what we have
  }
  if (idx.length === 3) tris.push(idx[0]!, idx[1]!, idx[2]!);
  const geo = new BufferGeometry();
  geo.setAttribute('position', new BufferAttribute(new Float32Array(pts.flatMap((p) => [p.x, room.floor_y, p.z])), 3));
  geo.setIndex(tris);
  geo.computeVertexNormals();
  return geo;
}

/**
 * Walls of a room, one quad per edge, facing inward. With single-sided rendering the walls
 * between the camera and the room are culled, so the room reads as an open model.
 */
export function wallGeometry(room: Room, height = room.height_m): BufferGeometry {
  const positions: number[] = [];
  const poly = room.polygon;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i]!;
    const b = poly[(i + 1) % poly.length]!;
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (len < 1e-6) continue;
    let nx = -(b[1] - a[1]) / len;
    let nz = (b[0] - a[0]) / len;
    const mx = (a[0] + b[0]) / 2;
    const mz = (a[1] + b[1]) / 2;
    if (!pointInPolygon([mx + nx * 0.05, mz + nz * 0.05], poly)) {
      nx = -nx;
      nz = -nz;
    }
    const y0 = room.floor_y;
    const y1 = room.floor_y + height;
    const quad = [
      [a[0], y0, a[1]],
      [b[0], y0, b[1]],
      [b[0], y1, b[1]],
      [a[0], y1, a[1]],
    ] as const;
    // Triangle normal of (q0, q1, q2) is (b - a) x up. Flip the winding if it points outward.
    const tx = b[0] - a[0];
    const tz = b[1] - a[1];
    const faceNx = -tz; // (tx, 0, tz) x (0, 1, 0) = (-tz, 0, tx)
    const faceNz = tx;
    const order = faceNx * nx + faceNz * nz >= 0 ? [0, 1, 2, 0, 2, 3] : [0, 2, 1, 0, 3, 2];
    for (const k of order) positions.push(...quad[k]!);
  }
  const geo = new BufferGeometry();
  geo.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
  geo.computeVertexNormals();
  return geo;
}
