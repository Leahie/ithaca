import { describe, expect, it } from 'vitest';
import { formatClock, fromZonedParts, localDate, minutesOfDay, offsetMinutes, toZonedIso, withDate, withTimeOfDay, zonedParts } from './time';

const TZ = 'America/Chicago';

describe('zoned time (I-G13)', () => {
  it('writes ISO 8601 with the zone offset', () => {
    expect(toZonedIso(new Date('2026-12-21T15:00:00Z'), TZ)).toBe('2026-12-21T09:00:00-06:00');
    expect(toZonedIso(new Date('2026-06-21T15:00:00Z'), TZ)).toBe('2026-06-21T10:00:00-05:00');
    expect(toZonedIso(new Date('2026-06-21T15:00:00Z'), 'Asia/Kolkata')).toBe('2026-06-21T20:30:00+05:30');
  });

  it('knows the offset on both sides of a DST change', () => {
    expect(offsetMinutes(new Date('2026-03-08T07:59:00Z'), TZ)).toBe(-360);
    expect(offsetMinutes(new Date('2026-03-08T08:01:00Z'), TZ)).toBe(-300);
  });

  it('round-trips wall-clock parts', () => {
    for (const iso of ['2026-01-15T07:30:00-06:00', '2026-07-04T23:55:00-05:00', '2026-11-01T12:00:00-06:00']) {
      const instant = new Date(iso);
      expect(fromZonedParts(zonedParts(instant, TZ), TZ).getTime()).toBe(instant.getTime());
      expect(toZonedIso(instant, TZ)).toBe(iso);
    }
  });

  it('changes time of day and date in the property zone, not the browser zone', () => {
    const start = '2026-12-21T09:00:00-06:00';
    expect(withTimeOfDay(start, TZ, 16 * 60 + 30)).toBe('2026-12-21T16:30:00-06:00');
    expect(withDate(start, TZ, '2026-06-21')).toBe('2026-06-21T09:00:00-05:00'); // the offset follows DST
    expect(minutesOfDay(start, TZ)).toBe(540);
    expect(localDate('2026-12-22T03:00:00Z', TZ)).toBe('2026-12-21');
  });

  it('formats a clock', () => {
    expect(formatClock(0)).toBe('12:00 am');
    expect(formatClock(13 * 60 + 5)).toBe('1:05 pm');
  });
});
