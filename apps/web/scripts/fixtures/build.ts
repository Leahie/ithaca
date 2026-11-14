/**
 * Stand-in fixture builder for Stream A (WORKSTREAMS.md section 3).
 *
 * Streams B and C own these fixtures. Until their mocks land, this script builds valid
 * stand-ins at the agreed paths so the web app runs offline (A6):
 *
 *   fixtures/twin/<pid>.json                  PropertyTwin from the real Matterport model
 *   fixtures/blobs/<pid>/interior.glb         real 50k mesh, converted to the interior frame
 *   fixtures/blobs/<pid>/context.glb          SYNTHETIC ground + neighbors + trees (not DSM)
 *   fixtures/blobs/<pid>/ortho.svg            SYNTHETIC top-down drawing of that context
 *   fixtures/blobs/inventory/*                3 furniture GLBs + source images + masks
 *   fixtures/inventory/demo.json              InventoryItem[]
 *   fixtures/layouts/<pid>.json               LayoutVariant[] (3 valid variants, one room)
 *   fixtures/reports/<pid>/{sun,noise}/*.json STAND-IN numbers, not the Stream C engines
 *   fixtures/reports/<pid>/area.json          STAND-IN area profile
 *   fixtures/memory/demo/**                   memory files
 *
 * Rooms, openings, sweeps and the mesh are real Matterport data. Sun, noise and area numbers
 * are plausible placeholders with the right schema shape.
 *
 *   pnpm --filter @ithaca/web fixtures:build            # from .cache/matterport/<pid>
 *   pnpm --filter @ithaca/web fixtures:build --fetch    # needs MATTERPORT_TOKEN_ID / _SECRET
 *
 * Output is deterministic: no clock, no randomness.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type {
  AreaProfile,
  InventoryItem,
  LayoutVariant,
  NoiseReport,
  NoiseScenario,
  Opening,
  PropertyTwin,
  Room,
  SunReport,
  Sweep,
} from '@ithaca/schema';
import { compassToInterior, interiorDirectionToAzimuth, interiorToWorldFromHeading } from '../../src/lib/geometry/frames';
import {
  area,
  bounds,
  centroid,
  convexOverlap,
  ensureCcwFromAbove,
  footprint,
  pointInPolygon,
  polygonInside,
  type Pt,
} from '../../src/lib/geometry/polygon';
import { sunPosition } from '../../src/lib/sun/position';
import { fromZonedParts } from '../../src/lib/time';
import { box, writeGlb, type Part } from './glb';

const PID = 'onXQLXhfyQv';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '../../../..');
const FIX = path.join(REPO, 'fixtures');
const RAW = path.join(REPO, '.cache', 'matterport', PID);
const TZ = 'America/Chicago';
const USER = 'demo';
// Matterport gives GPS but no heading. Stream C's ICP against the Vexcel footprint fixes this;
// until then interior -Z is taken as north and quality.alignment says "gps_only".
const HEADING_DEG = 0;

const r3 = (n: number) => Math.round(n * 1000) / 1000;
const r2 = (n: number) => Math.round(n * 100) / 100;
const r1 = (n: number) => Math.round(n * 10) / 10;

function write(rel: string, data: string | Buffer): void {
  const file = path.join(FIX, rel);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, data);
}
const writeJson = (rel: string, value: unknown, pretty = true) => write(rel, JSON.stringify(value, null, pretty ? 1 : undefined) + '\n');

// ---------------------------------------------------------------------------------------------
// Raw Matterport data
// ---------------------------------------------------------------------------------------------

const MODEL_QUERY = `{ model(id:"${PID}"){ id name publication{ address } geocoordinates{ latitude longitude altitude }
 floors{ id label sequence }
 rooms{ id label tags classifications{ label } floor{ id } ceiling(units: metric){ minHeight maxHeight }
  dimensions(units: metric){ areaFloor height } dimensionEstimates(units: metric){ lowerElevation height area }
  boundary{ edges{ id type vertices{ id position{ x y } } openings{ id label type width height lowerElevation relativeCenter } } } }
 locations{ id position{ x y z } floor{ id } room{ id } }
 panoLocations{ id position{ x y z } anchor{ id } }
 assets{ meshes{ filename url } } } }`;

async function fetchRaw(): Promise<void> {
  const id = process.env.MATTERPORT_TOKEN_ID;
  const secret = process.env.MATTERPORT_TOKEN_SECRET;
  if (!id || !secret) throw new Error('--fetch needs MATTERPORT_TOKEN_ID and MATTERPORT_TOKEN_SECRET');
  const res = await fetch('https://api.matterport.com/api/models/graph', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: 'Basic ' + Buffer.from(`${id}:${secret}`).toString('base64') },
    body: JSON.stringify({ query: MODEL_QUERY }),
  });
  const body = (await res.json()) as { data?: { model: RawModel & { assets: { meshes: { filename: string; url: string }[] } } }; errors?: unknown };
  if (!body.data) throw new Error('Matterport query failed: ' + JSON.stringify(body.errors));
  fs.mkdirSync(RAW, { recursive: true });
  const mesh = body.data.model.assets.meshes.find((m) => m.filename.includes('50k'));
  if (!mesh) throw new Error('no 50k mesh asset on the model');
  fs.writeFileSync(path.join(RAW, 'mesh_50k.obj'), Buffer.from(await (await fetch(mesh.url)).arrayBuffer()));
  const { assets: _assets, ...model } = body.data.model;
  fs.writeFileSync(path.join(RAW, 'model.json'), JSON.stringify({ data: { model } }));
}

interface RawVertex {
  id: string;
  position: { x: number; y: number };
}
interface RawOpening {
  id: string;
  label: string | null;
  type: 'doorway' | 'window' | 'opening' | 'divider';
  width: number;
  height: number;
  lowerElevation: number;
  relativeCenter: number;
}
interface RawEdge {
  id: string;
  vertices: [RawVertex, RawVertex];
  openings: RawOpening[];
}
interface RawRoom {
  id: string;
  tags: string[];
  classifications: { label: string }[];
  floor: { id: string };
  ceiling: { minHeight?: number; maxHeight?: number } | null;
  dimensions: { areaFloor?: number; height?: number } | null;
  dimensionEstimates: { lowerElevation?: number; height?: number; area?: number } | null;
  boundary: { edges: RawEdge[] } | null;
}
interface RawModel {
  name: string;
  publication: { address: string };
  geocoordinates: { latitude: number; longitude: number; altitude: number };
  floors: { id: string; label: string; sequence: number }[];
  rooms: RawRoom[];
  locations: { id: string; position: { x: number; y: number; z: number }; floor: { id: string } | null; room: { id: string } | null }[];
  panoLocations: { id: string; position: { x: number; y: number; z: number }; anchor: { id: string } | null }[];
}

// The Model API frame is Z up. The Showcase SDK (interior) frame is Y up: (x, y, z) -> (x, z, -y).
const toInterior = (x: number, y: number, z: number): [number, number, number] => [x, z, -y];
const toInteriorXZ = (x: number, y: number): [number, number] => [x, -y];

// ---------------------------------------------------------------------------------------------
// Rooms
// ---------------------------------------------------------------------------------------------

/** Outer outline of a room from its unordered wall edges (planar face walk, largest face). */
function roomOutline(edges: RawEdge[]): [number, number][] | null {
  const pos = new Map<string, [number, number]>();
  const adj = new Map<string, Set<string>>();
  for (const e of edges) {
    const [a, b] = e.vertices;
    if (a.id === b.id) continue;
    pos.set(a.id, toInteriorXZ(a.position.x, a.position.y));
    pos.set(b.id, toInteriorXZ(b.position.x, b.position.y));
    if (!adj.has(a.id)) adj.set(a.id, new Set());
    if (!adj.has(b.id)) adj.set(b.id, new Set());
    adj.get(a.id)!.add(b.id);
    adj.get(b.id)!.add(a.id);
  }
  // Drop dangling walls (they are partitions that end inside the room).
  for (let changed = true; changed; ) {
    changed = false;
    for (const [v, ns] of adj) {
      if (ns.size <= 1) {
        for (const n of ns) adj.get(n)!.delete(v);
        adj.delete(v);
        changed = true;
      }
    }
  }
  if (adj.size < 3) return null;
  const sorted = new Map<string, string[]>();
  for (const [v, ns] of adj) {
    const p = pos.get(v)!;
    sorted.set(
      v,
      [...ns].sort((a, b) => {
        const pa = pos.get(a)!;
        const pb = pos.get(b)!;
        return Math.atan2(pa[1] - p[1], pa[0] - p[0]) - Math.atan2(pb[1] - p[1], pb[0] - p[0]);
      }),
    );
  }
  const seen = new Set<string>();
  let best: [number, number][] | null = null;
  let bestArea = 0;
  for (const [u, ns] of sorted) {
    for (const v of ns) {
      if (seen.has(`${u}>${v}`)) continue;
      const face: string[] = [];
      let a = u;
      let b = v;
      for (let guard = 0; guard < 10000 && !seen.has(`${a}>${b}`); guard++) {
        seen.add(`${a}>${b}`);
        face.push(a);
        const ring = sorted.get(b)!;
        const next = ring[(ring.indexOf(a) + 1) % ring.length]!;
        a = b;
        b = next;
      }
      const poly = face.map((id) => pos.get(id)!);
      const ar = area(poly);
      if (ar > bestArea) {
        bestArea = ar;
        best = poly;
      }
    }
  }
  if (!best) return null;
  // Remove repeated and collinear points.
  const clean: [number, number][] = [];
  for (let i = 0; i < best.length; i++) {
    const prev = best[(i - 1 + best.length) % best.length]!;
    const cur = best[i]!;
    const next = best[(i + 1) % best.length]!;
    const cross = (cur[0] - prev[0]) * (next[1] - cur[1]) - (cur[1] - prev[1]) * (next[0] - cur[0]);
    if (Math.hypot(cur[0] - prev[0], cur[1] - prev[1]) > 1e-4 && Math.abs(cross) > 1e-5) clean.push(cur);
  }
  return clean.length >= 3 ? ensureCcwFromAbove(clean).map(([x, z]) => [r3(x), r3(z)]) : null;
}

