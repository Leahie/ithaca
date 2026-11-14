import type { PropertyTwin, Room, Sweep } from '@ithaca/schema';
import { centroid } from '@/lib/geometry/polygon';

/** The sweep to stand on when asked to go to a room: the one nearest the room's center. */
export function sweepForRoom(twin: PropertyTwin, roomId: string): Sweep | undefined {
  const room = twin.rooms.find((r) => r.room_id === roomId);
  if (!room) return undefined;
  const [cx, cz] = centroid(room.polygon);
  let best: Sweep | undefined;
  let bestDist = Infinity;
  for (const s of twin.sweeps) {
    if (s.room_id !== roomId) continue;
    const d = Math.hypot(s.position.x - cx, s.position.z - cz);
    if (d < bestDist) {
      bestDist = d;
      best = s;
    }
  }
  return best;
}

/** Eye point for a room in the interior frame: its center, 1.6 m above the floor. */
export function roomEye(room: Room): [number, number, number] {
  const [cx, cz] = centroid(room.polygon);
  return [cx, room.floor_y + 1.6, cz];
}
