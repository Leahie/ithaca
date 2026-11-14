'use client';

import type { PropertyTwin } from '@ithaca/schema';
import { cn } from '@/components/ui';
import { IconMute, IconSound } from '@/components/ui/icons';
import { SOUND_LAYERS, dayTypeId, levelAt, useSoundscape } from '@/lib/audio/soundscape';
import type { SoundLayer } from '@/lib/store/reducer';
import { useSession } from '@/lib/store/session';
import { toggleListening } from '@/lib/store/usePlayback';
import { minutesOfDay } from '@/lib/time';
import { CARD, OFF, ON } from './LightPanel';

/**
 * Which outside sounds the big screen plays: tap one to add or drop it. Each shows how loud it
 * is outside the house at the chosen time, so turning the day shows the birds waking up and the
 * highway filling. The choice is `viewer.playback.sounds`, shared with the big screen.
 */
export function SoundPicker({ twin }: { twin: PropertyTwin }) {
  const datetime = useSession((s) => s.viewer.datetime);
  const { sounds, listening } = useSession((s) => s.viewer.playback);
  const desktopOn = useSession((s) => s.peers.desktop);
  const dispatch = useSession((s) => s.dispatch);
  const soundscape = useSoundscape(twin.property_id);
  const dayType = dayTypeId(datetime, twin.timezone);
  const minutes = minutesOfDay(datetime, twin.timezone);

  const toggle = (id: SoundLayer) => dispatch('viewer.playback', { sounds: sounds.includes(id) ? sounds.filter((s) => s !== id) : [...sounds, id] });

  return (
    <div className={`px-4 pt-3 pb-4 ${CARD}`} data-testid="remote-sounds">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-[11px] font-semibold tracking-wider text-faint uppercase">Sounds outside</h2>
        <button type="button" disabled={!desktopOn} onClick={toggleListening} aria-pressed={listening}
          className={cn('flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-semibold disabled:opacity-40', listening ? ON : OFF)}>
          {listening ? <IconSound width={14} height={14} /> : <IconMute width={14} height={14} />}
          {listening ? 'Playing' : 'Off'}
        </button>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {SOUND_LAYERS.map((l) => {
          const on = sounds.includes(l.id);
          const now = soundscape.data ? levelAt(soundscape.data, dayType, l.sources, minutes) : null;
          return (
            <button key={l.id} type="button" aria-pressed={on} data-sound={l.id} onClick={() => toggle(l.id)}
              className={cn('flex min-w-0 flex-col items-start gap-0.5 rounded-xl border px-3 py-2 text-left', on ? ON : 'border-line bg-raised text-muted')}>
              <span className="w-full truncate text-sm leading-5 font-semibold">{l.label}</span>
              <span className="font-mono text-[11px] leading-4 tabular-nums opacity-80">{now ? (now.laeq_dba > 0 ? `${now.laeq_dba.toFixed(0)} dB` : 'quiet') : '–'}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