const FLOOR_MATERIAL: Record<string, string> = {
  Bedroom: 'carpet',
  Closet: 'carpet',
  Bathroom: 'tile',
  Kitchen: 'tile',
  Laundry: 'tile',
};

function buildRooms(model: RawModel): { rooms: Room[]; raw: Map<string, RawRoom> } {
  const kept: { room: Room; cls: string; size: number }[] = [];
  const raw = new Map<string, RawRoom>();
  for (const r of model.rooms) {
    const cls = r.classifications[0]?.label;
    if (!cls || cls === 'Void' || r.tags.includes('hide') || !r.boundary?.edges.length) continue;
    const polygon = roomOutline(r.boundary.edges);
    if (!polygon || area(polygon) < 1) continue;
    raw.set(r.id, r);
    kept.push({
      cls,
      size: area(polygon),
      room: {
        room_id: r.id,
        floor_id: r.floor.id,
        label: cls,
        polygon,
        floor_y: r3(r.dimensionEstimates?.lowerElevation ?? 0),
        height_m: r3(r.dimensions?.height ?? r.dimensionEstimates?.height ?? r.ceiling?.maxHeight ?? 2.44),
        floor_material: FLOOR_MATERIAL[cls] ?? 'hardwood',
      },
    });
  }
  // "Bedroom 1", "Bedroom 2", ... largest first, when a label repeats.
  const byClass = new Map<string, typeof kept>();
  for (const k of kept) byClass.set(k.cls, [...(byClass.get(k.cls) ?? []), k]);
  for (const [cls, list] of byClass) {
    if (list.length < 2) continue;
    list.sort((a, b) => b.size - a.size || a.room.room_id.localeCompare(b.room.room_id));
    list.forEach((k, i) => (k.room.label = `${cls} ${i + 1}`));
  }
  const floorIndex = new Map(model.floors.map((f) => [f.id, f.sequence]));
  const rooms = kept
    .map((k) => k.room)
    .sort((a, b) => floorIndex.get(a.floor_id)! - floorIndex.get(b.floor_id)! || a.label.localeCompare(b.label, 'en', { numeric: true }));
  return { rooms, raw };
}

// ---------------------------------------------------------------------------------------------
// Openings
// ---------------------------------------------------------------------------------------------

