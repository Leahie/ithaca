import type { PropertyTwin, Room, Sweep } from '@ithaca/schema';
import { centroid } from '@/lib/geometry/polygon';
import { roomArea } from '@/lib/rooms';

/**
 * The scan positions that stand on a room's floor. A room outline can take in a staircase, whose
 * positions sit part-way between floors; going "to the room" must not land on a step.
 */
export function floorSweeps(twin: PropertyTwin, roomId: string): Sweep[] {
  const inRoom = twin.sweeps.filter((s) => s.room_id === roomId);
  const ys = inRoom.map((s) => s.position.y).sort((a, b) => a - b);
  const median = ys[Math.floor(ys.length / 2)] ?? 0;
  const level = inRoom.filter((s) => Math.abs(s.position.y - median) < 0.4);
  return level.length ? level : inRoom;
}

/** The sweep to stand on when asked to go to a room: the one nearest the room's center. */
export function sweepForRoom(twin: PropertyTwin, roomId: string): Sweep | undefined {
  const room = twin.rooms.find((r) => r.room_id === roomId);
  if (!room) return undefined;
  const [cx, cz] = centroid(room.polygon);
  let best: Sweep | undefined;
  let bestDist = Infinity;
  for (const s of floorSweeps(twin, roomId)) {
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


type P3 = { x: number; y: number; z: number };

/**
 * Where to stand and what to face so a room shows well: the sweep farthest from the room's focus
 * (its middle, pulled toward its windows), looking at that focus. Used when the user picks a room
 * themselves; the agent sends its own view with the goto (same idea, computed on the server).
 */
export function viewForRoom(twin: PropertyTwin, roomId: string): { sweep: Sweep; lookAt: P3 } | undefined {
  const room = twin.rooms.find((r) => r.room_id === roomId);
  const inRoom = floorSweeps(twin, roomId);
  if (!room || !inRoom.length) return undefined;
  let [fx, fz] = centroid(room.polygon);
  const windows = twin.openings.filter((o) => o.room_id === roomId && o.kind !== 'door' && o.exterior !== false && o.polygon.length >= 2);
  if (windows.length) {
    const wx = windows.reduce((sum, o) => sum + (o.polygon[0]!.x + o.polygon[1]!.x) / 2, 0) / windows.length;
    const wz = windows.reduce((sum, o) => sum + (o.polygon[0]!.z + o.polygon[1]!.z) / 2, 0) / windows.length;
    fx += (wx - fx) * 0.35;
    fz += (wz - fz) * 0.35;
  }
  const far = (s: Sweep) => Math.hypot(s.position.x - fx, s.position.z - fz);
  const sweep = inRoom.reduce((best, s) => (far(s) > far(best) ? s : best));
  if (far(sweep) < 0.6) {
    // Standing on the focus: face the far end of the room instead.
    const [vx, vz] = room.polygon.reduce((best, p) => (Math.hypot(p[0] - sweep.position.x, p[1] - sweep.position.z) > Math.hypot(best[0] - sweep.position.x, best[1] - sweep.position.z) ? p : best));
    fx = vx;
    fz = vz;
  }
  return { sweep, lookAt: { x: fx, y: sweep.position.y - 0.25, z: fz } };
}

/**
 * The Showcase camera rotation (degrees) that looks from one point toward another. `x` is pitch,
 * up positive; `y` is yaw about the vertical axis, where 0 looks along -Z and positive turns left
 * (the three.js camera convention the Showcase pose uses).
 */
export function rotationToward(from: P3, to: P3): { x: number; y: number } {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dz = to.z - from.z;
  const flat = Math.hypot(dx, dz);
  if (flat < 1e-6) return { x: 0, y: 0 };
  const deg = 180 / Math.PI;
  return { x: Math.max(-35, Math.min(35, Math.atan2(dy, flat) * deg)), y: Math.atan2(-dx, -dz) * deg };
}

const AERIAL_PITCH_DEG = 60;

/**
 * The dollhouse camera for arranging furniture: above `target`, looking down at it and facing -Z,
 * so the screen is turned like the phone's plan (+X right, +Z down the page) and "up" on the phone
 * is up on the screen. `span` is the room's longest side: a bigger room is seen from further away.
 */
export function aerialView(target: P3, span: number): { position: P3; rotation: { x: number; y: number } } {
  const distance = Math.min(9, Math.max(5.5, span * 0.6));
  const pitch = (AERIAL_PITCH_DEG * Math.PI) / 180;
  return {
    position: { x: target.x, y: target.y + distance * Math.sin(pitch), z: target.z + distance * Math.cos(pitch) },
    rotation: { x: -AERIAL_PITCH_DEG, y: 0 },
  };
}
