import type { Opening, PropertyTwin } from '@ithaca/schema';
import { describe, expect, it } from 'vitest';
import { interiorToWorldFromHeading } from '@/lib/geometry/frames';
import { openingFromCorners, openingSize } from './annotate';
import { MAX_PORTALS, createPortalUniforms, passesLight, portalFromOpening, setPortals } from './portals';

const twin = {
  interior_to_world: interiorToWorldFromHeading(0),
  rooms: [
    {
      room_id: 'living',
      floor_id: 'f1',
      label: 'Living Room',
      // 4 x 5 m room; its -Z wall (z = 0) faces north at heading 0.
      polygon: [
        [0, 0],
        [0, 5],
        [4, 5],
        [4, 0],
      ],
      floor_y: 0,
      height_m: 2.5,
      floor_material: 'hardwood',
    },
  ],
} as unknown as PropertyTwin;

describe('openings annotation', () => {
  it('builds an upright rectangle on the wall from two clicked corners', () => {
    // Clicks on the north wall, seen from inside: the face normal points into the room (+Z).
    const r = openingFromCorners(twin, { a: [1, 0.9, 0], b: [2.5, 2.1, 0], normal: [0, 0, 1], kind: 'window', glazing: 'double' }, 'w1');
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.opening.room_id).toBe('living');
    expect(openingSize(r.opening).width).toBeCloseTo(1.5);
    expect(openingSize(r.opening).height).toBeCloseTo(1.2);
    expect(r.opening.polygon.every((p) => p.z === 0)).toBe(true);
    expect(Math.min(...r.opening.polygon.map((p) => p.y))).toBeCloseTo(0.9);
  });

  it('gives the outward normal as the facade azimuth (I-G11), whichever way the face normal points', () => {
    for (const normal of [
      [0, 0, 1],
      [0, 0, -1],
    ] as const) {
      const north = openingFromCorners(twin, { a: [1, 1, 0], b: [2, 2, 0], normal, kind: 'window', glazing: 'double' });
      expect(north.ok && north.opening.facade_azimuth_deg).toBe(0);
    }
    const east = openingFromCorners(twin, { a: [4, 1, 1], b: [4, 2, 2], normal: [-1, 0, 0], kind: 'window', glazing: 'single' });
    expect(east.ok && east.opening.facade_azimuth_deg).toBe(90);
    const south = openingFromCorners(twin, { a: [1, 0, 5], b: [2, 2, 5], normal: [0, 0, -1], kind: 'door', glazing: 'none' });
    expect(south.ok && south.opening.facade_azimuth_deg).toBe(180);
  });

  it('refuses floors, tiny rectangles and walls outside every room', () => {
    expect(openingFromCorners(twin, { a: [1, 0, 1], b: [2, 0, 2], normal: [0, 1, 0], kind: 'window', glazing: 'double' }).ok).toBe(false);
    expect(openingFromCorners(twin, { a: [1, 1, 0], b: [1.05, 2, 0], normal: [0, 0, 1], kind: 'window', glazing: 'double' }).ok).toBe(false);
    expect(openingFromCorners(twin, { a: [20, 1, 20], b: [21, 2, 20], normal: [0, 0, 1], kind: 'window', glazing: 'double' }).ok).toBe(false);
  });
});

describe('light portals', () => {
  const opening = (over: Partial<Opening>): Opening => ({
    opening_id: 'o',
    room_id: 'living',
    kind: 'window',
    polygon: [
      { x: 1, y: 1, z: 0 },
      { x: 2, y: 1, z: 0 },
      { x: 2, y: 2.2, z: 0 },
      { x: 1, y: 2.2, z: 0 },
    ],
    facade_azimuth_deg: 0,
    glazing: 'double',
    stc: null,
    ...over,
  });

  it('windows and sliders pass light; a door only if it is glazed', () => {
    expect(passesLight(opening({ kind: 'window' }))).toBe(true);
    expect(passesLight(opening({ kind: 'slider', glazing: 'single' }))).toBe(true);
    expect(passesLight(opening({ kind: 'door', glazing: 'none' }))).toBe(false);
    expect(passesLight(opening({ kind: 'door', glazing: 'double' }))).toBe(true);
  });

  it('describes the opening as a center, two axes and two half-sizes', () => {
    const p = portalFromOpening(opening({}))!;
    expect(p.center.toArray()).toEqual([1.5, 1.6, 0]);
    expect(p.halfWidth).toBeCloseTo(0.5);
    expect(p.halfHeight).toBeCloseTo(0.6);
    expect(p.u.toArray()).toEqual([1, 0, 0]);
    expect(p.v.toArray()).toEqual([0, 1, 0]);
  });

  it('fills the shader uniforms and caps the count', () => {
    const u = createPortalUniforms();
    expect(setPortals(u, [opening({}), opening({ kind: 'door', glazing: 'none' }), opening({ kind: 'slider' })])).toBe(2);
    expect(u.uPortalCount.value).toBe(2);
    expect(u.uPortalU.value[0]!.w).toBeCloseTo(0.5);
    expect(setPortals(u, Array.from({ length: MAX_PORTALS + 10 }, () => opening({})))).toBe(MAX_PORTALS);
  });
});