function buildOpenings(rooms: Room[], raw: Map<string, RawRoom>, interiorToWorld: number[]): Opening[] {
  const out = new Map<string, Opening>();
  for (const room of rooms) {
    for (const edge of raw.get(room.room_id)!.boundary!.edges) {
      const a = toInteriorXZ(edge.vertices[0].position.x, edge.vertices[0].position.y);
      const b = toInteriorXZ(edge.vertices[1].position.x, edge.vertices[1].position.y);
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      if (len < 1e-6) continue;
      const ux = (b[0] - a[0]) / len;
      const uz = (b[1] - a[1]) / len;
      for (const o of edge.openings) {
        if (out.has(o.id) || o.width < 0.3 || o.height < 0.3 || o.type === 'divider') continue;
        const cx = a[0] + (b[0] - a[0]) * o.relativeCenter;
        const cz = a[1] + (b[1] - a[1]) * o.relativeCenter;
        // Outward normal: the side of the wall that is not inside this room.
        let nx = -uz;
        let nz = ux;
        if (pointInPolygon([cx + nx * 0.25, cz + nz * 0.25], room.polygon)) {
          nx = -nx;
          nz = -nz;
        }
        const y0 = room.floor_y + o.lowerElevation;
        const y1 = y0 + o.height;
        const h = o.width / 2;
        const kind = o.type === 'window' ? 'window' : 'door';
        out.set(o.id, {
          opening_id: o.id,
          room_id: room.room_id,
          kind,
          polygon: [
            { x: r3(cx - ux * h), y: r3(y0), z: r3(cz - uz * h) },
            { x: r3(cx + ux * h), y: r3(y0), z: r3(cz + uz * h) },
            { x: r3(cx + ux * h), y: r3(y1), z: r3(cz + uz * h) },
            { x: r3(cx - ux * h), y: r3(y1), z: r3(cz - uz * h) },
          ],
          facade_azimuth_deg: r1(interiorDirectionToAzimuth(interiorToWorld, [nx, 0, nz])) % 360,
          glazing: kind === 'window' ? 'double' : 'none',
          stc: null,
        });
      }
    }
  }
  return [...out.values()];
}

// ---------------------------------------------------------------------------------------------
// Mesh
// ---------------------------------------------------------------------------------------------

function interiorGlb(objPath: string): { glb: Buffer; bbox: { min: number[]; max: number[] } } {
  const positions: number[] = [];
  const indices: number[] = [];
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const line of fs.readFileSync(objPath, 'utf8').split('\n')) {
    if (line.startsWith('v ')) {
      const [, x, y, z] = line.split(/\s+/);
      const p = toInterior(Number(x), Number(y), Number(z));
      positions.push(...p);
      for (let k = 0; k < 3; k++) {
        min[k] = Math.min(min[k]!, p[k]!);
        max[k] = Math.max(max[k]!, p[k]!);
      }
    } else if (line.startsWith('f ')) {
      const idx = line
        .trim()
        .split(/\s+/)
        .slice(1)
        .map((t) => Number(t.split('/')[0]) - 1);
      for (let i = 1; i + 1 < idx.length; i++) indices.push(idx[0]!, idx[i]!, idx[i + 1]!);
    }
  }
  const part: Part = { positions, indices, color: [0.87, 0.85, 0.81], roughness: 0.95 };
  return { glb: writeGlb([part], { smooth: true, generator: 'ithaca fixture builder (Matterport 50k mesh)' }), bbox: { min, max } };
}

// ---------------------------------------------------------------------------------------------
// Synthetic context (ground, neighbors, trees) and its top-down "ortho"
// ---------------------------------------------------------------------------------------------

function buildContext(bbox: { min: number[]; max: number[] }, groundY: number): { glb: Buffer; svg: string } {
  const cx = (bbox.min[0]! + bbox.max[0]!) / 2;
  const cz = (bbox.min[2]! + bbox.max[2]!) / 2;
  const hw = (bbox.max[0]! - bbox.min[0]!) / 2;
  const hd = (bbox.max[2]! - bbox.min[2]!) / 2;
  const R = 110;
  const parts: Part[] = [];
  const svg: string[] = [];
  const S = 5; // px per meter
  const px = (x: number) => r1((x - cx + R) * S);
  const pz = (z: number) => r1((z - cz + R) * S);
  svg.push(`<rect width="${2 * R * S}" height="${2 * R * S}" fill="#6f8f5a"/>`);

  const ground = box([cx - R, groundY - 0.5, cz - R], [cx + R, groundY, cz + R], [0.44, 0.52, 0.36], 1);
  parts.push(ground);

  // Street on the north side (interior -Z at heading 0).
  const streetZ = cz - hd - 16;
  parts.push(box([cx - R, groundY, streetZ - 4.5], [cx + R, groundY + 0.03, streetZ + 4.5], [0.25, 0.25, 0.27], 1));
  svg.push(`<rect x="0" y="${pz(streetZ - 4.5)}" width="${2 * R * S}" height="${9 * S}" fill="#4a4a4f"/>`);
  svg.push(`<line x1="0" x2="${2 * R * S}" y1="${pz(streetZ)}" y2="${pz(streetZ)}" stroke="#d8c35a" stroke-width="1.5" stroke-dasharray="20 14"/>`);

  // The subject lot outline.
  svg.push(
    `<rect x="${px(cx - hw - 6)}" y="${pz(cz - hd - 10)}" width="${(2 * hw + 12) * S}" height="${(2 * hd + 22) * S}" fill="#7d9b66" stroke="#ffffff" stroke-opacity="0.55" stroke-width="2"/>`,
  );
  svg.push(`<rect x="${px(bbox.min[0]!)}" y="${pz(bbox.min[2]!)}" width="${2 * hw * S}" height="${2 * hd * S}" fill="#b9aa97" stroke="#6b5f52" stroke-width="2"/>`);

  // Neighbors: [dx, dz, w, d, h] relative to the house center.
  const houses: [number, number, number, number, number][] = [
    [-(hw + 24), -2, 20, 15, 7.5],
    [hw + 22, 1, 18, 16, 6.5],
    [-(hw + 26), -(2 * hd + 40), 19, 14, 6],
    [-2, -(2 * hd + 42), 22, 14, 7],
    [hw + 24, -(2 * hd + 41), 18, 15, 6.5],
    [-(hw + 20), hd + 34, 18, 14, 6],
    [4, hd + 36, 21, 15, 9],
    [hw + 26, hd + 33, 17, 14, 6.5],
  ];
  for (const [dx, dz, w, d, h] of houses) {
    parts.push(box([cx + dx - w / 2, groundY, cz + dz - d / 2], [cx + dx + w / 2, groundY + h, cz + dz + d / 2], [0.74, 0.7, 0.66], 0.9));
    svg.push(`<rect x="${px(cx + dx - w / 2)}" y="${pz(cz + dz - d / 2)}" width="${w * S}" height="${d * S}" fill="#a39485" stroke="#6b5f52" stroke-width="1.5"/>`);
  }

  // Trees: [dx, dz, crown radius, height].
  const trees: [number, number, number, number][] = [
    [-(hw + 9), -(hd + 6), 3.5, 11],
    [hw + 8, -(hd + 7), 3, 9],
    [-(hw + 11), hd + 9, 4, 13],
    [hw + 10, hd + 12, 3.5, 12],
    [-4, hd + 14, 4.5, 14],
    [-(hw - 6), -(hd + 9), 2.5, 8],
    [hw + 9, 2, 3, 10],
    [-30, -(2 * hd + 26), 3.5, 11],
    [26, -(2 * hd + 27), 3, 10],
    [-(hw + 40), hd + 16, 4, 12],
  ];
  for (const [dx, dz, rad, h] of trees) {
    const x = cx + dx;
    const z = cz + dz;
    parts.push(box([x - 0.25, groundY, z - 0.25], [x + 0.25, groundY + h - rad, z + 0.25], [0.36, 0.27, 0.2], 1));
    parts.push(box([x - rad, groundY + h - 2 * rad, z - rad], [x + rad, groundY + h, z + rad], [0.24, 0.4, 0.22], 1));
    svg.push(`<circle cx="${px(x)}" cy="${pz(z)}" r="${rad * S}" fill="#3f6b3a" fill-opacity="0.92"/>`);
  }
  const size = 2 * R * S;
  return {
    glb: writeGlb(parts, { generator: 'ithaca fixture builder (synthetic context)' }),
    svg: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}">\n${svg.join('\n')}\n</svg>\n`,
  };
}

