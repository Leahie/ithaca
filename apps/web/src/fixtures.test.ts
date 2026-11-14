import fs from 'node:fs';
import path from 'node:path';
import { EVENT_TYPES, type InventoryItem, type LayoutVariant, type NoiseReport, type PropertyTwin, type SunReport } from '@ithaca/schema';
import { describe, expect, it } from 'vitest';
import { fixtureAssetUrl, noiseKey } from '@/lib/api/fixture';
import { convexOverlap, footprint, isCcwFromAbove, pointInPolygon, polygonInside } from '@/lib/geometry/polygon';
import { applyEvent, initialCore } from '@/lib/store/reducer';
import { visibleLayouts } from '@/lib/layouts';
import { roomNames } from '@/lib/rooms';

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

interface Gltf {
  scenes: { nodes: number[] }[];
  nodes: { mesh?: number; children?: number[]; translation?: number[]; scale?: number[]; rotation?: number[]; matrix?: number[] }[];
  meshes: { primitives: { attributes: { POSITION: number } }[] }[];
  accessors: { min?: number[]; max?: number[] }[];
  extensionsRequired?: string[];
}

function readGlb(file: string): Gltf {
  const buf = fs.readFileSync(file);
  expect(buf.readUInt32LE(0)).toBe(0x46546c67); // "glTF"
  expect(buf.readUInt32LE(4)).toBe(2);
  return JSON.parse(buf.subarray(20, 20 + buf.readUInt32LE(12)).toString('utf8')) as Gltf;
}

/** Column-major 4x4 of a glTF node: its `matrix`, or translation * rotation * scale. */
function nodeMatrix(node: Gltf['nodes'][number]): number[] {
  if (node.matrix) return node.matrix;
  const [x, y, z, w] = node.rotation ?? [0, 0, 0, 1];
  const [sx, sy, sz] = node.scale ?? [1, 1, 1];
  const [tx, ty, tz] = node.translation ?? [0, 0, 0];
  return [
    (1 - 2 * (y! * y! + z! * z!)) * sx!, 2 * (x! * y! + z! * w!) * sx!, 2 * (x! * z! - y! * w!) * sx!, 0,
    2 * (x! * y! - z! * w!) * sy!, (1 - 2 * (x! * x! + z! * z!)) * sy!, 2 * (y! * z! + x! * w!) * sy!, 0,
    2 * (x! * z! + y! * w!) * sz!, 2 * (y! * z! - x! * w!) * sz!, (1 - 2 * (x! * x! + y! * y!)) * sz!, 0,
    tx!, ty!, tz!, 1,
  ];
}

const multiply = (a: number[], b: number[]) =>
  Array.from({ length: 16 }, (_, i) => {
    const col = Math.floor(i / 4);
    const row = i % 4;
    return a[row]! * b[col * 4]! + a[4 + row]! * b[col * 4 + 1]! + a[8 + row]! * b[col * 4 + 2]! + a[12 + row]! * b[col * 4 + 3]!;
  });

/**
 * Bounding box of a GLB in its scene frame, from the POSITION accessors' min / max carried
 * through the node transforms (a quantized mesh stores integers and scales them on the node).
 */
function glbBounds(file: string): { min: number[]; max: number[] } {
  const doc = readGlb(file);
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  const visit = (index: number, parent: number[]) => {
    const node = doc.nodes[index]!;
    const world = multiply(parent, nodeMatrix(node));
    if (node.mesh !== undefined) {
      for (const prim of doc.meshes[node.mesh]!.primitives) {
        const acc = doc.accessors[prim.attributes.POSITION]!;
        for (let corner = 0; corner < 8; corner++) {
          const p = [0, 1, 2].map((k) => ((corner >> k) & 1 ? acc.max![k]! : acc.min![k]!));
          for (let k = 0; k < 3; k++) {
            const v = world[k]! * p[0]! + world[4 + k]! * p[1]! + world[8 + k]! * p[2]! + world[12 + k]!;
            min[k] = Math.min(min[k]!, v);
            max[k] = Math.max(max[k]!, v);
          }
        }
      }
    }
    for (const child of node.children ?? []) visit(child, world);
  };
  for (const root of doc.scenes[0]!.nodes) visit(root, [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
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
    expect(twin.sweeps.some((s) => s.room_id === null)).toBe(true); // yard scans belong to no room
    // Matterport assigns a few sweeps on thresholds to the neighbouring room.
    expect(inside.length / placed.length).toBeGreaterThan(0.9);
  });

  it('room names for display are unique, though labels repeat', () => {
    const names = [...roomNames(twin).values()];
    expect(new Set(twin.rooms.map((r) => r.label)).size).toBeLessThan(twin.rooms.length);
    expect(new Set(names).size).toBe(twin.rooms.length);
    expect(names).toContain('Office'); // a label used once stays as it is
    expect(names).toContain('Bedroom 1');
  });

  it('the interior mesh exists, uses only compressions the loader decodes, and fits the asset budget (C9)', () => {
    expect(twin.assets.interior_glb).toBeTruthy();
    const file = path.join(FIX, twin.assets.interior_glb!.replace(/^\/?(fixtures\/)?/, ''));
    expect(fs.statSync(file).size).toBeLessThan(25e6);
    // drei's useGLTF decodes meshopt and quantized meshes with no extra setup; Draco would need a decoder.
    for (const ext of readGlb(file).extensionsRequired ?? []) expect(['EXT_meshopt_compression', 'KHR_mesh_quantization']).toContain(ext);
  });

  it('the interior mesh is in the interior frame: Y up, and the rooms lie inside it (I-G9)', () => {
    const { min, max } = glbBounds(path.join(FIX, twin.assets.interior_glb!.replace(/^\/?(fixtures\/)?/, '')));
    expect(max[1]! - min[1]!).toBeLessThan(20); // a two-storey house with yard trees, not a 30 m tall one
    for (const room of twin.rooms) {
      expect(room.floor_y).toBeGreaterThan(min[1]! - 0.5);
      expect(room.floor_y + room.height_m).toBeLessThan(max[1]! + 0.5);
      for (const [x, z] of room.polygon) {
        expect(x).toBeGreaterThan(min[0]! - 1);
        expect(x).toBeLessThan(max[0]! + 1);
        expect(z).toBeGreaterThan(min[2]! - 1);
        expect(z).toBeLessThan(max[2]! + 1);
      }
    }
  });

  it('fixture asset paths are keys under fixtures/, turned into URLs by the fixture API', () => {
    expect(fixtureAssetUrl('blobs/x/interior.glb')).toBe('/fixtures/blobs/x/interior.glb');
    expect(fixtureAssetUrl('/fixtures/blobs/x/interior.glb')).toBe('/fixtures/blobs/x/interior.glb');
    expect(fixtureAssetUrl('https://cdn.example/x.glb')).toBe('https://cdn.example/x.glb');
    expect(fixtureAssetUrl(null)).toBeNull();
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
