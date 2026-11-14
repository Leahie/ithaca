import { describe, expect, it } from 'vitest';
import { applyDirection, applyPoint, compassToEnu, compassToInterior, enuToAzimuth, interiorDirectionToAzimuth, interiorToWorldFromHeading, invertRigid } from './frames';
import { area, convexOverlap, ensureCcwFromAbove, footprint, frontDirection, isCcwFromAbove, pointInPolygon, polygonInside, type Pt } from './polygon';

const close = (a: readonly number[], b: readonly number[], eps = 1e-9) => a.forEach((v, i) => expect(v).toBeCloseTo(b[i]!, -Math.log10(eps)));

describe('frames (I-G9 .. I-G11)', () => {
  it('maps interior axes to ENU at heading 0: +Y is up, -Z is north, +X is east', () => {
    const m = interiorToWorldFromHeading(0);
    close(applyDirection(m, [1, 0, 0]), [1, 0, 0]);
    close(applyDirection(m, [0, 1, 0]), [0, 0, 1]);
    close(applyDirection(m, [0, 0, -1]), [0, 1, 0]);
  });

  it('heading is the compass direction of interior -Z', () => {
    for (const heading of [0, 37, 90, 180, 271.5]) {
      expect(interiorDirectionToAzimuth(interiorToWorldFromHeading(heading), [0, 0, -1])).toBeCloseTo(heading, 6);
    }
  });

  it('round-trips a point: interior -> world -> interior (C6)', () => {
    const m = interiorToWorldFromHeading(123.4, [5, -7, 16.9]);
    const p = [-9.6, 1.4, -15.2] as const;
    close(applyPoint(invertRigid(m), applyPoint(m, p)), p, 1e-9);
  });

  it('uses column-major order like three.js Matrix4.fromArray (I-G10)', () => {
    const m = interiorToWorldFromHeading(0, [10, 20, 30]);
    expect(m.slice(12, 15)).toEqual([10, 20, 30]); // translation lives in elements 12..14
    close(applyPoint(m, [0, 0, 0]), [10, 20, 30]);
  });

  it('compass angles are clockwise from true north', () => {
    close(compassToEnu(0, 0), [0, 1, 0]);
    close(compassToEnu(90, 0), [1, 0, 0]);
    close(compassToEnu(180, 0), [0, -1, 0]);
    close(compassToEnu(0, 90), [0, 0, 1], 1e-9);
    expect(enuToAzimuth([-1, 0, 0])).toBeCloseTo(270);
  });

  it('puts a southern sun on the +Z side of an unrotated interior', () => {
    const d = compassToInterior(interiorToWorldFromHeading(0), 180, 30);
    expect(d[2]).toBeGreaterThan(0.8);
    expect(d[1]).toBeCloseTo(0.5);
  });
});

describe('polygons', () => {
  // Seen from above (+X right, +Z toward the viewer) this square runs counter-clockwise.
  const ccw: Pt[] = [
    [0, 0],
    [0, 4],
    [4, 4],
    [4, 0],
  ];

  it('knows counter-clockwise seen from above', () => {
    expect(isCcwFromAbove(ccw)).toBe(true);
    expect(isCcwFromAbove([...ccw].reverse())).toBe(false);
    expect(ensureCcwFromAbove([...ccw].reverse())).toEqual(ccw);
    expect(area(ccw)).toBeCloseTo(16);
  });

  it('tests containment and overlap', () => {
    expect(pointInPolygon([1, 1], ccw)).toBe(true);
    expect(pointInPolygon([5, 1], ccw)).toBe(false);
    expect(polygonInside(footprint(2, 2, 2, 1, 30), ccw)).toBe(true);
    expect(polygonInside(footprint(3.6, 2, 2, 1, 0), ccw)).toBe(false);
    expect(convexOverlap(footprint(1, 1, 1, 1, 0), footprint(1.5, 1, 1, 1, 0))).toBe(true);
    expect(convexOverlap(footprint(1, 1, 1, 1, 0), footprint(2, 1, 1, 1, 0))).toBe(false); // touching edges
  });
});

describe('yaw (I-G12)', () => {
  it('yaw 0 points the item front along +Z', () => {
    close(frontDirection(0), [0, 1]);
  });

  it('positive yaw turns counter-clockwise seen from above: +Z toward +X', () => {
    close(frontDirection(90), [1, 0]);
    close(frontDirection(180), [0, -1]);
  });

  it('footprint is w along X and d along Z at yaw 0, and swaps at 90', () => {
    const xs = (f: number[][]) => Math.max(...f.map((p) => p[0]!)) - Math.min(...f.map((p) => p[0]!));
    const zs = (f: number[][]) => Math.max(...f.map((p) => p[1]!)) - Math.min(...f.map((p) => p[1]!));
    const at0 = footprint(0, 0, 2, 1, 0);
    const at90 = footprint(0, 0, 2, 1, 90);
    expect(xs(at0)).toBeCloseTo(2);
    expect(zs(at0)).toBeCloseTo(1);
    expect(xs(at90)).toBeCloseTo(1);
    expect(zs(at90)).toBeCloseTo(2);
  });
});