// ---------------------------------------------------------------------------------------------
// Inventory
// ---------------------------------------------------------------------------------------------

interface ItemSpec {
  id: string;
  slug: string;
  label: string;
  item_class: string;
  attributes: InventoryItem['attributes'];
  price: number;
  parts: Part[];
}

const GRAY: [number, number, number] = [0.47, 0.5, 0.54];
const GRAY_DARK: [number, number, number] = [0.38, 0.41, 0.45];
const OAK: [number, number, number] = [0.72, 0.55, 0.36];
const MUSTARD: [number, number, number] = [0.82, 0.62, 0.2];
const MUSTARD_DARK: [number, number, number] = [0.7, 0.52, 0.16];
const LEG: [number, number, number] = [0.2, 0.17, 0.15];

// Item space (I-G15): origin at the bottom center, front = +Z, Y up.
const ITEMS: ItemSpec[] = [
  {
    id: '6f1c2a54-8b0e-4d1a-9c3e-0a51b7d2e001',
    slug: 'sofa',
    label: 'Gray three-seat sofa',
    item_class: 'sofa',
    attributes: { style: 'mid-century modern', color: 'slate gray', material: 'woven fabric' },
    price: 1299,
    parts: [
      box([-1.05, 0.12, -0.46], [1.05, 0.42, 0.46], GRAY_DARK),
      box([-1.05, 0.42, -0.46], [1.05, 0.82, -0.24], GRAY),
      box([-1.05, 0.42, -0.24], [-0.85, 0.62, 0.46], GRAY),
      box([0.85, 0.42, -0.24], [1.05, 0.62, 0.46], GRAY),
      box([-0.84, 0.42, -0.23], [-0.29, 0.54, 0.44], GRAY),
      box([-0.275, 0.42, -0.23], [0.275, 0.54, 0.44], GRAY),
      box([0.29, 0.42, -0.23], [0.84, 0.54, 0.44], GRAY),
      box([-1.0, 0, -0.4], [-0.92, 0.12, -0.32], LEG),
      box([0.92, 0, -0.4], [1.0, 0.12, -0.32], LEG),
      box([-1.0, 0, 0.32], [-0.92, 0.12, 0.4], LEG),
      box([0.92, 0, 0.32], [1.0, 0.12, 0.4], LEG),
    ],
  },
  {
    id: '6f1c2a54-8b0e-4d1a-9c3e-0a51b7d2e002',
    slug: 'coffee-table',
    label: 'Oak coffee table',
    item_class: 'coffee_table',
    attributes: { style: 'scandinavian', color: 'natural oak', material: 'solid oak' },
    price: 349,
    parts: [
      box([-0.55, 0.37, -0.3], [0.55, 0.42, 0.3], OAK, 0.6),
      box([-0.5, 0.12, -0.25], [0.5, 0.15, 0.25], OAK, 0.6),
      box([-0.53, 0, -0.28], [-0.47, 0.37, -0.22], OAK, 0.6),
      box([0.47, 0, -0.28], [0.53, 0.37, -0.22], OAK, 0.6),
      box([-0.53, 0, 0.22], [-0.47, 0.37, 0.28], OAK, 0.6),
      box([0.47, 0, 0.22], [0.53, 0.37, 0.28], OAK, 0.6),
    ],
  },
  {
    id: '6f1c2a54-8b0e-4d1a-9c3e-0a51b7d2e003',
    slug: 'armchair',
    label: 'Mustard armchair',
    item_class: 'armchair',
    attributes: { style: 'mid-century modern', color: 'mustard yellow', material: 'velvet' },
    price: 529,
    parts: [
      box([-0.4, 0.16, -0.42], [0.4, 0.4, 0.43], MUSTARD_DARK),
      box([-0.4, 0.4, -0.42], [0.4, 0.85, -0.24], MUSTARD),
      box([-0.4, 0.4, -0.24], [-0.28, 0.58, 0.43], MUSTARD),
      box([0.28, 0.4, -0.24], [0.4, 0.58, 0.43], MUSTARD),
      box([-0.27, 0.4, -0.23], [0.27, 0.5, 0.41], MUSTARD),
      box([-0.36, 0, -0.38], [-0.3, 0.16, -0.32], LEG),
      box([0.3, 0, -0.38], [0.36, 0.16, -0.32], LEG),
      box([-0.36, 0, 0.33], [-0.3, 0.16, 0.39], LEG),
      box([0.3, 0, 0.33], [0.36, 0.16, 0.39], LEG),
    ],
  },
];

