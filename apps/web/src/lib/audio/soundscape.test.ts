import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { indoors } from './mixer';
import { dayCurve, dayTypeId, levelAt, type CurveHour, type SoundLevel, type Soundscape } from './soundscape';

const hours = (v: number) => Array.from({ length: 24 }, () => v);
const data: Soundscape = {
  day_types: [{ id: 'summer-weekday', season: 'summer', day: 'weekday' }],
  sources: [],
  levels: {
    'summer-weekday': {
      sources: {
        birds: { laeq_dba: hours(40).map((v, h) => (h === 6 ? 50 : v)), bands_db: hours(0).map(() => [0, 0, 20, 30, 38, 44, 40, 30]) },
        arterial: { laeq_dba: hours(45), bands_db: hours(0).map(() => [45, 43, 41, 41, 44, 39, 24, 0]) },
        local_streets: { laeq_dba: hours(45), bands_db: hours(0).map(() => [45, 43, 41, 41, 44, 39, 24, 0]) },
      },
    },
  },
};

describe('soundscape levels', () => {
  it('picks the season and weekend in the home timezone', () => {
    expect(dayTypeId('2026-07-15T10:00:00-05:00', 'America/Chicago')).toBe('summer-weekday');
    expect(dayTypeId('2026-01-17T10:00:00-06:00', 'America/Chicago')).toBe('winter-weekend');
  });

  it('adds sources in energy and blends hours', () => {
    expect(levelAt(data, 'summer-weekday', ['arterial', 'local_streets'], 12 * 60)!.laeq_dba).toBeCloseTo(48.01, 1);
    const half = levelAt(data, 'summer-weekday', ['birds'], 5 * 60 + 30)!.laeq_dba;
    expect(half).toBeGreaterThan(40);
    expect(half).toBeLessThan(50);
    expect(levelAt(data, 'summer-weekday', ['frogs'], 0)).toBeNull();
  });

  it('takes what the walls stop off each band inside', () => {
    const out = levelAt(data, 'summer-weekday', ['birds'], 6 * 60)!;
    const inside = indoors(out, [10, 10, 20, 25, 30, 30, 30, 30])!;
    expect(inside.laeq_dba).toBeCloseTo(out.laeq_dba - 30, 0);
    expect(indoors(out, null)).toBe(out);
  });
});

describe('the day curve of the chosen sounds', () => {
  // The demo home's soundscape, as the API serves it.
  const home = JSON.parse(readFileSync(resolve(__dirname, '../../../../../fixtures/reports/onXQLXhfyQv/soundscape.json'), 'utf8')) as Soundscape;
  const outside = (l: SoundLevel) => l;
  const walls = (l: SoundLevel) => indoors(l, [26, 30, 29, 33, 35, 37, 28, 20]);
  const loudestHour = (c: CurveHour[]) => c.reduce((a, b) => (b.laeq_dba > a.laeq_dba ? b : a)).hour;
  const at = (c: CurveHour[], h: number) => c[h]!.laeq_dba;

  it('changes with the sounds chosen', () => {
    const birds = dayCurve(home, 'summer-weekday', ['birds'], outside);
    const traffic = dayCurve(home, 'summer-weekday', ['highway', 'streets'], outside);
    expect(birds.map((h) => h.laeq_dba)).not.toEqual(traffic.map((h) => h.laeq_dba));
    expect(dayCurve(home, 'summer-weekday', [], outside).every((h) => h.laeq_dba === 0)).toBe(true);
  });

  it('has birds loudest at dawn and traffic loudest in the rush hours', () => {
    const birds = dayCurve(home, 'summer-weekday', ['birds'], outside);
    expect(loudestHour(birds)).toBeGreaterThanOrEqual(5);
    expect(loudestHour(birds)).toBeLessThanOrEqual(7);
    expect(at(birds, 6) - at(birds, 13)).toBeGreaterThan(5);

    const traffic = dayCurve(home, 'summer-weekday', ['highway', 'streets'], outside);
    expect(at(traffic, 8)).toBeGreaterThan(at(traffic, 3) + 8); // morning rush over the small hours
    expect(at(traffic, 17)).toBeGreaterThan(at(traffic, 12)); // evening rush over midday
    expect(at(traffic, 17)).toBeGreaterThan(at(traffic, 22));
  });

  it('colours each hour by its loudest sound: birds at dawn, insects at noon, frogs at night', () => {
    const all = dayCurve(home, 'summer-weekday', ['birds', 'insects', 'frogs'], outside);
    expect(all[6]!.loudest).toBe('birds');
    expect(all[13]!.loudest).toBe('insects');
    expect(['frogs', 'insects']).toContain(all[2]!.loudest);
  });

  it('is quieter through the walls, keeping its shape', () => {
    const out = dayCurve(home, 'summer-weekday', ['birds'], outside);
    const inside = dayCurve(home, 'summer-weekday', ['birds'], walls);
    expect(at(inside, 6)).toBeLessThan(at(out, 6) - 20);
    expect(loudestHour(inside)).toBe(loudestHour(out));
  });
});
