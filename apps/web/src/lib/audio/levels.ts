import type { NoiseHour, NoiseSource } from '@ithaca/schema';

/** Everyday anchors for an indoor level. General acoustics references, not report data. */
export function describeLevel(dba: number): string {
  if (dba < 25) return 'barely audible';
  if (dba < 32) return 'like a quiet bedroom at night';
  if (dba < 40) return 'like a library';
  if (dba < 48) return 'like a quiet office';
  if (dba < 56) return 'like a conversation in the next room';
  return 'like a busy street through an open window';
}

/**
 * The level at a minute of the day: linear in dB between the report's hours, which are taken
 * as the middle of each hour, wrapping round midnight. Null when the hours are missing.
 */
export function noiseAt(hourly: readonly NoiseHour[], minuteOfDay: number): Pick<NoiseHour, 'laeq_dba' | 'bands_db'> | null {
  const byHour = new Map(hourly.map((h) => [h.hour, h]));
  const t = (((minuteOfDay - 30) / 60) % 24 + 24) % 24;
  const a = byHour.get(Math.floor(t));
  const b = byHour.get((Math.floor(t) + 1) % 24);
  if (!a || !b) return a ?? b ?? null;
  const f = t - Math.floor(t);
  return {
    laeq_dba: a.laeq_dba + (b.laeq_dba - a.laeq_dba) * f,
    bands_db: a.bands_db.map((v, i) => v + ((b.bands_db[i] ?? v) - v) * f),
  };
}

/** Share of the energy that comes from roads and rail rather than the background. */
export function trafficShare(sources: readonly NoiseSource[]): number {
  let traffic = 0;
  let total = 0;
  for (const s of sources) {
    const e = 10 ** (s.contribution_dba / 10);
    total += e;
    const background = s.kind ? s.kind === 'background' : /background/i.test(s.name);
    if (!background) traffic += e;
  }
  return total > 0 ? traffic / total : 0;
}
