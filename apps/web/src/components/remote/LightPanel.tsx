'use client';

import type { PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState } from 'react';
import { IconPause, IconPlay } from '@/components/ui/icons';
import { useSession } from '@/lib/store/session';
import { useThrottledDispatch } from '@/lib/store/useThrottledDispatch';
import { sunPosition, sunTimes } from '@/lib/sun/position';
import { formatClock, fromZonedParts, localDate, minutesOfDay, toZonedIso, withDate, withTimeOfDay, zonedParts } from '@/lib/time';
import { compassWord } from '@/components/sunlab/TimeScrubber';

const LOOP_STEP_MIN = 8;
const LOOP_TICK_MS = 100;
/** The big screen's glass, on the phone: dark, translucent, white type. */
const GLASS = 'border border-white/25 bg-main/85 text-white backdrop-blur-md';

/**
 * Time and date on the phone, in the look of the big screen's glass chips rather than a card:
 * a large clock, a day-long slider tinted by daylight, and a date picker. Every change is a
 * `viewer.setTime`, the same event the guide sends.
 */
export function LightPanel({ twin }: { twin: PropertyTwin }) {
  const datetime = useSession((s) => s.viewer.datetime);
  const setTime = useThrottledDispatch('viewer.setTime', 70);
  const [playing, setPlaying] = useState(false);
  const tz = twin.timezone;
  const minutes = minutesOfDay(datetime, tz);
  const date = localDate(datetime, tz);
  const latest = useRef(datetime);
  latest.current = datetime;

  const { sunrise, sunset } = useMemo(() => {
    const noon = fromZonedParts({ ...zonedParts(new Date(datetime), tz), hour: 12, minute: 0 }, tz);
    const t = sunTimes(noon, twin.lat, twin.lon);
    const toMin = (d: Date | null) => (d ? minutesOfDay(toZonedIso(d, tz), tz) : null);
    return { sunrise: toMin(t.sunrise), sunset: toMin(t.sunset) };
    // Sunrise and sunset depend on the date only.
  }, [date, tz, twin.lat, twin.lon]); // eslint-disable-line react-hooks/exhaustive-deps
  const sun = sunPosition(new Date(datetime), twin.lat, twin.lon);

  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => {
      const next = (minutesOfDay(latest.current, tz) + LOOP_STEP_MIN) % 1440;
      setTime({ datetime: withTimeOfDay(latest.current, tz, next) });
    }, LOOP_TICK_MS);
    return () => clearInterval(t);
  }, [playing, tz, setTime]);

  const pct = (m: number) => (m / 1440) * 100;
  const night = '#0d1a2b';
  const dayGradient =
    sunrise !== null && sunset !== null
      ? `linear-gradient(to right, ${night} 0%, ${night} ${pct(sunrise) - 2}%, #f1c84b ${pct(sunrise) + 3}%, #fff1c2 50%, #f1c84b ${pct(sunset) - 3}%, ${night} ${pct(sunset) + 2}%, ${night} 100%)`
      : night;
  const thumb = '[&::-webkit-slider-thumb]:h-7 [&::-webkit-slider-thumb]:w-7 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white [&::-webkit-slider-thumb]:bg-leaf [&::-moz-range-thumb]:h-7 [&::-moz-range-thumb]:w-7 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-white [&::-moz-range-thumb]:bg-leaf';

  return (
    <div className="space-y-3" data-testid="remote-time">
      <div className={`rounded-3xl px-5 pt-5 pb-4 ${GLASS}`}>
        <div className="flex items-end justify-between gap-3">
          <div>
            <div className="font-mono text-4xl leading-none font-medium tabular-nums" data-testid="clock">{formatClock(minutes)}</div>
            <div className="mt-2 text-sm text-white/75">
              {sun.elevation_deg > 0 ? `Sun ${sun.elevation_deg.toFixed(0)}° high in the ${compassWord(sun.azimuth_deg)}` : 'Sun below the horizon'}
            </div>
          </div>
          <button type="button" aria-label={playing ? 'Pause the day' : 'Play the day'} aria-pressed={playing} onClick={() => setPlaying(!playing)}
            className={`flex h-12 w-12 shrink-0 items-center justify-center rounded-full ${playing ? 'bg-leaf text-main' : 'border border-white/30 bg-white/10 text-white'}`}>
            {playing ? <IconPause width={20} height={20} /> : <IconPlay width={20} height={20} />}
          </button>
        </div>
        <div className="relative mt-5">
          <div className="absolute inset-x-0 top-1/2 h-2.5 -translate-y-1/2 rounded-full" style={{ background: dayGradient }} />
          <input type="range" min={0} max={1435} step={5} value={minutes} aria-label="Time of day" aria-valuetext={formatClock(minutes)}
            onChange={(e) => setTime({ datetime: withTimeOfDay(datetime, tz, Number(e.target.value)) })}
            className={`relative block h-9 w-full cursor-pointer appearance-none bg-transparent ${thumb}`} />
        </div>
        <div className="mt-1 flex justify-between text-[11px] text-white/60 tabular-nums">
          <span>{sunrise !== null ? `Sunrise ${formatClock(sunrise)}` : ''}</span>
          <span>{sunset !== null ? `Sunset ${formatClock(sunset)}` : ''}</span>
        </div>
      </div>

      <label className={`flex items-center justify-between gap-3 rounded-full py-2 pr-2 pl-5 ${GLASS}`}>
        <span className="text-sm text-white/75">Date</span>
        <input type="date" value={date} aria-label="Date" style={{ colorScheme: 'dark' }}
          onChange={(e) => e.target.value && setTime({ datetime: withDate(datetime, tz, e.target.value) })}
          className="h-11 min-w-0 flex-1 rounded-full border border-white/25 bg-white/10 px-4 text-right text-base text-white" />
      </label>
    </div>
  );
}
