'use client';

import type { NoiseScenario, PropertyTwin } from '@ithaca/schema';
import { useEffect, useRef, useState } from 'react';
import { Button, cn } from '@/components/ui';
import { IconPause, IconPlay, IconSound } from '@/components/ui/icons';
import { AURALIZATION, Auralizer, NO_TRAFFIC, auralizationParams, trafficTexture } from '@/lib/audio/auralizer';
import { describeLevel, noiseAt, trafficShare } from '@/lib/audio/levels';
import { useNoiseReport } from '@/lib/data/hooks';
import { roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';
import { useTour } from '@/lib/store/tour';
import { useThrottledDispatch } from '@/lib/store/useThrottledDispatch';
import { formatClock, minutesOfDay, withTimeOfDay } from '@/lib/time';

const AS_BUILT: NoiseScenario = { glazing: null, add_carpet: false, include_furniture: true };

/** A day in 36 s: 4 minutes of day per 100 ms tick. */
const STEP_MIN = 4;
const TICK_MS = 100;
/** Reverberation of open air, s: almost none. */
const OPEN_AIR_RT60 = 0.1;

const RANGE =
  'relative block h-6 w-full cursor-pointer appearance-none bg-transparent [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-main [&::-moz-range-thumb]:bg-raised [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-main [&::-webkit-slider-thumb]:bg-raised';

/**
 * A day of outside noise, heard from wherever the tour is: the room you stand in, or the
 * outside of its most exposed wall in the outside view. Time is `viewer.setTime`, shared with
 * the Sun Lab, the phone and the agent (A1).
 */
export function SoundTimelapse({ twin, active }: { twin: PropertyTwin; active: boolean }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const datetime = useSession((s) => s.viewer.datetime);
  const scenario = useSession((s) => s.viewer.sound_scenario?.scenario) ?? AS_BUILT;
  const outside = useTour((s) => s.place === 'outside');
  const setTime = useThrottledDispatch('viewer.setTime', 70);
  const tz = twin.timezone;
  const minutes = minutesOfDay(datetime, tz);
  const latest = useRef(datetime);
  latest.current = datetime;

  const [playing, setPlaying] = useState(false);
  const [listening, setListening] = useState(false);
  const [volume, setVolume] = useState(85 - AURALIZATION.defaultFullScaleDba);
  const [audioError, setAudioError] = useState<string | null>(null);
  const auralizer = useRef<Auralizer | null>(null);

  const report = useNoiseReport(twin.property_id, roomId, scenario);
  const outdoor = report.data?.outdoor_hourly?.length ? report.data.outdoor_hourly : null;
  const hourly = (outside ? outdoor : report.data?.hourly) ?? null;
  const rt60 = outside ? OPEN_AIR_RT60 : (report.data?.rt60_s ?? 0.5);
  const level = hourly ? noiseAt(hourly, minutes) : null;
  const peak = hourly?.length ? Math.max(...hourly.map((h) => h.laeq_dba)) : 0;
  const share = report.data ? trafficShare(report.data.sources) : 0;

  const params = level ? auralizationParams(level, rt60, 85 - volume) : null;
  const paramsKey = params ? [params.gain.toFixed(5), params.rt60_s, ...params.bandGains.map((g) => g.toFixed(4))].join() : '';
  const texture = level ? trafficTexture(level.laeq_dba, peak, share) : NO_TRAFFIC;

  useEffect(() => {
    if (!playing) return;
    const t = setInterval(() => {
      const next = (minutesOfDay(latest.current, tz) + STEP_MIN) % 1440;
      setTime({ datetime: withTimeOfDay(latest.current, tz, next) });
    }, TICK_MS);
    return () => clearInterval(t);
  }, [playing, tz, setTime]);

  useEffect(() => {
    if (!listening) return;
    if (params) auralizer.current?.update(params);
    else auralizer.current?.silence();
  }, [listening, paramsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (listening) auralizer.current?.setTraffic(texture);
  }, [listening, texture.share, texture.ratePerS]); // eslint-disable-line react-hooks/exhaustive-deps

  // Leaving the tour stops the day and the sound.
  useEffect(() => {
    if (active) return;
    setPlaying(false);
    setListening(false);
    auralizer.current?.stop();
  }, [active]);

  useEffect(
    () => () => {
      auralizer.current?.dispose();
      auralizer.current = null;
    },
    [],
  );

  const toggleListening = async () => {
    setAudioError(null);
    if (listening) {
      auralizer.current?.stop();
      setListening(false);
      return;
    }
    try {
      auralizer.current ??= new Auralizer();
      auralizer.current.setTraffic(texture);
      if (params) auralizer.current.update(params);
      await auralizer.current.start();
      if (!params) auralizer.current.silence();
      setListening(true);
    } catch (err) {
      setAudioError(err instanceof Error ? err.message : String(err));
    }
  };

  const where = outside ? 'Outside' : roomId ? roomName(twin, roomId) : null;
  let status: string;
  if (!roomId) status = 'Walk into a room to hear it';
  else if (report.loading) status = `${where}: loading…`;
  else if (report.error) status = `${where}: no noise estimate`;
  else if (outside && !outdoor) status = 'No outdoor levels yet; pick a room';
  else if (level) status = `${where} · ${level.laeq_dba.toFixed(0)} dBA, ${describeLevel(level.laeq_dba)}`;
  else status = `${where}: no noise estimate`;

  const bars = hourly ? [...hourly].sort((a, b) => a.hour - b.hour) : [];
  const low = bars.length ? Math.min(...bars.map((h) => h.laeq_dba)) : 0;
  const span = Math.max(1, peak - low);
  const hour = Math.floor(minutes / 60);

  return (
    <div
      className="w-[min(560px,calc(100%-24px))] rounded-[var(--radius-card)] border border-line bg-raised px-4 py-3 shadow-float"
      data-testid="sound-timelapse"
      data-listening={listening}
      data-playing={playing}
      data-laeq={level?.laeq_dba.toFixed(1)}
    >
      <div className="flex items-center gap-2">
        <Button variant={listening ? 'primary' : 'subtle'} square aria-pressed={listening} aria-label={listening ? 'Stop listening' : 'Listen to the outside from here'} onClick={toggleListening}>
          <IconSound />
        </Button>
        <Button variant={playing ? 'primary' : 'subtle'} square aria-pressed={playing} aria-label={playing ? 'Pause the day' : 'Play a day timelapse'} onClick={() => setPlaying(!playing)}>
          {playing ? <IconPause /> : <IconPlay />}
        </Button>
        <div className="ml-1 min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-mono text-lg font-medium tabular-nums">{formatClock(minutes)}</span>
            <span className="truncate text-xs text-muted" data-testid="sound-timelapse-status">
              {status}
            </span>
          </div>
          <div className="relative mt-1">
            <div aria-hidden className="absolute inset-x-0 bottom-1 flex h-5 items-end gap-px">
              {bars.map((h) => (
                <i
                  key={h.hour}
                  className={cn('flex-1 rounded-t-sm', h.hour === hour ? 'bg-main' : 'bg-line')}
                  style={{ height: `${20 + (80 * (h.laeq_dba - low)) / span}%` }}
                />
              ))}
            </div>
            <input
              type="range"
              min={0}
              max={1435}
              step={5}
              value={minutes}
              aria-label="Time of day"
              aria-valuetext={formatClock(minutes)}
              onChange={(e) => setTime({ datetime: withTimeOfDay(datetime, tz, Number(e.target.value)) })}
              className={RANGE}
            />
          </div>
        </div>
      </div>
      <div className="mt-2 flex items-center gap-3 border-t border-line pt-2">
        <p className="min-w-0 flex-1 text-[11px] leading-4 text-faint">
          A typical weekday, modelled from {twin.quality.traffic === 'aadt' ? 'traffic counts' : 'road-class defaults'}. Passing cars are added for feel; the average level is the model&apos;s.
        </p>
        <label className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted">
          Volume
          <input type="range" min={0} max={40} step={1} value={volume} onChange={(e) => setVolume(Number(e.target.value))} className="w-20" aria-valuetext={`${85 - volume} dBA at full volume`} />
        </label>
      </div>
      {audioError && <p className="mt-1 text-xs text-danger">{audioError}</p>}
    </div>
  );
}
