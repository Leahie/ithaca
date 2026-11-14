'use client';

import type { PropertyTwin } from '@ithaca/schema';
import { useMemo } from 'react';
import { IconPause, IconPlay } from '@/components/ui/icons';
import { useSession } from '@/lib/store/session';
import { togglePlaying } from '@/lib/store/usePlayback';
import { useThrottledDispatch } from '@/lib/store/useThrottledDispatch';
import { sunPosition, sunTimes } from '@/lib/sun/position';
import { formatClock, fromZonedParts, localDate, minutesOfDay, toZonedIso, withDate, withTimeOfDay, zonedParts } from '@/lib/time';
import { compassWord } from '@/components/sunlab/TimeScrubber';

/** The remote's card: outlined, light, as on every other screen. */
export const CARD = 'rounded-2xl border border-line bg-surface text-text';
/** Something switched on: outlined in green on a light green. */
export const ON = 'border-leaf-deep/40 bg-leaf/15 text-leaf-deep';
export const OFF = 'border-line bg-raised text-text';

/**
 * Time and date on the phone: a large clock, a day-long slider tinted by daylight, and a date
 * picker. Every change is a `viewer.setTime`, the same event the guide sends.
 */
export function LightPanel({ twin }: { twin: PropertyTwin }) {
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

  const pct = (m: number) => (m / 1440) * 100;
  const night = '#3d5260';
  const dayGradient =
    sunrise !== null && sunset !== null
      ? `linear-gradient(to right, ${night} 0%, ${night} ${pct(sunrise) - 2}%, #f1c84b ${pct(sunrise) + 3}%, #fff1c2 50%, #f1c84b ${pct(sunset) - 3}%, ${night} ${pct(sunset) + 2}%, ${night} 100%)`
      : night;
  const thumb = '[&::-webkit-slider-thumb]:h-7 [&::-webkit-slider-thumb]:w-7 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white [&::-webkit-slider-thumb]:bg-leaf-deep [&::-webkit-slider-thumb]:shadow [&::-moz-range-thumb]:h-7 [&::-moz-range-thumb]:w-7 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-white [&::-moz-range-thumb]:bg-leaf-deep';

  return (
    <div className="space-y-3" data-testid="remote-time">
      <div className={`px-4 pt-4 pb-3 ${CARD}`}>
        <div className="flex items-end justify-between gap-3">
          <div>
            <div className="font-mono text-4xl leading-none font-medium tabular-nums" data-testid="clock">{formatClock(minutes)}</div>
            <div className="mt-2 text-sm text-muted">
              {sun.elevation_deg > 0 ? `Sun ${sun.elevation_deg.toFixed(0)}° high in the ${compassWord(sun.azimuth_deg)}` : 'Sun below the horizon'}
            </div>
          </div>
          <button type="button" aria-label={playing ? 'Pause the day' : 'Play the day'} aria-pressed={playing} onClick={togglePlaying}
            className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full border ${playing ? ON : OFF}`}>
            {playing ? <IconPause width={18} height={18} /> : <IconPlay width={18} height={18} />}
          </button>
        </div>
        <div className="relative mt-5">
          <div className="absolute inset-x-0 top-1/2 h-2.5 -translate-y-1/2 rounded-full" style={{ background: dayGradient }} />
          <input type="range" min={0} max={1435} step={5} value={minutes} aria-label="Time of day" aria-valuetext={formatClock(minutes)}
            onChange={(e) => setTime({ datetime: withTimeOfDay(datetime, tz, Number(e.target.value)) })}
            className={`relative block h-9 w-full cursor-pointer appearance-none bg-transparent ${thumb}`} />
        </div>
        <div className="mt-1 flex justify-between text-[11px] text-muted tabular-nums">
          <span>{sunrise !== null ? `Sunrise ${formatClock(sunrise)}` : ''}</span>
          <span>{sunset !== null ? `Sunset ${formatClock(sunset)}` : ''}</span>
        </div>
        <label className="mt-3 flex items-center justify-between gap-3 border-t border-line pt-3">
          <span className="text-sm text-muted">Date</span>
          <input type="date" value={date} aria-label="Date"
            onChange={(e) => e.target.value && setTime({ datetime: withDate(datetime, tz, e.target.value) })}
            className="h-10 min-w-0 flex-1 rounded-full border border-line bg-raised px-4 text-right text-base text-text" />
        </label>
      </div>
    </div>
  );
}
