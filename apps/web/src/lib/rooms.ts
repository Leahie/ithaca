import type { PropertyTwin, Room } from '@ithaca/schema';
import { area } from '@/lib/geometry/polygon';

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
