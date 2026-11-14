'use client';

import { useEffect, useState } from 'react';
import { config } from '@/lib/config';
import type { SoundLayer } from '@/lib/store/reducer';
import { zonedParts } from '@/lib/time';

/**
 * The soundscape dataset (services/api/sim/soundscape.py): how loud each outdoor sound is just
 * outside the house, per local hour, for every season x weekday/weekend. Levels are LAeq dBA,
 * bands are dB per octave band (63 Hz .. 8 kHz).
 */
export interface Soundscape {
  day_types: { id: string; season: string; day: 'weekday' | 'weekend' }[];
  sources: { source_id: string; label: string }[];
  levels: Record<string, { sources: Record<string, { laeq_dba: number[]; bands_db: number[][] }> }>;
}

/** What one can choose to hear, the soundscape sources each is made of, and its colour on the day curve. */
export const SOUND_LAYERS: { id: SoundLayer; label: string; sources: string[]; color: string }[] = [
  { id: 'highway', label: 'Highway', sources: ['highway'], color: '#64748b' },
  { id: 'streets', label: 'Street traffic', sources: ['arterial', 'local_streets', 'horns'], color: '#d97706' },
  { id: 'birds', label: 'Birds', sources: ['birds'], color: '#0ea5e9' },
  { id: 'insects', label: 'Insects', sources: ['cicadas', 'night_insects'], color: '#65a30d' },
  { id: 'frogs', label: 'Frogs', sources: ['frogs'], color: '#0d9488' },
  { id: 'dogs', label: 'Dogs', sources: ['dogs'], color: '#a855f7' },
];

/** One hour of the chosen sounds where you are: their total, and which of them is loudest. */
export interface CurveHour {
  hour: number;
  laeq_dba: number;
  loudest: SoundLayer | null;
}

/**
 * The chosen sounds over a day where you are. `heard` turns an outside level into what reaches
 * you at that hour (indoors: less what the walls stop); outside, it is the level itself.
 */
export function dayCurve(data: Soundscape, dayType: string, layers: SoundLayer[], heard: (level: SoundLevel, hour: number) => SoundLevel | null): CurveHour[] {
  const chosen = SOUND_LAYERS.filter((l) => layers.includes(l.id));
  return Array.from({ length: 24 }, (_, hour) => {
    let total = 0;
    let loudest: SoundLayer | null = null;
    let top = 0;
    for (const l of chosen) {
      const out = levelAt(data, dayType, l.sources, hour * 60);
      const at = out && out.laeq_dba > 0 ? heard(out, hour) : null;
      const e = energy(at?.laeq_dba ?? 0);
      total += e;
      if (e > top) [top, loudest] = [e, l.id];
    }
    return { hour, laeq_dba: level(total), loudest };
  });
}

/** A sound at one moment: overall level and octave bands, outside the house. */
export interface SoundLevel {
  laeq_dba: number;
  bands_db: number[];
}

const SEASON_OF_MONTH = ['winter', 'winter', 'spring', 'spring', 'spring', 'summer', 'summer', 'summer', 'fall', 'fall', 'fall', 'winter'];

/** The dataset's day type for a moment: its season and whether it is a weekend, in the home's timezone. */
export function dayTypeId(datetime: string, timeZone: string): string {
  const p = zonedParts(new Date(datetime), timeZone);
  const weekday = new Date(Date.UTC(p.year, p.month - 1, p.day)).getUTCDay();
  return `${SEASON_OF_MONTH[p.month - 1]}-${weekday === 0 || weekday === 6 ? 'weekend' : 'weekday'}`;
}

const energy = (db: number) => (db > 0 ? 10 ** (db / 10) : 0);
const level = (e: number) => (e > 0 ? 10 * Math.log10(e) : 0);

/**
 * One source, or the energy sum of several, at `minutes` past midnight: hours are blended in
 * energy so the level glides as the day plays. Null when the dataset has none of them.
 */
export function levelAt(data: Soundscape, dayType: string, sourceIds: string[], minutes: number): SoundLevel | null {
  const day = data.levels[dayType]?.sources;
  const present = sourceIds.map((id) => day?.[id]).filter((s) => !!s);
  if (!present.length) return null;
  const h0 = Math.floor(minutes / 60) % 24;
  const h1 = (h0 + 1) % 24;
  const f = (minutes % 60) / 60;
  const at = (values: (h: number) => number) => (1 - f) * values(h0) + f * values(h1);
  const laeq = at((h) => present.reduce((s, src) => s + energy(src.laeq_dba[h] ?? 0), 0));
  const bands = Array.from({ length: 8 }, (_, b) => level(at((h) => present.reduce((s, src) => s + energy(src.bands_db[h]?.[b] ?? 0), 0))));
  return { laeq_dba: level(laeq), bands_db: bands };
}

const cache = new Map<string, Promise<Soundscape>>();

function load(propertyId: string): Promise<Soundscape> {
  const pid = encodeURIComponent(propertyId);
  const url = config.useFixtures ? `/fixtures/reports/${pid}/soundscape.json` : `${config.apiBase}/properties/${pid}/soundscape`;
  let hit = cache.get(url);
  if (!hit) {
    hit = fetch(url).then((res) => {
      if (!res.ok) throw new Error(res.status === 404 ? 'No soundscape for this home yet' : res.statusText);
      return res.json() as Promise<Soundscape>;
    });
    hit.catch(() => cache.delete(url));
    cache.set(url, hit);
  }
  return hit;
}

/** The home's soundscape, or null while loading or when there is none (the error says which). */
export function useSoundscape(propertyId: string | null): { data: Soundscape | null; error: string | null } {
  const [state, setState] = useState<{ data: Soundscape | null; error: string | null }>({ data: null, error: null });
  useEffect(() => {
    if (!propertyId) return;
    let live = true;
    load(propertyId).then(
      (data) => live && setState({ data, error: null }),
      (err: unknown) => live && setState({ data: null, error: err instanceof Error ? err.message : String(err) }),
    );
    return () => {
      live = false;
    };
  }, [propertyId]);
  return state;
}
