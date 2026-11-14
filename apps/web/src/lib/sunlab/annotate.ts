import type { Glazing, Opening, OpeningKind, PropertyTwin, Room } from '@ithaca/schema';
import { interiorDirectionToAzimuth, type V3 } from '@/lib/geometry/frames';
import { pointInPolygon } from '@/lib/geometry/polygon';

export interface AnnotationInput {
  /** Two opposite corners clicked on the wall, interior frame. */
  a: V3;
  b: V3;
  /** Normal of the clicked face, any sign. */
  normal: V3;
  kind: OpeningKind;
  glazing: Glazing;
}

export type AnnotationResult = { ok: true; opening: Opening } | { ok: false; reason: string };

const round = (n: number) => Math.round(n * 1000) / 1000;

function roomAt(twin: PropertyTwin, x: number, y: number, z: number): Room | undefined {
  return twin.rooms.find((r) => y >= r.floor_y - 0.3 && y <= r.floor_y + r.height_m + 0.5 && pointInPolygon([x, z], r.polygon));
}

/**
 * Build an Opening from two clicks on a wall (openings annotation, v1).
 * The opening is the upright rectangle, in the wall plane, that has the clicks as corners.
 * The facade azimuth sent here is provisional: Stream C owns `facade_azimuth_deg`.
 */
export function openingFromCorners(twin: PropertyTwin, input: AnnotationInput, id: string = `ann-${crypto.randomUUID()}`): AnnotationResult {
  const { a, b } = input;
  // A wall normal is horizontal. Reject floors and ceilings.
  const nl = Math.hypot(input.normal[0], input.normal[2]);
  if (nl < 0.5 * Math.hypot(...input.normal)) return { ok: false, reason: 'Click on a wall, not on a floor or ceiling.' };
  let nx = input.normal[0] / nl;
  let nz = input.normal[2] / nl;
  // Tangent along the wall: up x normal.
  const ux = nz;
  const uz = -nx;
  const du = (b[0] - a[0]) * ux + (b[2] - a[2]) * uz;
  const dy = b[1] - a[1];
  if (Math.abs(du) < 0.2 || Math.abs(dy) < 0.2) return { ok: false, reason: 'That opening is too small. Click two opposite corners.' };

  const x0 = a[0] + Math.min(0, du) * ux;
  const z0 = a[2] + Math.min(0, du) * uz;
  const y0 = Math.min(a[1], b[1]);
  const w = Math.abs(du);
  const h = Math.abs(dy);
  const cx = x0 + (ux * w) / 2;
  const cz = z0 + (uz * w) / 2;
  const cy = y0 + h / 2;

  // The room is on one side of the wall; the outward normal points to the other side.
  const front = roomAt(twin, cx + nx * 0.3, cy, cz + nz * 0.3);
  const back = roomAt(twin, cx - nx * 0.3, cy, cz - nz * 0.3);
  const room = front ?? back;
  if (!room) return { ok: false, reason: 'That wall does not belong to a known room.' };
  if (front) {
    nx = -nx;
    nz = -nz;
  }

  return {
    ok: true,
    opening: {
      opening_id: id,
      room_id: room.room_id,
      kind: input.kind,
      polygon: [
        { x: round(x0), y: round(y0), z: round(z0) },
        { x: round(x0 + ux * w), y: round(y0), z: round(z0 + uz * w) },
        { x: round(x0 + ux * w), y: round(y0 + h), z: round(z0 + uz * w) },
        { x: round(x0), y: round(y0 + h), z: round(z0) },
      ],
      facade_azimuth_deg: Math.round(interiorDirectionToAzimuth(twin.interior_to_world, [nx, 0, nz]) * 10) / 10 % 360,
      glazing: input.glazing,
      stc: null,
    },
  };
}

export function openingSize(o: Opening): { width: number; height: number } {
  const [p0, p1, , p3] = o.polygon;
  if (!p0 || !p1 || !p3) return { width: 0, height: 0 };
  return { width: Math.hypot(p1.x - p0.x, p1.y - p0.y, p1.z - p0.z), height: Math.hypot(p3.x - p0.x, p3.y - p0.y, p3.z - p0.z) };
}
