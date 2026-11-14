import type { PropertyTwin, Room, Sweep } from '@ithaca/schema';
import { centroid } from '@/lib/geometry/polygon';
import { roomArea } from '@/lib/rooms';

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

/**
 * Where a tour starts when nobody has asked for a room: inside the largest room on the lowest
 * floor. Some models start on a far outside view, which is a poor place to begin.
 */
export function defaultStartSweep(twin: PropertyTwin): Sweep | undefined {
  const lowest = [...twin.floors].sort((a, b) => a.index - b.index)[0];
  const rooms = twin.rooms.filter((r) => !lowest || r.floor_id === lowest.floor_id).sort((a, b) => roomArea(b) - roomArea(a));
  for (const room of rooms) {
    const sweep = sweepForRoom(twin, room.room_id);
    if (sweep) return sweep;
  }
  return twin.sweeps.find((s) => s.room_id);
}
