/**
 * Stand-in fixture builder for Stream A (WORKSTREAMS.md section 3).
 *
 * Stream C owns the twin and the interior mesh (fixtures/twin, fixtures/blobs/<pid>/interior.glb).
 * This script reads C's twin and builds the fixtures that do not exist yet, so the web app
 * runs offline (A6). Streams B and C replace them when their mocks land:
 *
 *   fixtures/blobs/inventory/*                3 furniture GLBs + source images + masks   (B)
 *   fixtures/inventory/demo.json              InventoryItem[]                            (B)
 *   fixtures/layouts/<pid>.json               LayoutVariant[]: 3 valid variants, one room (B)
 *   fixtures/reports/<pid>/{sun,noise}/*.json STAND-IN numbers, not the Stream C engines (C)
 *   fixtures/reports/<pid>/area.json          STAND-IN area profile                      (C)
 *   fixtures/memory/demo/**                   memory files                               (B)
 *
 * Sun, noise and area numbers are plausible placeholders with the right schema shape.
 *
 *   pnpm --filter @ithaca/web fixtures:build [property_id]
 *
 * Output is deterministic: no clock, no randomness. Run it again when the twin changes.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { AreaProfile, InventoryItem, LayoutVariant, NoiseReport, NoiseScenario, Opening, PropertyTwin, Room, SunReport } from '@ithaca/schema';
import { compassToInterior } from '../../src/lib/geometry/frames';
import { area, bounds, convexOverlap, footprint, pointInPolygon, polygonInside, type Pt } from '../../src/lib/geometry/polygon';
import { roomName } from '../../src/lib/rooms';
import { sunPosition } from '../../src/lib/sun/position';
import { fromZonedParts } from '../../src/lib/time';
import { box, writeGlb, type Part } from './glb';

// The fixture property (I-G19: seed scripts may name it). Another id can be passed as an argument.
const PID = process.argv.slice(2).find((a) => !a.startsWith('-')) ?? 'onXQLXhfyQv';
const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '../../../..');
const FIX = path.join(REPO, 'fixtures');
const USER = 'demo';

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
      const instant = fromZonedParts({ ...parts, hour: Math.floor(m / 60), minute: m % 60 }, twin.timezone);
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
// The stand-in street is on the east side: the house faces east (Stream C's handoff note).
const STREET_AZIMUTH = 83;

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

function buildMemory(items: InventoryItem[], livingName: string, twin: PropertyTwin): void {
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
      `# ${twin.address.split(',')[0]}`,
      '',
      '- First visit: liked the open living area and the kitchen light.',
      `- ${livingName}: three layouts proposed with the gray sofa. No choice made yet.`,
      '- Open question: how loud is the front bedroom at rush hour?',
      '- Verdict: undecided.',
      '',
    ].join('\n'),
  );
}

// ---------------------------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------------------------

function main(): void {
  const twinPath = path.join(FIX, 'twin', `${PID}.json`);
  if (!fs.existsSync(twinPath)) throw new Error(`no twin at ${twinPath}. Stream C's ingestion writes it.`);
  const twin = JSON.parse(fs.readFileSync(twinPath, 'utf8')) as PropertyTwin;

  const items = buildInventory();
  writeJson('inventory/demo.json', items);

  // The simplest good-sized living room: fewest corners among living rooms over 25 m2.
  const living = twin.rooms
    .filter((r) => /living/i.test(r.label) && area(r.polygon) > 25)
    .sort((a, b) => a.polygon.length - b.polygon.length || a.room_id.localeCompare(b.room_id))[0];
  if (!living) throw new Error('no living room found for the layout fixture');
  const layouts = buildLayouts(
    living,
    twin.openings.filter((o) => o.room_id === living.room_id || o.other_room_id === living.room_id),
    items,
  );
  writeJson(`layouts/${PID}.json`, layouts);

  // Reports for rooms that no longer exist would be stale: start clean.
  fs.rmSync(path.join(FIX, 'reports', PID), { recursive: true, force: true });
  for (const room of twin.rooms) {
    const windows = twin.openings.filter((o) => o.room_id === room.room_id && o.kind === 'window');
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
  buildMemory(items, roomName(twin, living.room_id), twin);

  console.log(`twin v${twin.version}: ${twin.rooms.length} rooms, ${twin.openings.length} openings, alignment ${twin.quality.alignment}`);
  console.log(`layouts: 3 variants in ${roomName(twin, living.room_id)} (${living.room_id}); reports for ${twin.rooms.length} rooms`);
}

main();
