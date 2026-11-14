import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { compassToEnu, interiorToWorldFromHeading } from '@/lib/geometry/frames';
import { sunDirectionInterior, sunPosition } from './position';

/**
 * A3 / C5: the client's realtime sun must match the server's pvlib reference within 0.5 degrees.
 * The reference file is shared with Stream C (fixtures/sun/reference.json).
 */
interface Reference {
  lat: number;
  lon: number;
  /** Stream C's file (the owner, C5) calls the list `cases`. */
  cases: { datetime: string; azimuth_deg: number; elevation_deg: number; apparent_elevation_deg: number }[];
}
const ref = JSON.parse(fs.readFileSync(path.resolve(import.meta.dirname, '../../../../../fixtures/sun/reference.json'), 'utf8')) as Reference;

const angleBetween = (a: number[], b: number[]) => (Math.acos(Math.min(1, a[0]! * b[0]! + a[1]! * b[1]! + a[2]! * b[2]!)) * 180) / Math.PI;

describe('sun position vs the pvlib reference', () => {
  const daytime = ref.cases.filter((s) => s.apparent_elevation_deg > 0);

  it('has a useful reference', () => {
    expect(daytime.length).toBeGreaterThan(10);
  });

  it('is within 0.5 degrees for every daytime sample', () => {
    let worst = 0;
    for (const s of daytime) {
      const p = sunPosition(new Date(s.datetime), ref.lat, ref.lon);
      worst = Math.max(worst, angleBetween(compassToEnu(p.azimuth_deg, p.elevation_deg), compassToEnu(s.azimuth_deg, s.apparent_elevation_deg)));
    }
    expect(worst).toBeLessThan(0.5);
  });

  it('gives azimuth in [0, 360), clockwise from north', () => {
    for (const s of daytime) {
      const p = sunPosition(new Date(s.datetime), ref.lat, ref.lon);
      expect(p.azimuth_deg).toBeGreaterThanOrEqual(0);
      expect(p.azimuth_deg).toBeLessThan(360);
    }
  });

  it('turns the direction into the interior frame with the twin transform', () => {
    const noon = daytime.reduce((a, b) => (b.elevation_deg > a.elevation_deg ? b : a)); // the highest sun in the file
    const unrotated = sunDirectionInterior(new Date(noon.datetime), ref.lat, ref.lon, interiorToWorldFromHeading(0));
    const rotated = sunDirectionInterior(new Date(noon.datetime), ref.lat, ref.lon, interiorToWorldFromHeading(90));
    expect(unrotated[1]).toBeGreaterThan(0.9); // high summer sun is mostly up, in any heading
    expect(rotated[1]).toBeCloseTo(unrotated[1], 9);
    expect(Math.hypot(...unrotated)).toBeCloseTo(1, 9);
    // Heading 0: interior = (east, up, -north). Heading 90: interior = (-north, up, -east).
    expect(rotated[0]).toBeCloseTo(unrotated[2], 9);
    expect(rotated[2]).toBeCloseTo(-unrotated[0], 9);
  });
});
