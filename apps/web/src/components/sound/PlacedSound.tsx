'use client';

import type { PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Segmented, cn } from '@/components/ui';
import { IconPause, IconPlay } from '@/components/ui/icons';
import { Auralizer, auralizationParams } from '@/lib/audio/auralizer';
import { useSoundField, useSoundPresets } from '@/lib/data/hooks';
import { interiorPoint } from '@/lib/geometry/polygon';
import { roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';

/** Below this a sound is lost under the quietest room's own background. */
const INAUDIBLE_DBA = 15;

/** How a placed sound comes across at a level, in words. */
function heard(dba: number): string {
  if (dba < 25) return 'barely audible';
  if (dba < 35) return 'faint';
  if (dba < 50) return 'clearly heard';
  if (dba < 65) return 'loud, like someone talking beside you';
  return 'very loud';
}

/**
 * A sound placed in the house: a TV, a crying baby, a dog. The server works out how it carries
 * through the real walls, doors and stairwell (`sound/field`); this shows how loud it is in every
 * room and plays it as it is heard in the room chosen above. The guide places sounds too
 * (`sound.placeSource`), and every screen in the session sees the same one.
 *
 * What plays is noise with the sound's pitch and the room's loudness and echo, not a recording
 * (A4: the audio follows the numbers, nothing else).
 */
export function PlacedSound({ twin, listenerRoomId, fullScale }: { twin: PropertyTwin; listenerRoomId: string; fullScale: number }) {
  const source = useSession((s) => s.viewer.sound_source);
  const dispatch = useSession((s) => s.dispatch);
  const presets = useSoundPresets(twin.property_id);
  const [doors, setDoors] = useState<'closed' | 'open'>('closed');
  const field = useSoundField(twin.property_id, source, doors);
  const [presetId, setPresetId] = useState('tv');
  const [playing, setPlaying] = useState(false);
  const [audioError, setAudioError] = useState<string | null>(null);
  const auralizer = useRef<Auralizer | null>(null);

  const choices = useMemo(() => (presets.data?.presets ?? []).filter((p) => p.preset_id !== 'recording'), [presets.data]);
  const here = field.data?.rooms.find((r) => r.room_id === listenerRoomId) ?? null;
  const params = useMemo(() => (here && here.laeq_dba > 0 ? auralizationParams(here, here.rt60_s, fullScale) : null), [here, fullScale]);
  const label = choices.find((p) => p.preset_id === source?.preset_id)?.label ?? source?.preset_id ?? '';

  useEffect(() => {
    if (params) auralizer.current?.update(params);
    else if (auralizer.current?.playing) {
      auralizer.current.stop();
      setPlaying(false);
    }
  }, [params]);
  useEffect(
    () => () => {
      auralizer.current?.dispose();
      auralizer.current = null;
    },
    [],
  );

  const stop = () => {
    auralizer.current?.stop();
    setPlaying(false);
  };
  const play = async () => {
    setAudioError(null);
    try {
      auralizer.current ??= new Auralizer();
      if (params) auralizer.current.update(params);
      await auralizer.current.start();
      setPlaying(true);
    } catch (err) {
      setAudioError(err instanceof Error ? err.message : String(err));
    }
  };

  // A sound that has just been placed (here, on a phone or by the guide) starts playing, as does
  // one already in the house when this view opens. A page that has not been clicked yet may not
  // start audio: then the play button is there for it.
  const placedKey = source ? `${source.room_id}:${source.preset_id}` : null;
  const wasPlaced = useRef(placedKey);
  const autoplay = useRef(placedKey !== null);
  useEffect(() => {
    if (placedKey === wasPlaced.current) return;
    wasPlaced.current = placedKey;
    if (placedKey) autoplay.current = true;
    else stop();
  }, [placedKey]);
  useEffect(() => {
    if (!autoplay.current || !params || playing) return;
    autoplay.current = false;
    if (navigator.userActivation?.hasBeenActive) void play();
  }, [params, playing]); // eslint-disable-line react-hooks/exhaustive-deps

  const place = () => {
    const room = twin.rooms.find((r) => r.room_id === listenerRoomId);
    if (!room) return;
    const [x, z] = interiorPoint(room.polygon);
    dispatch('sound.placeSource', { room_id: room.room_id, x: Number(x.toFixed(3)), z: Number(z.toFixed(3)), preset_id: presetId });
  };

  // No sound simulation here (fixture mode, or the server lacks it): say so once, quietly.
  if (presets.error) {
    return (
      <section className="rounded-xl border border-line bg-surface p-4" data-testid="placed-sound" data-available="false">
        <div className="text-xs text-muted">A sound in the house</div>
        <p className="mt-1 text-xs leading-5 text-faint">{presets.error.message}</p>
      </section>
    );
  }

  const loudest = field.data?.rooms.filter((r) => r.laeq_dba >= INAUDIBLE_DBA).slice(0, 7) ?? [];
  const top = loudest[0]?.laeq_dba ?? 1;

  return (
    <section className="rounded-xl border border-line bg-surface p-4" data-testid="placed-sound" data-source-room={source?.room_id ?? ''} data-playing={playing}>
      <div className="text-xs text-muted">A sound in the house</div>
      {!source ? (
        <>
          <p className="mt-1 text-[13px] leading-5 text-muted">Put a sound in {roomName(twin, listenerRoomId)}, then pick other rooms to hear how far it carries.</p>
          <select value={presetId} aria-label="Sound to place" onChange={(e) => setPresetId(e.target.value)} disabled={!choices.length} className="mt-3 h-9 w-full rounded-md border border-line bg-ink px-2 text-sm">
            {choices.map((p) => (
              <option key={p.preset_id} value={p.preset_id}>
                {p.label} ({p.level_dba.toFixed(0)} dBA)
              </option>
            ))}
          </select>
          <Button className="mt-2 w-full" variant="primary" disabled={!choices.length} onClick={place}>
            Place it here
          </Button>
        </>
      ) : (
        <>
          <div className="mt-1 flex items-center gap-3">
            <Button variant="primary" square onClick={playing ? stop : () => void play()} disabled={!params} aria-pressed={playing} aria-label={playing ? 'Stop the placed sound' : 'Hear the placed sound from this room'}>
              {playing ? <IconPause /> : <IconPlay />}
            </Button>
            <div className="min-w-0 text-sm leading-5">
              <div className="truncate font-medium">
                {label} in {roomName(twin, source.room_id)}
              </div>
              <div className="text-xs text-muted" data-testid="placed-sound-here">
                {field.error
                  ? field.error.message
                  : !here
                    ? 'Working out how it carries…'
                    : here.laeq_dba < INAUDIBLE_DBA
                      ? `Not heard in ${roomName(twin, listenerRoomId)}`
                      : `${here.laeq_dba.toFixed(0)} dBA in ${roomName(twin, listenerRoomId)}: ${heard(here.laeq_dba)}`}
              </div>
            </div>
          </div>
          <Segmented<'closed' | 'open'>
            className="mt-3 w-full [&>button]:flex-1 [&>button]:justify-center"
            label="Doors"
            size="sm"
            value={doors}
            options={[
              { value: 'closed', label: 'Doors closed' },
              { value: 'open', label: 'Doors open' },
            ]}
            onChange={setDoors}
          />
          {loudest.length > 0 && (
            <ul className="mt-3 space-y-1" aria-label="How loud the sound is in each room">
              {loudest.map((r) => {
                const on = r.room_id === listenerRoomId;
                return (
                  <li key={r.room_id}>
                    <button
                      type="button"
                      aria-pressed={on}
                      title={`Listen from ${roomName(twin, r.room_id)}`}
                      onClick={() => dispatch('sound.setScenario', { room_id: r.room_id, scenario: useSession.getState().viewer.sound_scenario?.scenario ?? {} })}
                      className={cn('relative flex w-full items-baseline justify-between gap-3 overflow-hidden rounded-md px-2 py-1 text-left text-[13px]', on ? 'font-semibold' : 'text-muted hover:text-text')}
                    >
                      <i aria-hidden className={cn('absolute inset-y-0 left-0 rounded-md', on ? 'bg-sun/35' : 'bg-ink')} style={{ width: `${Math.max(6, (r.laeq_dba / top) * 100)}%` }} />
                      <span className="relative truncate">{roomName(twin, r.room_id)}</span>
                      <span className="relative shrink-0 tabular-nums">{r.laeq_dba.toFixed(0)} dBA</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="mt-3 flex items-center justify-between gap-2">
            <span className="text-[11px] leading-4 text-faint">Played as noise with this sound's pitch, not a recording.</span>
            <Button size="sm" onClick={() => dispatch('sound.clearSource', {})}>
              Remove
            </Button>
          </div>
          {audioError && <p className="mt-2 text-xs text-danger">{audioError}</p>}
        </>
      )}
    </section>
  );
}
