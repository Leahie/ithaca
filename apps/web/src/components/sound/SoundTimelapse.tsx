'use client';

import type { NoiseScenario, PropertyTwin } from '@ithaca/schema';
import { useEffect, useRef, useState } from 'react';
import { cn, GLASS_PANEL, glassToggle } from '@/components/ui';
import { IconPause, IconPlay, IconSound } from '@/components/ui/icons';
import { AURALIZATION } from '@/lib/audio/auralizer';
import { noiseAt } from '@/lib/audio/levels';
import { SoundscapeMixer, indoors, type Mix } from '@/lib/audio/mixer';
import { SOUND_LAYERS, dayCurve, dayTypeId, levelAt, useSoundscape, type CurveHour, type SoundLevel } from '@/lib/audio/soundscape';
import { useNoiseReport } from '@/lib/data/hooks';
import { roomName } from '@/lib/rooms';
import type { SoundLayer } from '@/lib/store/reducer';
import { dispatch, useSession } from '@/lib/store/session';
import { useTour } from '@/lib/store/tour';
import { togglePlaying, toggleListening } from '@/lib/store/usePlayback';
import { useThrottledDispatch } from '@/lib/store/useThrottledDispatch';
import { formatClock, minutesOfDay, withTimeOfDay } from '@/lib/time';

const AS_BUILT: NoiseScenario = { glazing: null, add_carpet: false, include_furniture: true };

/** What a closed house stops, dB per octave band (63 Hz .. 8 kHz), until the room's estimate loads. */
const INSIDE_DEFAULT_DB = [12, 15, 20, 25, 30, 33, 35, 38];

/** Total dBA of several sounds playing at once (energy sum). */
const energySum = (levels: (SoundLevel | null)[]): number => {
  const e = levels.reduce((s, l) => s + (l && l.laeq_dba > 0 ? 10 ** (l.laeq_dba / 10) : 0), 0);
  return e > 0 ? 10 * Math.log10(e) : 0;
};

const RANGE =
  'relative block h-6 w-full cursor-pointer appearance-none bg-transparent [&::-moz-range-thumb]:h-4 [&::-moz-range-thumb]:w-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-main [&::-moz-range-thumb]:bg-raised [&::-webkit-slider-thumb]:h-4 [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-main [&::-webkit-slider-thumb]:bg-raised';
const TOGGLE = 'inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full border border-white/40 transition-colors';

/**
 * A day of outside noise, heard from wherever the tour is: the room you stand in, or the
 * outside of its most exposed wall in the outside view. Time is `viewer.setTime`, shared with
 * the phone and the agent (A1).
 */