function itemDims(parts: Part[]): { w: number; d: number; h: number } {
  const min = [Infinity, Infinity, Infinity];
  const max = [-Infinity, -Infinity, -Infinity];
  for (const p of parts) {
    for (let i = 0; i < p.positions.length; i++) {
      min[i % 3] = Math.min(min[i % 3]!, p.positions[i]!);
      max[i % 3] = Math.max(max[i % 3]!, p.positions[i]!);
    }
  }
  return { w: r3(max[0]! - min[0]!), d: r3(max[2]! - min[2]!), h: r3(max[1]! - min[1]!) };
}

/** Front elevation of the parts as a product-photo stand-in, and the matching mask. */
function itemImages(spec: ItemSpec, dims: { w: number; h: number }): { photo: string; mask: string } {
  const W = 800;
  const H = 600;
  const scale = Math.min((W * 0.72) / dims.w, (H * 0.6) / dims.h);
  const ox = W / 2;
  const oy = H * 0.8;
  const rects = spec.parts
    .map((p) => {
      const xs = p.positions.filter((_, i) => i % 3 === 0);
      const ys = p.positions.filter((_, i) => i % 3 === 1);
      const zs = p.positions.filter((_, i) => i % 3 === 2);
      return { x0: Math.min(...xs), x1: Math.max(...xs), y0: Math.min(...ys), y1: Math.max(...ys), z: Math.max(...zs), color: p.color };
    })
    .sort((a, b) => a.z - b.z);
  const rect = (r: (typeof rects)[number], fill: string) =>
    `<rect x="${r1(ox + r.x0 * scale)}" y="${r1(oy - r.y1 * scale)}" width="${r1((r.x1 - r.x0) * scale)}" height="${r1((r.y1 - r.y0) * scale)}" rx="6" fill="${fill}"/>`;
  const hex = (c: [number, number, number]) => '#' + c.map((v) => Math.round(v * 255).toString(16).padStart(2, '0')).join('');
  const head = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">`;
  const photo = [
    head,
    `<rect width="${W}" height="${H}" fill="#efe9e1"/>`,
    `<rect y="${oy}" width="${W}" height="${H - oy}" fill="#d9cfc2"/>`,
    `<ellipse cx="${ox}" cy="${oy + 6}" rx="${r1((dims.w * scale) / 1.9)}" ry="14" fill="#000" fill-opacity="0.14"/>`,
    ...rects.map((r) => rect(r, hex(r.color))),
    `<text x="24" y="40" font-family="system-ui, sans-serif" font-size="20" fill="#7a6f63">${spec.label} (fixture image)</text>`,
    '</svg>',
  ].join('\n');
  const mask = [head, `<rect width="${W}" height="${H}" fill="#000"/>`, ...rects.map((r) => rect(r, '#fff')), '</svg>'].join('\n');
  return { photo: photo + '\n', mask: mask + '\n' };
}

function buildInventory(): InventoryItem[] {
  return ITEMS.map((spec) => {
    const dims = itemDims(spec.parts);
    write(`blobs/inventory/${spec.slug}.glb`, writeGlb(spec.parts, { generator: 'ithaca fixture builder (furniture)' }));
    const { photo, mask } = itemImages(spec, dims);
    write(`blobs/inventory/${spec.slug}.svg`, photo);
    write(`blobs/inventory/${spec.slug}.mask.svg`, mask);
    return {
      item_id: spec.id,
      user_id: USER,
      label: spec.label,
      item_class: spec.item_class,
      attributes: spec.attributes,
      dims_m: dims,
      glb_url: `/fixtures/blobs/inventory/${spec.slug}.glb`,
      source_image_url: `/fixtures/blobs/inventory/${spec.slug}.svg`,
      mask_url: `/fixtures/blobs/inventory/${spec.slug}.mask.svg`,
      price: spec.price,
      product_url: null,
    };
  });
}

// ---------------------------------------------------------------------------------------------
// Layouts: 3 valid variants for one living room
// ---------------------------------------------------------------------------------------------

function buildLayouts(room: Room, openings: Opening[], items: InventoryItem[]): LayoutVariant[] {
  const [sofa, table, chair] = items as [InventoryItem, InventoryItem, InventoryItem];
  const poly = room.polygon;
  const doors = openings
    .filter((o) => o.kind !== 'window')
    .map((o) => ({ x: (o.polygon[0]!.x + o.polygon[1]!.x) / 2, z: (o.polygon[0]!.z + o.polygon[1]!.z) / 2, half: Math.hypot(o.polygon[1]!.x - o.polygon[0]!.x, o.polygon[1]!.z - o.polygon[0]!.z) / 2 }));
  const walls = poly
    .map((a, i) => {
      const b = poly[(i + 1) % poly.length]!;
      return { a, b, len: Math.hypot(b[0] - a[0], b[1] - a[1]) };
    })
    .sort((p, q) => q.len - p.len);
  const yawOf = (dx: number, dz: number) => r1(((Math.atan2(dx, dz) * 180) / Math.PI + 360) % 360) % 360;

  const variants: LayoutVariant[] = [];
  const rationale = ['faces into the room with the table centered on it', 'opens the middle of the room for circulation', 'keeps the longest wall free'];
  for (const wall of walls) {
    if (variants.length === 3 || wall.len < sofa.dims_m.w + 0.3) continue;
    const ux = (wall.b[0] - wall.a[0]) / wall.len;
    const uz = (wall.b[1] - wall.a[1]) / wall.len;
    const mid: Pt = [(wall.a[0] + wall.b[0]) / 2, (wall.a[1] + wall.b[1]) / 2];
    let nx = -uz;
    let nz = ux;
    if (!pointInPolygon([mid[0] + nx * 0.3, mid[1] + nz * 0.3], poly)) {
      nx = -nx;
      nz = -nz;
    }
    search: for (const t of [0.5, 0.4, 0.6, 0.3, 0.7]) {
      for (const side of [1, -1]) {
        const wx = wall.a[0] + (wall.b[0] - wall.a[0]) * t;
        const wz = wall.a[1] + (wall.b[1] - wall.a[1]) * t;
        const yaw = yawOf(nx, nz);
        const sofaAt: Pt = [wx + nx * (sofa.dims_m.d / 2 + 0.08), wz + nz * (sofa.dims_m.d / 2 + 0.08)];
        const tableAt: Pt = [sofaAt[0] + nx * (sofa.dims_m.d / 2 + 0.45 + table.dims_m.d / 2), sofaAt[1] + nz * (sofa.dims_m.d / 2 + 0.45 + table.dims_m.d / 2)];
        const chairAt: Pt = [tableAt[0] + ux * side * 1.55, tableAt[1] + uz * side * 1.55];
        const chairYaw = yawOf(-ux * side, -uz * side);
        const placed = [
          { item: sofa, at: sofaAt, yaw },
          { item: table, at: tableAt, yaw },
          { item: chair, at: chairAt, yaw: chairYaw },
        ];
        const prints = placed.map((p) => footprint(p.at[0], p.at[1], p.item.dims_m.w, p.item.dims_m.d, p.yaw));
        const inside = prints.every((f) => polygonInside(f, poly));
        const clash = prints.some((f, i) => prints.some((g, j) => j > i && convexOverlap(f, g)));
        // Keep 0.9 m clear in front of every door.
        const blocksDoor = doors.some((d) => prints.some((f) => f.some(([x, z]) => Math.hypot(x - d.x, z - d.z) < d.half + 0.9)));
        if (!inside || clash || blocksDoor) continue;
        const n = variants.length;
        variants.push({
          layout_id: `9a7d3e10-52c4-4f6b-b8a1-3c0e5d6f700${n + 1}`,
          user_id: USER,
          property_id: PID,
          room_id: room.room_id,
          placements: placed.map((p) => ({ item_id: p.item.item_id, position: { x: r3(p.at[0]), y: room.floor_y, z: r3(p.at[1]) }, yaw_deg: p.yaw })),
          score: [0.91, 0.84, 0.77][n]!,
          rationale: `Sofa on the ${r1(wall.len)} m wall; ${rationale[n]}. Armchair angled toward the coffee table.`,
          status: 'proposed',
          feedback: null,
          thumbnail_url: null,
        });
        break search;
      }
    }
  }
  if (variants.length !== 3) throw new Error(`expected 3 valid layout variants for ${room.label}, got ${variants.length}`);
  return variants;
}

