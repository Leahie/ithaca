import type { PropertyTwin } from '@ithaca/schema';
import { describe, expect, it } from 'vitest';
import { applyEvent, initialCore, type CoreState } from './store/reducer';
import { placeName, sweepRooms } from './rooms';

const square = (x0: number, z0: number, s: number): [number, number][] => [
  [x0, z0],
  [x0 + s, z0],
  [x0 + s, z0 + s],
  [x0, z0 + s],
];
const sweep = (sweep_id: string, floor_id: string, x: number, z: number, room_id: string | null = null, y = floor_id === 'f2' ? 4.2 : 1.5) => ({ sweep_id, floor_id, room_id, position: { x, y, z }, neighbors: [] });

// A living room downstairs with a bedroom right above it.
const twin = {
  floors: [
    { floor_id: 'f1', index: 0, label: 'Floor 1' },
    { floor_id: 'f2', index: 1, label: 'Floor 2' },
  ],
  rooms: [
    { room_id: 'living', floor_id: 'f1', label: 'Living Room', polygon: square(0, 0, 5), floor_y: 0 },
    { room_id: 'bed', floor_id: 'f2', label: 'Bedroom', polygon: square(0, 0, 5), floor_y: 2.7 },
  ],
  sweeps: [
    sweep('up', 'f2', 2, 2, 'bed'),
    sweep('glass-door', 'f1', 5.8, 2), // just past the outline, by the glass
    sweep('stairs', 'f2', 7.5, 2), // between rooms upstairs
    sweep('stair-step', 'f2', 5.3, 2, null, 3.0), // beside the bedroom, but half-way up the stairs
    sweep('pool', 'f1', 15, 2), // well outside
  ],
} as unknown as PropertyTwin;

describe('sweepRooms', () => {
  it('puts a sweep just outside an outline in that room, never one on another floor', () => {
    const rooms = sweepRooms(twin);
    expect(rooms['up']).toBe('bed');
    expect(rooms['glass-door']).toBe('living');
    expect(rooms['stairs']).toBeNull();
    expect(rooms['stair-step']).toBeNull();
    expect(rooms['pool']).toBeNull();
  });

  it('names where someone stands when it is no room', () => {
    expect(placeName(twin, null, 'stairs')).toBe('Floor 2');
    expect(placeName(twin, null, 'pool')).toBe('Outside');
    expect(placeName(twin, 'living', 'glass-door')).toBe('Living Room');
  });
});

describe('viewer.goto by sweep', () => {
  it('leaves the last room when the sweep is in none', () => {
    const goto = (state: CoreState, sweep_id: string) =>
      applyEvent(state, { v: 0, id: sweep_id, ts: '', session_id: 's', source: 'desktop', type: 'viewer.goto', payload: { sweep_id } } as never);
    let state: CoreState = { ...initialCore('2026-06-21T12:00:00-05:00'), sweepRooms: sweepRooms(twin) };
    state = goto(state, 'up');
    expect(state.viewer.room_id).toBe('bed');
    state = goto(state, 'pool');
    expect(state.viewer.room_id).toBeNull();
    state = goto(state, 'glass-door');
    expect(state.viewer.room_id).toBe('living');
  });
});
