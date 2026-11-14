/**
 * Time helpers for I-G13: datetimes are ISO 8601 strings with an offset, in the property's
 * IANA timezone. "Hour" means local hour 0-23 in that zone.
 */
export interface ZonedParts {
  year: number;
  month: number; // 1-12
  day: number;
  hour: number;
  minute: number;
  second: number;
}

const formatters = new Map<string, Intl.DateTimeFormat>();

function formatter(timeZone: string): Intl.DateTimeFormat {
  let f = formatters.get(timeZone);
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone,
      hourCycle: 'h23',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    formatters.set(timeZone, f);
  }
  return f;
}

export function zonedParts(instant: Date, timeZone: string): ZonedParts {
  const out: Record<string, number> = {};
  for (const part of formatter(timeZone).formatToParts(instant)) {
    if (part.type !== 'literal') out[part.type] = Number(part.value);
  }
  return { year: out.year!, month: out.month!, day: out.day!, hour: out.hour!, minute: out.minute!, second: out.second! };
}

/** Offset of the zone from UTC, in minutes, at this instant (east positive). */
export function offsetMinutes(instant: Date, timeZone: string): number {
  const p = zonedParts(instant, timeZone);
  const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return Math.round((asUtc - Math.floor(instant.getTime() / 1000) * 1000) / 60000);
}

/** The instant at which the zone's wall clock shows these parts. */
export function fromZonedParts(parts: Omit<ZonedParts, 'second'> & { second?: number }, timeZone: string): Date {
  const wall = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute, parts.second ?? 0);
  // Two passes settle the offset across a DST change.
  let guess = wall - offsetMinutes(new Date(wall), timeZone) * 60000;
  guess = wall - offsetMinutes(new Date(guess), timeZone) * 60000;
  return new Date(guess);
}

const pad = (n: number, width = 2) => String(n).padStart(width, '0');

/** ISO 8601 with the zone's offset, e.g. 2026-12-21T09:00:00-06:00. */
export function toZonedIso(instant: Date, timeZone: string): string {
  const p = zonedParts(instant, timeZone);
  const off = offsetMinutes(instant, timeZone);
  const sign = off < 0 ? '-' : '+';
  const abs = Math.abs(off);
  return `${pad(p.year, 4)}-${pad(p.month)}-${pad(p.day)}T${pad(p.hour)}:${pad(p.minute)}:${pad(p.second)}${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

export function isoDate(parts: Pick<ZonedParts, 'year' | 'month' | 'day'>): string {
  return `${pad(parts.year, 4)}-${pad(parts.month)}-${pad(parts.day)}`;
}

/** Replace the wall-clock time of day, keeping the local date. */
export function withTimeOfDay(iso: string, timeZone: string, minutesOfDay: number): string {
  const p = zonedParts(new Date(iso), timeZone);
  return toZonedIso(fromZonedParts({ ...p, hour: Math.floor(minutesOfDay / 60), minute: Math.round(minutesOfDay % 60), second: 0 }, timeZone), timeZone);
}

/** Replace the local date (YYYY-MM-DD), keeping the wall-clock time of day. */
export function withDate(iso: string, timeZone: string, date: string): string {
  const p = zonedParts(new Date(iso), timeZone);
  const [year, month, day] = date.split('-').map(Number) as [number, number, number];
  return toZonedIso(fromZonedParts({ ...p, year, month, day }, timeZone), timeZone);
}

export function minutesOfDay(iso: string, timeZone: string): number {
  const p = zonedParts(new Date(iso), timeZone);
  return p.hour * 60 + p.minute;
}

export function localDate(iso: string, timeZone: string): string {
  return isoDate(zonedParts(new Date(iso), timeZone));
}

export function formatClock(minutes: number): string {
  const h = Math.floor(minutes / 60) % 24;
  const m = Math.floor(minutes % 60);
  const suffix = h < 12 ? 'am' : 'pm';
  return `${h % 12 === 0 ? 12 : h % 12}:${pad(m)} ${suffix}`;
}