// ---------------------------------------------------------------------------------------------
// Stand-in reports
// ---------------------------------------------------------------------------------------------

function buildSunReport(room: Room, windows: Opening[], twin: PropertyTwin): SunReport {
  const cell = 0.5;
  const b = bounds(room.polygon);
  const cols = Math.max(1, Math.ceil((b.maxX - b.minX) / cell));
  const rows = Math.max(1, Math.ceil((b.maxZ - b.minZ) / cell));
  const cells: { r: number; c: number; x: number; z: number }[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const x = b.minX + (c + 0.5) * cell;
      const z = b.minZ + (r + 0.5) * cell;
      if (pointInPolygon([x, z], room.polygon)) cells.push({ r, c, x, z });
    }
  }
  const heat = Array.from({ length: rows }, () => new Array<number>(cols).fill(0));
  const daily: SunReport['daily'] = [];
  const STEP_MIN = 20;
  for (let doy = 0; doy < 365; doy++) {
    const d = new Date(Date.UTC(2026, 0, 1 + doy));
    const parts = { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() };
    let hours = 0;
    for (let m = 5 * 60; windows.length && m < 21 * 60; m += STEP_MIN) {
      const instant = fromZonedParts({ ...parts, hour: Math.floor(m / 60), minute: m % 60 }, TZ);
      const sun = sunPosition(instant, twin.lat, twin.lon);
      if (sun.elevation_deg < 2) continue;
      const dir = compassToInterior(twin.interior_to_world, sun.azimuth_deg, sun.elevation_deg);
      let lit = false;
      for (const w of windows) {
        // The sun must be on the outside of this wall.
        let da = Math.abs(sun.azimuth_deg - w.facade_azimuth_deg);
        if (da > 180) da = 360 - da;
        if (da > 88) continue;
        // Project the window onto the floor along the sun ray: the sunlit patch.
        const patch = w.polygon.map((p): Pt => {
          const t = (p.y - room.floor_y) / dir[1];
          return [p.x - dir[0] * t, p.z - dir[2] * t];
        });
        for (const c of cells) {
          if (pointInPolygon([c.x, c.z], patch)) {
            lit = true;
            if (doy % 7 === 0) heat[c.r]![c.c]! += (STEP_MIN / 60) * (7 / 365);
            else break;
          }
        }
      }
      if (lit) hours += STEP_MIN / 60;
    }
    daily.push({ date: `${parts.year}-${String(parts.month).padStart(2, '0')}-${String(parts.day).padStart(2, '0')}`, direct_sun_hours: r2(hours) });
  }
  return {
    room_id: room.room_id,
    start: '2026-01-01',
    end: '2026-12-31',
    daily,
    heatmap: { cell_m: cell, origin_xz: [r3(b.minX), r3(b.minZ)], hours: heat.map((row) => row.map(r2)) },
  };
}

const A_WEIGHT = [-26.2, -16.1, -8.6, -3.2, 0, 1.2, 1.0, -1.1];
// Unweighted octave levels of road traffic, relative to its A-weighted total.
const TRAFFIC_SPECTRUM = [8, 4, 0, -3, -4, -7, -13, -20];
const TRANSMISSION: Record<string, number[]> = {
  single: [17, 19, 21, 25, 28, 29, 27, 30],
  double: [20, 22, 24, 30, 35, 36, 33, 38],
  triple: [23, 26, 29, 34, 39, 41, 39, 43],
};
// Hourly traffic level relative to the daily peak.
const DIURNAL = [-11, -13, -14, -14, -12, -8, -4, -1, 0, -2, -3, -3, -2.5, -3, -2.5, -1.5, -0.5, 0, -1.5, -3.5, -5, -6.5, -8, -9.5];
const FLOOR_ALPHA: Record<string, number> = { hardwood: 0.08, tile: 0.03, carpet: 0.35 };
const STREET_AZIMUTH = 0;

