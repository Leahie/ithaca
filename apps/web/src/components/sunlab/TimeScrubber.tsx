'use client';

import type { PropertyTwin } from '@ithaca/schema';
import { useMemo } from 'react';
import { Button, cn } from '@/components/ui';
import { IconPause, IconPlay } from '@/components/ui/icons';
import { useSession } from '@/lib/store/session';
import { togglePlaying } from '@/lib/store/usePlayback';
import { useThrottledDispatch } from '@/lib/store/useThrottledDispatch';
import { sunPosition, sunTimes } from '@/lib/sun/position';
import { formatClock, fromZonedParts, localDate, minutesOfDay, toZonedIso, withDate, withTimeOfDay, zonedParts } from '@/lib/time';

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
export const compassWord = (az: number) => COMPASS[Math.round(az / 45) % 8]!;

/**
 * Time-of-day and date scrub. Every change is a `viewer.setTime` event (A1), the same one the
 * phone and the agent send.
 */
export function TimeScrubber({ twin, compact = false }: { twin: PropertyTwin; compact?: boolean }) {
  const datetime = useSession((s) => s.viewer.datetime);
  const setTime = useThrottledDispatch('viewer.setTime', 70);
  const playing = useSession((s) => s.viewer.playback.playing);
  const tz = twin.timezone;
  const minutes = minutesOfDay(datetime, tz);
  const date = localDate(datetime, tz);

  const { sunrise, sunset } = useMemo(() => {
    const noon = fromZonedParts({ ...zonedParts(new Date(datetime), tz), hour: 12, minute: 0 }, tz);
    const t = sunTimes(noon, twin.lat, twin.lon);
    const toMin = (d: Date | null) => (d ? minutesOfDay(toZonedIso(d, tz), tz) : null);
    return { sunrise: toMin(t.sunrise), sunset: toMin(t.sunset) };
    // Sunrise and sunset depend on the date only.
  }, [date, tz, twin.lat, twin.lon]); // eslint-disable-line react-hooks/exhaustive-deps

  const sun = sunPosition(new Date(datetime), twin.lat, twin.lon);

  const year = date.slice(0, 4);
  const presets = [
    { label: 'Mar 20', date: `${year}-03-20` },
    { label: 'Jun 21', date: `${year}-06-21` },
    { label: 'Sep 22', date: `${year}-09-22` },
    { label: 'Dec 21', date: `${year}-12-21` },
  ];
  const dayGradient =
    sunrise !== null && sunset !== null
      ? `linear-gradient(to right, #3a3552 0%, #3a3552 ${(sunrise / 1440) * 100 - 2}%, #ffd35a ${(sunrise / 1440) * 100 + 3}%, #ffe9a8 50%, #ffd35a ${(sunset / 1440) * 100 - 3}%, #3a3552 ${(sunset / 1440) * 100 + 2}%, #3a3552 100%)`
      : '#3a3552';

  return (
    <div className={cn('rounded-[var(--radius-card)] border border-line bg-raised shadow-float', compact ? 'p-3' : 'px-4 py-3')} data-testid="time-scrubber">
      <div className="flex items-center gap-3">
        <Button variant={playing ? 'primary' : 'subtle'} square aria-label={playing ? 'Pause the day loop' : 'Play a day loop'} aria-pressed={playing} onClick={togglePlaying}>
          {playing ? <IconPause /> : <IconPlay />}
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-mono text-lg font-medium tabular-nums" data-testid="clock">
              {formatClock(minutes)}
            </span>
            <span className="truncate text-xs text-muted">
              {sun.elevation_deg > 0 ? `Sun ${sun.elevation_deg.toFixed(0)}° high, ${compassWord(sun.azimuth_deg)} (${sun.azimuth_deg.toFixed(0)}°)` : 'Sun below the horizon'}
            </span>
          </div>
          <div className="relative mt-1.5">
            <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full opacity-80" style={{ background: dayGradient }} />
            <input
              type="range"
              min={0}
              max={1435}
              step={5}
              value={minutes}
              aria-label="Time of day"
              aria-valuetext={formatClock(minutes)}
              onChange={(e) => setTime({ datetime: withTimeOfDay(datetime, tz, Number(e.target.value)) })}
              className="relative block h-5 w-full cursor-pointer appearance-none bg-transparent [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-main [&::-moz-range-thumb]:bg-raised [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-main [&::-webkit-slider-thumb]:bg-raised"
            />
          </div>
          <div className="mt-0.5 flex justify-between text-[10px] text-faint tabular-nums">
            <span>12 am</span>
            <span>{sunrise !== null ? `sunrise ${formatClock(sunrise)}` : ''}</span>
            <span>noon</span>
            <span>{sunset !== null ? `sunset ${formatClock(sunset)}` : ''}</span>
            <span>12 am</span>
          </div>
        </div>
      </div>
      <div className="mt-2.5 flex flex-wrap items-center gap-1.5 border-t border-line pt-2.5">
        <input
          type="date"
          value={date}
          aria-label="Date"
          onChange={(e) => e.target.value && setTime({ datetime: withDate(datetime, tz, e.target.value) })}
          className="h-7 rounded-full border border-line bg-ink px-2.5 text-xs text-text"
        />
        {presets.map((p) => (
          <button
            key={p.label}
            type="button"
            aria-pressed={p.date === date}
            onClick={() => setTime({ datetime: withDate(datetime, tz, p.date) })}
            className={cn('h-7 rounded-full border px-2.5 text-xs font-semibold', p.date === date ? 'border-main bg-main text-on-main' : 'border-line text-muted hover:text-text')}
          >
            {p.label}
          </button>
        ))}
        <span className="ml-auto text-[10px] text-faint">{tz}</span>
      </div>
    </div>
  );
}
