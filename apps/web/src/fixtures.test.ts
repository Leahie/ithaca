import fs from 'node:fs';
import path from 'node:path';
import { EVENT_TYPES, type InventoryItem, type LayoutVariant, type NoiseReport, type PropertyTwin, type SunReport } from '@ithaca/schema';
import { describe, expect, it } from 'vitest';
import { noiseKey } from '@/lib/api/fixture';
import { convexOverlap, footprint, isCcwFromAbove, pointInPolygon, polygonInside } from '@/lib/geometry/polygon';
import { applyEvent, initialCore } from '@/lib/store/reducer';
import { visibleLayouts } from '@/lib/layouts';

/**
 * The fixtures the web app runs on in fixture mode (A6) must have the schema's shape and
 * respect the global invariants, because the frontend trusts them without checking.
 */
const FIX = path.resolve(import.meta.dirname, '../../../fixtures');
const PID = 'onXQLXhfyQv'; // I-G19: the fixture property id appears in tests and fixtures only
const json = <T>(rel: string) => JSON.parse(fs.readFileSync(path.join(FIX, rel), 'utf8')) as T;

const twin = json<PropertyTwin>(`twin/${PID}.json`);
const inventory = json<InventoryItem[]>('inventory/demo.json');
const layouts = json<LayoutVariant[]>(`layouts/${PID}.json`);

/** Bounding box of a GLB from its POSITION accessors (min / max are required by glTF). */
function glbBounds(file: string): { min: number[]; max: number[] } {
  const buf = fs.readFileSync(file);
  expect(buf.readUInt32LE(0)).toBe(0x46546c67); // "glTF"
  expect(buf.readUInt32LE(4)).toBe(2);
  const jsonLength = buf.readUInt32LE(12);
  const doc = JSON.parse(buf.subarray(20, 20 + jsonLength).toString('utf8')) as {
    meshes: { primitives: { attributes: { POSITION: number } }[] }[];
    accessors: { min?: number[]; max?: number[] }[];
  };
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const mesh of doc.meshes) {
    for (const prim of mesh.primitives) {
      const acc = doc.accessors[prim.attributes.POSITION]!;
      for (let k = 0; k < 3; k++) {
        min[k] = Math.min(min[k]!, acc.min![k]!);
        max[k] = Math.max(max[k]!, acc.max![k]!);
      }
    }
  }
  return { min, max };
}

describe('twin fixture', () => {
  it('has the frame, time and identity fields the invariants need', () => {
    expect(twin.property_id).toBe(PID);
    expect(twin.sources.matterport_model_id).toBeTruthy(); // I-G16
    expect(twin.interior_to_world).toHaveLength(16); // I-G10
    expect(() => new Intl.DateTimeFormat('en-US', { timeZone: twin.timezone })).not.toThrow(); // I-G13
    expect(twin.rooms.length).toBeGreaterThan(5);
    expect(twin.sweeps.length).toBeGreaterThan(10);
  });

  it('room polygons are open, counter-clockwise from above, and unique by id', () => {
    for (const room of twin.rooms) {
      expect(room.polygon.length, room.label).toBeGreaterThanOrEqual(3);
      expect(room.polygon[0], room.label).not.toEqual(room.polygon.at(-1));
      expect(isCcwFromAbove(room.polygon), room.label).toBe(true);
    }
    expect(new Set(twin.rooms.map((r) => r.room_id)).size).toBe(twin.rooms.length);
  });

  it('openings refer to rooms, are upright quads, and have compass azimuths in [0, 360)', () => {
    const rooms = new Set(twin.rooms.map((r) => r.room_id));
    for (const o of twin.openings) {
      expect(rooms.has(o.room_id)).toBe(true);
      expect(o.polygon).toHaveLength(4);
      expect(o.facade_azimuth_deg).toBeGreaterThanOrEqual(0);
      expect(o.facade_azimuth_deg).toBeLessThan(360);
      expect(o.polygon[0]!.y).toBeCloseTo(o.polygon[1]!.y, 6);
      expect(o.polygon[2]!.y).toBeGreaterThan(o.polygon[1]!.y);
    }
  });

  it('every sweep that names a room sits inside that room (the frames agree)', () => {
    const rooms = new Map(twin.rooms.map((r) => [r.room_id, r]));
    const placed = twin.sweeps.filter((s) => s.room_id);
    const inside = placed.filter((s) => pointInPolygon([s.position.x, s.position.z], rooms.get(s.room_id!)!.polygon));
    expect(placed.length).toBeGreaterThan(40);
    // Matterport assigns a few sweeps on thresholds to the neighbouring room.
    expect(inside.length / placed.length).toBeGreaterThan(0.9);
  });

  it('the interior mesh exists, is in the interior frame (Y up), and fits the asset budget (C9)', () => {
    const file = path.join(FIX, 'blobs', PID, 'interior.glb');
    const { min, max } = glbBounds(file);
    expect(fs.statSync(file).size).toBeLessThan(25e6);
    expect(max[1]! - min[1]!).toBeLessThan(15); // two storeys tall, not 30 m: Y is up
    for (const room of twin.rooms) {
      for (const [x, z] of room.polygon) {
        expect(x).toBeGreaterThan(min[0]! - 1);
        expect(x).toBeLessThan(max[0]! + 1);
        expect(z).toBeGreaterThan(min[2]! - 1);
        expect(z).toBeLessThan(max[2]! + 1);
      }
    }
  });
});