function buildNoiseReport(room: Room, windows: Opening[], scenario: Required<NoiseScenario>): NoiseReport {
  const floorArea = area(room.polygon);
  const perimeter = room.polygon.reduce((s, p, i) => {
    const q = room.polygon[(i + 1) % room.polygon.length]!;
    return s + Math.hypot(q[0] - p[0], q[1] - p[1]);
  }, 0);
  const volume = floorArea * room.height_m;
  const baseAlpha = FLOOR_ALPHA[room.floor_material] ?? 0.08;
  const floorAlpha = scenario.add_carpet ? Math.max(baseAlpha, 0.6 * 0.35 + 0.4 * baseAlpha) : baseAlpha;
  const absorption = floorArea * floorAlpha + (perimeter * room.height_m + floorArea) * 0.07 + (scenario.include_furniture ? 0.12 * floorArea : 0);
  const rt60 = (0.161 * volume) / absorption;

  const glazing = scenario.glazing ?? 'double';
  const tl = TRANSMISSION[glazing]!;
  // Each window: facade level falls off as the facade turns away from the street.
  const facades = windows.map((w) => {
    let da = Math.abs(w.facade_azimuth_deg - STREET_AZIMUTH);
    if (da > 180) da = 360 - da;
    const s = Math.hypot(w.polygon[1]!.x - w.polygon[0]!.x, w.polygon[1]!.z - w.polygon[0]!.z) * (w.polygon[2]!.y - w.polygon[1]!.y);
    return { level: 71 - 9 * (da / 180), s };
  });

  const hourly = DIURNAL.map((rel, hour) => {
    const bands = TRAFFIC_SPECTRUM.map((spec, i) => {
      let power = 0;
      for (const f of facades) power += 10 ** ((f.level + rel + spec - tl[i]! + 10 * Math.log10(f.s / absorption)) / 10);
      // Sound that leaks through the rest of the envelope, and the room's own floor.
      power += 10 ** ((71 + rel + spec - tl[i]! - 24) / 10) + 10 ** ((18 + spec / 2) / 10);
      return 10 * Math.log10(power);
    });
    const laeq = 10 * Math.log10(bands.reduce((s, l, i) => s + 10 ** ((l + A_WEIGHT[i]!) / 10), 0));
    return { hour, laeq_dba: r1(laeq), bands_db: bands.map(r1) };
  });
  const peak = Math.max(...hourly.map((h) => h.laeq_dba));
  return {
    room_id: room.room_id,
    scenario,
    hourly,
    rt60_s: r2(rt60),
    sources: [
      { name: 'Endicott Ln traffic (stand-in)', contribution_dba: r1(peak - 0.4) },
      { name: 'Background', contribution_dba: r1(peak - 11) },
    ],
  };
}

export function noiseKey(s: NoiseScenario): string {
  return `${s.glazing ?? 'asbuilt'}|${s.add_carpet ? 1 : 0}|${s.include_furniture === false ? 0 : 1}`;
}

function buildArea(twin: PropertyTwin): AreaProfile {
  const mPerDegLat = 111_320;
  const mPerDegLon = 111_320 * Math.cos((twin.lat * Math.PI) / 180);
  // [name, category, east m, north m, source]
  const spots: [string, string, number, number, 'vexcel' | 'costar' | 'osm'][] = [
    ['Neighborhood grocery', 'grocery store', 620, 410, 'vexcel'],
    ['Corner market', 'grocery store', -340, -520, 'osm'],
    ['Pocket park', 'park', -260, 300, 'vexcel'],
    ['Bayou greenway trailhead', 'park', 180, -980, 'vexcel'],
    ['Elementary school', 'school', 540, -260, 'osm'],
    ['Middle school', 'school', -1150, 640, 'osm'],
    ['Coffee roaster', 'cafe', 700, 350, 'costar'],
    ['Bakery cafe', 'cafe', -820, -140, 'costar'],
    ['Taqueria', 'restaurant', 760, 440, 'costar'],
    ['Neighborhood bistro', 'restaurant', -900, 210, 'costar'],
    ['Wine bar', 'nightlife', 1280, 520, 'costar'],
    ['Community gym', 'fitness', 660, 500, 'costar'],
    ['Pharmacy', 'pharmacy', 590, 460, 'vexcel'],
    ['Public library branch', 'library', -1400, -300, 'osm'],
  ];
  const amenities = spots
    .map(([name, category, e, n, source]) => ({
      name,
      category,
      lat: Number((twin.lat + n / mPerDegLat).toFixed(6)),
      lon: Number((twin.lon + e / mPerDegLon).toFixed(6)),
      distance_m: Math.round(Math.hypot(e, n)),
      source,
    }))
    .sort((a, b) => a.distance_m - b.distance_m);
  const comps = [
    { address: '9400 block, same street (stand-in)', price: 1_185_000, sqft: 5400, beds: 5, sold_date: '2026-08-12', distance_m: 90 },
    { address: 'One block north (stand-in)', price: 965_000, sqft: 4300, beds: 4, sold_date: '2026-06-30', distance_m: 210 },
    { address: 'Two blocks east (stand-in)', price: 1_320_000, sqft: 6100, beds: 5, sold_date: '2026-05-04', distance_m: 380 },
    { address: 'Cul-de-sac to the south (stand-in)', price: 879_000, sqft: 3900, beds: 4, sold_date: '2026-03-19', distance_m: 460 },
    { address: 'Corner lot to the west (stand-in)', price: 1_049_000, sqft: 4800, beds: 5, sold_date: null, distance_m: 520 },
  ];
  const markdown = [
    '# Area profile',
    '',
    '> Stand-in fixture. These places and sales are placeholders, not CoStar or Vexcel results.',
    '',
    '## Day to day',
    '- **Groceries:** two options within a 10 minute walk, the closest about 480 m away.',
    '- **Green space:** a pocket park about 400 m north-west, and a bayou trail about 1 km south.',
    '- **Schools:** an elementary school within 600 m. The middle school is a short drive.',
    '',
    '## Evenings',
    '- A small cluster of restaurants and a cafe sits about 800 m to the north-east.',
    '- Nightlife is thin: one wine bar within 1.5 km. The street itself is quiet after 9 pm.',
    '',
    '## Market',
    '- Five recent comparable sales within 550 m, from $879k to $1.32M.',
    '- Larger homes (5 bedrooms, over 5,000 sq ft) sold above $1.1M.',
  ].join('\n');
  return { markdown, amenities, comps };
}

// ---------------------------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------------------------