export function SoundTimelapse({ twin, active }: { twin: PropertyTwin; active: boolean }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const datetime = useSession((s) => s.viewer.datetime);
  const scenario = useSession((s) => s.viewer.sound_scenario?.scenario) ?? AS_BUILT;
  const outside = useTour((s) => s.place === 'outside');
  const setTime = useThrottledDispatch('viewer.setTime', 70);
  const tz = twin.timezone;
  const minutes = minutesOfDay(datetime, tz);
  // Shared with the phone: either one plays the day or the sound, and both show it.
  const playing = useSession((s) => s.viewer.playback.playing);
  const wantSound = useSession((s) => s.viewer.playback.listening);
  const sounds = useSession((s) => s.viewer.playback.sounds);
  const [listening, setListening] = useState(false);
  // 0..40; the day's loudest hour plays `volume - 30` dB from full scale.
  const [volume, setVolume] = useState(24);
  const [audioError, setAudioError] = useState<string | null>(null);
  const mixer = useRef<SoundscapeMixer | null>(null);

  const report = useNoiseReport(twin.property_id, roomId, scenario);
  const outdoor = report.data?.outdoor_hourly?.length ? report.data.outdoor_hourly : null;
  const hourly = (outside ? outdoor : report.data?.hourly) ?? null;
  const level = hourly ? noiseAt(hourly, minutes) : null;
  const peak = hourly?.length ? Math.max(...hourly.map((h) => h.laeq_dba)) : 0;

  // Each chosen sound as loud as the soundscape says for this hour, season and day of the week.
  // Inside, every band loses what this room's walls and windows stop: outside less inside, per band.
  const soundscape = useSoundscape(twin.property_id).data;
  const dayType = dayTypeId(datetime, tz);
  const attenuationAt = (minute: number): number[] | null => {
    if (outside) return null;
    const out = outdoor ? noiseAt(outdoor, minute) : null;
    const inside = report.data?.hourly ? noiseAt(report.data.hourly, minute) : null;
    return out && inside ? out.bands_db.map((b, i) => Math.max(0, b - (inside.bands_db[i] ?? b))) : INSIDE_DEFAULT_DB;
  };
  const attenuation = attenuationAt(minutes);
  const outsideNow = (sources: string[]) => (soundscape ? levelAt(soundscape, dayType, sources, minutes) : null);
  const layer = (id: SoundLayer, sources: string[]) => (sounds.includes(id) ? indoors(outsideNow(sources), attenuation) : null);
  const outdoorDba = (id: SoundLayer) => (sounds.includes(id) ? outsideNow(SOUND_LAYERS.find((l) => l.id === id)!.sources)?.laeq_dba : undefined);
  const mix: Mix = {
    highway: layer('highway', ['highway']),
    // Without a soundscape for this home, street traffic is the room's own noise estimate.
    streets: soundscape ? layer('streets', ['arterial', 'local_streets', 'horns']) : sounds.includes('streets') ? level : null,
    birds: layer('birds', ['birds']),
    cicadas: layer('insects', ['cicadas']),
    crickets: layer('insects', ['night_insects']),
    frogs: layer('frogs', ['frogs']),
    dogs: layer('dogs', ['dogs']),
    outdoor: { birds: outdoorDba('birds'), streets: outdoorDba('streets'), dogs: outdoorDba('dogs') },
  };

  // The day curve is the chosen sounds where you are; without a soundscape, the room's estimate.
  const curve: CurveHour[] = soundscape
    ? dayCurve(soundscape, dayType, sounds, (l, h) => indoors(l, attenuationAt(h * 60 + 30)))
    : (hourly ?? []).map((h) => ({ hour: h.hour, laeq_dba: h.laeq_dba, loudest: null }));
  const nowDba = soundscape ? energySum([mix.highway, mix.streets, mix.birds, mix.cicadas, mix.crickets, mix.frogs, mix.dogs]) : (level?.laeq_dba ?? 0);
  const loudestNow = curve.find((h) => h.hour === Math.floor(minutes / 60))?.loudest ?? null;
  // Playback is scaled to this curve: its loudest hour plays clearly at the volume set, and
  // every other hour and sound keeps its real distance below it. Indoors the real levels are
  // 10 to 30 dBA, which at a fixed calibration would be silent.
  const dayPeak = curve.length ? Math.max(...curve.map((h) => h.laeq_dba)) : 0;
  const fullScale = dayPeak > 0 ? dayPeak + 30 - volume : AURALIZATION.defaultFullScaleDba;
  const mixKey = JSON.stringify(mix, (_, v: unknown) => (typeof v === 'number' ? Math.round(v * 2) / 2 : v)) + Math.round(fullScale);

  useEffect(() => {
    mixer.current?.update(mix, fullScale);
  }, [mixKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Leaving the tour stops the sound.
  useEffect(() => {
    if (!active && useSession.getState().viewer.playback.listening) dispatch('viewer.playback', { listening: false });
  }, [active]);

  useEffect(
    () => () => {
      mixer.current?.dispose();
      mixer.current = null;
    },
    [],
  );

  // The sound follows the shared switch, pressed here or on the phone.
  useEffect(() => {
    if (!wantSound) {
      mixer.current?.stop();
      setListening(false);
      return;
    }
    let cancelled = false;
    setAudioError(null);
    (async () => {
      try {
        mixer.current ??= new SoundscapeMixer();
        mixer.current.update(mix, fullScale);
        await mixer.current.start();
        if (!cancelled) setListening(true);
      } catch (err) {
        if (cancelled) return;
        setAudioError(err instanceof Error ? err.message : String(err));
        dispatch('viewer.playback', { listening: false });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [wantSound]); // eslint-disable-line react-hooks/exhaustive-deps

  const where = outside ? 'Outside' : roomId ? roomName(twin, roomId) : null;
  const loudestLabel = SOUND_LAYERS.find((l) => l.id === loudestNow)?.label;
  let status: string;
  if (!roomId) status = 'Walk into a room to hear it';
  else if (!sounds.length) status = `${where}: no sounds chosen`;
  else if (report.loading) status = `${where}: loading…`;
  else if (report.error && !soundscape) status = `${where}: no noise estimate`;
  else if (outside && !outdoor) status = 'No outdoor levels yet; pick a room';
  else if (nowDba > 0) status = `${where} · ${nowDba.toFixed(0)} dBA${loudestLabel ? `, mostly ${loudestLabel.toLowerCase()}` : ''}`;
  else status = `${where}: quiet now`;

  const bars = [...curve].sort((a, b) => a.hour - b.hour);
  const audible = bars.filter((h) => h.laeq_dba > 0);
  const low = audible.length ? Math.min(...audible.map((h) => h.laeq_dba)) : 0;
  const top = audible.length ? Math.max(...audible.map((h) => h.laeq_dba)) : peak;
  const span = Math.max(1, top - low);
  const hour = Math.floor(minutes / 60);
  const colorOf = (id: SoundLayer | null) => SOUND_LAYERS.find((l) => l.id === id)?.color;
  const season = dayType.split('-')[0];
  const weekend = dayType.endsWith('weekend');

  return (
    <div
      className={`w-[min(560px,calc(100%-24px))] rounded-3xl px-4 py-3 ${GLASS_PANEL}`}
      data-testid="sound-timelapse"
      data-listening={listening}
      data-playing={playing}
      data-laeq={level?.laeq_dba.toFixed(1)}
    >
      <div className="flex items-center gap-2">
        <button type="button" aria-pressed={listening} aria-label={listening ? 'Stop listening' : 'Listen to the outside from here'} onClick={toggleListening} className={cn(TOGGLE, glassToggle(listening))}>
          <IconSound />
        </button>
        <button type="button" aria-pressed={playing} aria-label={playing ? 'Pause the day' : 'Play a day timelapse'} onClick={togglePlaying} className={cn(TOGGLE, glassToggle(playing))}>
          {playing ? <IconPause /> : <IconPlay />}
        </button>
        <div className="ml-1 min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-mono text-lg font-medium tabular-nums">{formatClock(minutes)}</span>
            <span className="truncate text-xs text-white/75" data-testid="sound-timelapse-status">
              {status}
            </span>
          </div>
          <div className="relative mt-1">
            <div aria-hidden className="absolute inset-x-0 bottom-1 flex h-5 items-end gap-px">
              {bars.map((h) => {
                const color = colorOf(h.loudest);
                return (
                  <i
                    key={h.hour}
                    data-hour={h.hour}
                    data-dba={h.laeq_dba.toFixed(1)}
                    className={cn('flex-1 rounded-t-sm', !color && (h.hour === hour ? 'bg-white' : 'bg-white/25'))}
                    style={{ height: h.laeq_dba > 0 ? `${12 + (88 * (h.laeq_dba - low)) / span}%` : '4%', backgroundColor: color, opacity: color ? (h.hour === hour ? 1 : 0.45) : undefined }}
                  />
                );
              })}
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
      <div className="mt-2 flex items-center gap-3 border-t border-white/20 pt-2">
        <div className="min-w-0 flex-1 space-y-1">
          {soundscape && sounds.length > 0 && (
            <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 text-[11px] text-white/75" data-testid="sound-legend">
              {SOUND_LAYERS.filter((l) => sounds.includes(l.id)).map((l) => (
                <span key={l.id} className="inline-flex items-center gap-1">
                  <i className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: l.color }} />
                  {l.label}
                </span>
              ))}
            </div>
          )}
          <p className="text-[11px] leading-4 text-white/60">
            A typical {season} {weekend ? 'weekend day' : 'weekday'}.
          </p>
        </div>
        <label className="flex shrink-0 items-center gap-1.5 text-[11px] text-white/75">
          Volume
          <input type="range" min={0} max={40} step={1} value={volume} onChange={(e) => setVolume(Number(e.target.value))} className="w-20 accent-white" aria-valuetext={`loudest hour ${volume - 30} dB from full scale`} />
        </label>
      </div>
      {audioError && <p className="mt-1 text-xs text-coral">{audioError}</p>}
    </div>
  );
}
