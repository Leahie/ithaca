import type { PropertyTwin, Room } from '@ithaca/schema';
import { area, distanceToPolygon } from '@/lib/geometry/polygon';

/**
 * Names for rooms as people see them.
 *
 * Twin labels repeat ("Bedroom" six times), so a label alone does not name a room. A label
 * that is used once is shown as it is; a repeated one gets a number, counted from the ground
 * floor up and from the largest room down. Code always keys on `room_id`; this is display only.
 */
const cache = new WeakMap<PropertyTwin, Map<string, string>>();

export function roomArea(room: Room): number {
  return room.area_m2 ?? area(room.polygon);
}

export function roomNames(twin: PropertyTwin): Map<string, string> {
  const hit = cache.get(twin);
  if (hit) return hit;
  const floorIndex = new Map(twin.floors.map((f) => [f.floor_id, f.index]));
  const byLabel = new Map<string, Room[]>();
  for (const room of twin.rooms) byLabel.set(room.label, [...(byLabel.get(room.label) ?? []), room]);
  const names = new Map<string, string>();
  for (const [label, rooms] of byLabel) {
    if (rooms.length === 1) {
      names.set(rooms[0]!.room_id, label);
      continue;
    }
    [...rooms]
      .sort((a, b) => (floorIndex.get(a.floor_id) ?? 0) - (floorIndex.get(b.floor_id) ?? 0) || roomArea(b) - roomArea(a) || a.room_id.localeCompare(b.room_id))
      .forEach((room, i) => names.set(room.room_id, `${label} ${i + 1}`));
  }
  cache.set(twin, names);
  return names;
}

export function roomName(twin: PropertyTwin, roomId: string | null | undefined): string {
  if (!roomId) return 'the room';
  return roomNames(twin).get(roomId) ?? roomId;
}

/** Rooms in the order they are listed: by display name, numbers in numeric order. */
export function roomsOnFloor(twin: PropertyTwin, floorId: string): Room[] {
  const names = roomNames(twin);
  return twin.rooms.filter((r) => r.floor_id === floorId).sort((a, b) => (names.get(a.room_id) ?? '').localeCompare(names.get(b.room_id) ?? '', 'en', { numeric: true }));
}

/** How far outside a room's outline a sweep can stand and still be in it (outlines stop short of glass walls and doorways). */
const SWEEP_ROOM_SLACK_M = 1.5;
/** A scan camera standing on a floor is this high above it; on a staircase it is not. */
const EYE_ABOVE_FLOOR_M: readonly [number, number] = [1.2, 2.1];

const sweepRoomCache = new WeakMap<PropertyTwin, Record<string, string | null>>();

/**
 * sweep_id -> the room someone standing there is in, or null when they are in none (stairs,
 * patio, garden). The twin's own `room_id` first; else the nearest room on the sweep's floor
 * within SWEEP_ROOM_SLACK_M whose floor the sweep stands on. Never a room on another floor, and
 * never a stair position (stairwells carved out of a room in the rooms file stay no room).
 */
export function sweepRooms(twin: PropertyTwin): Record<string, string | null> {
  const hit = sweepRoomCache.get(twin);
  if (hit) return hit;
  const out: Record<string, string | null> = {};
  for (const s of twin.sweeps) {
    if (s.room_id) {
      out[s.sweep_id] = s.room_id;
      continue;
    }
    let best: string | null = null;
    let bestD = SWEEP_ROOM_SLACK_M;
    for (const r of twin.rooms) {
      if (r.floor_id !== s.floor_id) continue;
      const eye = s.position.y - r.floor_y;
      if (eye < EYE_ABOVE_FLOOR_M[0] || eye > EYE_ABOVE_FLOOR_M[1]) continue;
      const d = distanceToPolygon([s.position.x, s.position.z], r.polygon);
      if (d <= bestD) [best, bestD] = [r.room_id, d];
    }
    out[s.sweep_id] = best;
  }
  sweepRoomCache.set(twin, out);
  return out;
}

/** Beyond this from every room on its floor, a sweep is out of doors. */
const OUTDOORS_M = 3;

/** Where someone is, for a label: the room's name; between rooms, the floor; else "Outside". */
export function placeName(twin: PropertyTwin, roomId: string | null | undefined, sweepId: string | null | undefined): string {
  if (roomId) return roomName(twin, roomId);
  const sweep = twin.sweeps.find((s) => s.sweep_id === sweepId);
  if (!sweep) return 'Outside';
  const pt = [sweep.position.x, sweep.position.z] as const;
  const near = twin.rooms.some((r) => r.floor_id === sweep.floor_id && distanceToPolygon(pt, r.polygon) <= OUTDOORS_M);
  const floor = twin.floors.find((f) => f.floor_id === sweep.floor_id);
  return near && floor ? (floor.label ?? `Floor ${floor.index + 1}`) : 'Outside';
}

/** The room to enter when none is chosen: the largest one on the lowest floor. */
export function defaultRoom(twin: PropertyTwin): Room | undefined {
  const lowest = [...twin.floors].sort((a, b) => a.index - b.index)[0];
  const onFloor = twin.rooms.filter((r) => !lowest || r.floor_id === lowest.floor_id);
  return [...(onFloor.length ? onFloor : twin.rooms)].sort((a, b) => roomArea(b) - roomArea(a))[0];
}