function buildMemory(items: InventoryItem[], living: Room): void {
  write(
    'memory/demo/profile.md',
    ['# Profile', '', '- Household: two adults, one dog.', '- Works from home three days a week. Needs a quiet office.', '- Priorities: morning light in the kitchen, a calm bedroom, room to host friends.', ''].join('\n'),
  );
  write(
    'memory/demo/preferences.md',
    [
      '# Preferences',
      '',
      '## Style',
      '- Mid-century modern with warm wood. Avoids glossy finishes.',
      '',
      '## Layout',
      '- Likes the sofa to face a window, not a TV.',
      '- No TV in the bedroom.',
      '- Wants a clear path from the entry to the kitchen.',
      '',
    ].join('\n'),
  );
  write(
    'memory/demo/inventory.md',
    ['# Inventory', '', 'Derived from the database. Do not edit by hand.', '', ...items.map((i) => `- ${i.label} (${i.item_class}), ${i.dims_m.w} x ${i.dims_m.d} x ${i.dims_m.h} m, id ${i.item_id}`), ''].join('\n'),
  );
  write(
    `memory/demo/properties/${PID}.md`,
    [
      '# 9414 Endicott Lane',
      '',
      '- First visit: liked the open living area and the kitchen light.',
      `- ${living.label}: three layouts proposed with the gray sofa. No choice made yet.`,
      '- Open question: how loud is the front bedroom at rush hour?',
      '- Verdict: undecided.',
      '',
    ].join('\n'),
  );
}

// ---------------------------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------------------------

async function main(): Promise<void> {
  if (process.argv.includes('--fetch')) await fetchRaw();
  const modelPath = path.join(RAW, 'model.json');
  const objPath = path.join(RAW, 'mesh_50k.obj');
  if (!fs.existsSync(modelPath) || !fs.existsSync(objPath)) {
    throw new Error(`raw Matterport data not found in ${RAW}. Run with --fetch (needs MATTERPORT_TOKEN_ID / MATTERPORT_TOKEN_SECRET).`);
  }
  const model = (JSON.parse(fs.readFileSync(modelPath, 'utf8')) as { data: { model: RawModel } }).data.model;

  const interiorToWorld = interiorToWorldFromHeading(HEADING_DEG).map(r3);
  const { rooms, raw } = buildRooms(model);
  const openings = buildOpenings(rooms, raw, interiorToWorld);
  const roomIds = new Set(rooms.map((r) => r.room_id));

  const panoByAnchor = new Map(model.panoLocations.filter((p) => p.anchor).map((p) => [p.anchor!.id, p.position]));
  const sweeps: Sweep[] = model.locations
    .filter((l) => l.floor)
    .map((l) => {
      const p = panoByAnchor.get(l.id) ?? { ...l.position, z: l.position.z + 1.4 };
      const [x, y, z] = toInterior(p.x, p.y, p.z);
      return { sweep_id: l.id, floor_id: l.floor!.id, room_id: l.room && roomIds.has(l.room.id) ? l.room.id : null, position: { x: r3(x), y: r3(y), z: r3(z) } };
    })
    .sort((a, b) => a.sweep_id.localeCompare(b.sweep_id));

  const floors = [...model.floors]
    .sort((a, b) => a.sequence - b.sequence)
    .map((f) => {
      const ys = rooms.filter((r) => r.floor_id === f.id).map((r) => r.floor_y).sort((a, b) => a - b);
      return { floor_id: f.id, index: f.sequence, elevation_m: r3(ys[Math.floor(ys.length / 2)] ?? 0) };
    });

  const { glb, bbox } = interiorGlb(objPath);
  write(`blobs/${PID}/interior.glb`, glb);
  const groundY = Math.min(...rooms.map((r) => r.floor_y)) - 0.25;
  const context = buildContext(bbox, r3(groundY));
  write(`blobs/${PID}/context.glb`, context.glb);
  write(`blobs/${PID}/ortho.svg`, context.svg);

  const twin: PropertyTwin = {
    property_id: PID,
    version: 1,
    sources: { matterport_model_id: PID, vexcel_parcel_id: null, costar_listing_id: null },
    address: model.publication.address,
    lat: model.geocoordinates.latitude,
    lon: model.geocoordinates.longitude,
    timezone: TZ,
    interior_to_world: interiorToWorld,
    floors,
    rooms,
    openings,
    sweeps,
    assets: {
      interior_glb: `/fixtures/blobs/${PID}/interior.glb`,
      context_glb: `/fixtures/blobs/${PID}/context.glb`,
      ortho: `/fixtures/blobs/${PID}/ortho.svg`,
      obliques: [],
    },
    // Openings come from Matterport's own detection. The context mesh is synthetic.
    quality: { alignment: 'gps_only', openings: 'detected', context: 'dsm', traffic: 'class_default' },
  };
  writeJson(`twin/${PID}.json`, twin);

  const items = buildInventory();
  writeJson('inventory/demo.json', items);

  // The simplest good-sized living room: fewest corners among rooms over 25 m2.
  const living = rooms
    .filter((r) => r.label.startsWith('Living Room') && area(r.polygon) > 25)
    .sort((a, b) => a.polygon.length - b.polygon.length || a.room_id.localeCompare(b.room_id))[0];
  if (!living) throw new Error('no living room found for the layout fixture');
  const layouts = buildLayouts(living, openings.filter((o) => o.room_id === living.room_id), items);
  writeJson(`layouts/${PID}.json`, layouts);

  for (const room of rooms) {
    const windows = openings.filter((o) => o.room_id === room.room_id && o.kind === 'window');
    writeJson(`reports/${PID}/sun/${room.room_id}.json`, buildSunReport(room, windows, twin), false);
    const noise: Record<string, NoiseReport> = {};
    for (const glazing of [null, 'single', 'double', 'triple'] as const) {
      for (const add_carpet of [false, true]) {
        for (const include_furniture of [true, false]) {
          const scenario = { glazing, add_carpet, include_furniture };
          noise[noiseKey(scenario)] = buildNoiseReport(room, windows, scenario);
        }
      }
    }
    writeJson(`reports/${PID}/noise/${room.room_id}.json`, noise, false);
  }
  writeJson(`reports/${PID}/area.json`, buildArea(twin));
  buildMemory(items, living);

  const c = centroid(living.polygon);
  console.log(
    `twin: ${rooms.length} rooms, ${openings.length} openings (${openings.filter((o) => o.kind === 'window').length} windows), ${sweeps.length} sweeps, ${floors.length} floors`,
  );
  console.log(`mesh bbox x ${r1(bbox.min[0]!)}..${r1(bbox.max[0]!)} y ${r1(bbox.min[1]!)}..${r1(bbox.max[1]!)} z ${r1(bbox.min[2]!)}..${r1(bbox.max[2]!)}; interior.glb ${(glb.length / 1e6).toFixed(2)} MB`);
  console.log(`layouts: 3 variants in ${living.label} (${living.room_id}), centroid ${r1(c[0])}, ${r1(c[1])}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