describe('inventory fixture (I-G15, B6)', () => {
  it('has three items', () => expect(inventory).toHaveLength(3));

  it.each(inventory.map((i) => [i.label, i] as const))('%s: GLB matches dims_m within 2 cm, origin at the bottom center', (_label, item) => {
    const { min, max } = glbBounds(path.join(FIX, item.glb_url.replace(/^\/fixtures\//, '')));
    expect(max[0]! - min[0]!).toBeCloseTo(item.dims_m.w, 1.7); // |diff| < 0.01
    expect(max[2]! - min[2]!).toBeCloseTo(item.dims_m.d, 1.7);
    expect(max[1]! - min[1]!).toBeCloseTo(item.dims_m.h, 1.7);
    expect(Math.abs(min[1]!)).toBeLessThan(0.02);
    expect(Math.abs(min[0]! + max[0]!) / 2).toBeLessThan(0.02);
    expect(Math.abs(min[2]! + max[2]!) / 2).toBeLessThan(0.02);
    expect(fs.existsSync(path.join(FIX, item.source_image_url.replace(/^\/fixtures\//, '')))).toBe(true);
  });
});

describe('layout fixture (B2)', () => {
  const items = new Map(inventory.map((i) => [i.item_id, i]));
  it('has three variants for one room', () => {
    expect(layouts).toHaveLength(3);
    expect(new Set(layouts.map((l) => l.room_id)).size).toBe(1);
  });

  it.each(layouts.map((l, i) => [`variant ${i + 1}`, l] as const))('%s: items sit on the floor, inside the room, without overlap', (_name, layout) => {
    const room = twin.rooms.find((r) => r.room_id === layout.room_id)!;
    const prints = layout.placements.map((p) => {
      const item = items.get(p.item_id)!;
      expect(p.position.y).toBe(room.floor_y);
      expect(p.yaw_deg).toBeGreaterThanOrEqual(0);
      expect(p.yaw_deg).toBeLessThan(360);
      return footprint(p.position.x, p.position.z, item.dims_m.w, item.dims_m.d, p.yaw_deg);
    });
    for (const f of prints) expect(polygonInside(f, room.polygon)).toBe(true);
    for (let i = 0; i < prints.length; i++) for (let j = i + 1; j < prints.length; j++) expect(convexOverlap(prints[i]!, prints[j]!)).toBe(false);
  });

  it('desktop and phone pick the same variants to show', () => {
    const roomId = layouts[0]!.room_id;
    expect(visibleLayouts(layouts, [], roomId)).toMatchObject({ scope: 'room' });
    expect(visibleLayouts(layouts, [], 'some-other-room')).toMatchObject({ scope: 'all' });
    expect(visibleLayouts(layouts, [layouts[1]!.layout_id], null).variants).toEqual([layouts[1]]);
  });
});

describe('report fixtures keep the full schema shape (C3)', () => {
  it('every room has a sun report for a whole year and a noise report for every scenario', () => {
    for (const room of twin.rooms) {
      const sun = json<SunReport>(`reports/${PID}/sun/${room.room_id}.json`);
      expect(sun.daily).toHaveLength(365);
      expect(sun.daily.every((d) => d.direct_sun_hours >= 0 && d.direct_sun_hours <= 16)).toBe(true);

      const noise = json<Record<string, NoiseReport>>(`reports/${PID}/noise/${room.room_id}.json`);
      for (const glazing of [null, 'single', 'double', 'triple'] as const) {
        for (const add_carpet of [false, true]) {
          const report = noise[noiseKey({ glazing, add_carpet, include_furniture: true })]!;
          expect(report.hourly.map((h) => h.hour)).toEqual(Array.from({ length: 24 }, (_, i) => i)); // I-G13
          expect(report.hourly.every((h) => h.bands_db.length === 8)).toBe(true); // I-G14
          expect(report.rt60_s).toBeGreaterThan(0);
        }
      }
    }
  });

  it('better glazing is quieter, and carpet shortens the reverberation (C5 direction)', () => {
    const room = twin.rooms.find((r) => r.floor_material === 'hardwood' && twin.openings.some((o) => o.room_id === r.room_id && o.kind === 'window'))!;
    const noise = json<Record<string, NoiseReport>>(`reports/${PID}/noise/${room.room_id}.json`);
    const at8 = (key: string) => noise[key]!.hourly[8]!.laeq_dba;
    expect(at8('single|0|1')).toBeGreaterThan(at8('double|0|1'));
    expect(at8('double|0|1')).toBeGreaterThan(at8('triple|0|1'));
    expect(noise['asbuilt|1|1']!.rt60_s).toBeLessThan(noise['asbuilt|0|1']!.rt60_s);
  });
});

describe('schema and reducer stay in step', () => {
  it('the reducer accepts every v0 event type', () => {
    const state = initialCore('2026-12-21T09:00:00-06:00');
    for (const type of EVENT_TYPES) {
      expect(() => applyEvent(state, { v: 1, id: 'x', ts: '', session_id: 's', source: 'server', type, payload: {} } as never)).not.toThrow();
    }
  });
});
