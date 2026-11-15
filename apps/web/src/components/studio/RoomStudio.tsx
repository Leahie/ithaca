'use client';

import type { PropertyTwin, Room } from '@ithaca/schema';
import dynamic from 'next/dynamic';
import { useEffect, useMemo, useRef, useState } from 'react';
import { cursorKey, drawCursors, watchCanvas } from '@/components/hands/HandControl';
import { GLASS_PANEL, cn, glassButton } from '@/components/ui';
import { IconChevronLeft, IconClose, IconMic, IconSend, IconSparkle } from '@/components/ui/icons';
import { api } from '@/lib/api';
import { useHouseFurniture } from '@/lib/data/hooks';
import { bounds } from '@/lib/geometry/polygon';
import { handEngine, onHandTick, useHands } from '@/lib/hands/store';
import { roomArea, roomName } from '@/lib/rooms';
import { useStudioVoice } from '@/lib/studio/request';
import { studioLive, useStudio, type StudioGesture, type StudioPhase } from '@/lib/studio/store';
import { onSessionEvent, useSession } from '@/lib/store/session';

/**
 * The room studio (experimental): pick a living space and it comes out of the house on its own,
 * to be turned by hand, marked by hand and furnished by voice. See lib/studio/store.ts.
 */

const StudioScene = dynamic(() => import('./StudioScene').then((m) => m.StudioScene), { ssr: false });
const HandCam = dynamic(() => import('./HandCam').then((m) => m.HandCam), { ssr: false });

/** Rooms people live in: the bedrooms, the living room and their like. A house whose rooms carry other names offers its larger rooms. */
const LIVING = /bed|living|family|great|lounge|den|sitting|primary|master|guest|nursery|kids|office|study|dining|play|media/i;
export function studioRooms(twin: PropertyTwin): Room[] {
  const named = twin.rooms.filter((r) => LIVING.test(r.label));
  const rooms = named.length ? named : twin.rooms.filter((r) => roomArea(r) >= 7);
  return [...rooms].sort((a, b) => roomName(twin, a.room_id).localeCompare(roomName(twin, b.room_id), 'en', { numeric: true }));
}

const STAGE = { background: 'radial-gradient(120% 95% at 50% 40%, #12463a 0%, #082a22 42%, #020d0a 100%)' } as const;

/** The room's outline, as a small plan. */
function Outline({ room, className }: { room: Room; className?: string }) {
  const b = bounds(room.polygon);
  const w = Math.max(b.maxX - b.minX, 0.1);
  const h = Math.max(b.maxZ - b.minZ, 0.1);
  const pad = Math.max(w, h) * 0.08;
  const points = room.polygon.map(([x, z]) => `${(x - b.minX).toFixed(2)},${(z - b.minZ).toFixed(2)}`).join(' ');
  return (
    <svg viewBox={`${-pad} ${-pad} ${w + pad * 2} ${h + pad * 2}`} className={className} aria-hidden>
      <polygon points={points} fill="rgba(43,220,134,0.14)" stroke="#2bdc86" strokeWidth={Math.max(w, h) * 0.022} strokeLinejoin="round" />
    </svg>
  );
}

function Picker({ twin, onPick }: { twin: PropertyTwin; onPick(roomId: string): void }) {
  const rooms = useMemo(() => studioRooms(twin), [twin]);
  const here = useSession((s) => s.viewer.room_id);
  const furniture = useHouseFurniture(twin.property_id);
  const count = (roomId: string) => (furniture.data ?? []).filter((p) => p.room_id === roomId).length;
  return (
    <div className="absolute inset-0 z-30 overflow-y-auto px-6 pt-24 pb-10">
      <div className="mx-auto max-w-5xl">
        <p className="text-[12px] font-semibold tracking-[0.14em] text-leaf uppercase">Room studio · experimental</p>
        <h1 className="mt-2 text-4xl text-white">Which room shall we work on?</h1>
        <p className="mt-3 max-w-2xl text-[17px] leading-7 text-white/75">
          The room comes out of the house on its own. Turn it with a pinch, show with your hands where a piece should go and how big it is, and say what you want there.
        </p>
        <ul className="mt-8 grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-4">
          {rooms.map((room, i) => {
            const pieces = count(room.room_id);
            return (
              <li key={room.room_id} className="studio-rise" style={{ animationDelay: `${i * 45}ms` }}>
                <button
                  type="button"
                  onClick={() => onPick(room.room_id)}
                  data-room-id={room.room_id}
                  className={cn(
                    'group flex w-full flex-col rounded-3xl border bg-white/[0.06] p-4 text-left transition duration-200 hover:-translate-y-1 hover:border-leaf hover:bg-white/[0.11] focus-visible:outline-leaf',
                    room.room_id === here ? 'border-leaf/70' : 'border-white/15',
                  )}
                >
                  <Outline room={room} className="h-32 w-full transition-transform duration-300 group-hover:scale-105" />
                  <span className="mt-3 font-display text-lg font-semibold text-white">{roomName(twin, room.room_id)}</span>
                  <span className="mt-0.5 text-sm text-white/60 tabular-nums">
                    {roomArea(room).toFixed(0)} m² · {pieces ? `${pieces} ${pieces === 1 ? 'piece' : 'pieces'}` : 'empty'}
                    {room.room_id === here ? ' · you are here' : ''}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

/** A ring for each hand over the whole studio, as over the house. */
function HandCursors() {
  const status = useHands((s) => s.status);
  const canvas = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    if (status !== 'on' || !canvas.current) return;
    const [surface, unwatch] = watchCanvas(canvas.current);
    const off = onHandTick(() => {
      const hands = handEngine.hands;
      const zooming = handEngine.gesture === 'zoom';
      const key = cursorKey(surface, hands, zooming);
      if (key === surface.drawn) return;
      drawCursors(surface, hands, zooming);
      surface.drawn = key;
    });
    return () => {
      off();
      unwatch();
    };
  }, [status]);
  return <canvas ref={canvas} className="pointer-events-none absolute inset-0 z-20 h-full w-full" aria-hidden />;
}

const GESTURES: { key: StudioGesture; move: string; does: string }[] = [
  { key: 'spin', move: 'Pinch and drag', does: 'turn and tilt the room' },
  { key: 'zoom', move: 'Pinch with both hands', does: 'pull apart to zoom, move together to slide' },
  { key: 'frame', move: 'Hold both hands apart', does: 'mark where, and how wide: 20 cm of hands is 1 m' },
  { key: 'pin', move: 'Point one finger', does: 'pin a spot' },
  { key: 'remove', move: 'Hold a fist over a piece', does: 'take it out of the room' },
];

/** What the hands can do here, with the one they are doing lit. */
function GestureGuide() {
  const [now, setNow] = useState<StudioGesture>('idle');
  useEffect(() => {
    let last: StudioGesture = 'idle';
    const read = () => {
      if (studioLive.gesture === last) return;
      last = studioLive.gesture;
      setNow(last);
    };
    const off = onHandTick(read);
    // The mouse marks too, with no hand tick to say so.
    const t = setInterval(read, 150);
    return () => {
      off();
      clearInterval(t);
    };
  }, []);
  return (
    <div className={cn('pointer-events-auto w-[300px] rounded-3xl p-2', GLASS_PANEL)} data-testid="studio-gestures">
      <ul className="space-y-1">
        {GESTURES.map((g) => (
          <li key={g.key} className={cn('rounded-2xl px-3 py-2 transition-colors duration-150', now === g.key ? 'bg-leaf/25 ring-1 ring-leaf/70 ring-inset' : '')}>
            <div className="font-display text-[15px] font-semibold text-white">{g.move}</div>
            <div className="text-[13px] text-white/65">{g.does}</div>
          </li>
        ))}
      </ul>
      <p className="px-3 pt-2 pb-1 text-[12px] text-white/45">With a mouse: drag, wheel, Shift-drag on the floor to mark.</p>
    </div>
  );
}

const WORKING: Partial<Record<StudioPhase, string>> = { searching: 'Searching the library', fetching: 'Fetching the model', placing: 'Standing it in the room' };

/** The talking part: the microphone, what was heard, what the studio answers, and a line to type in instead. */
function VoiceBar({ twin, room }: { twin: PropertyTwin; room: Room }) {
  const ctx = useMemo(() => ({ propertyId: twin.property_id, room }), [twin.property_id, room]);
  const voice = useStudioVoice(ctx);
  const phase = useStudio((s) => s.phase);
  const heard = useStudio((s) => s.heard);
  const reply = useStudio((s) => s.reply);
  const mark = useStudio((s) => s.mark);
  const piece = useStudio((s) => s.piece);
  const [draft, setDraft] = useState('');

  // The guide's answer to anything that was not about furniture is shown here too.
  useEffect(
    () =>
      onSessionEvent((event) => {
        if (event.type === 'agent.message' && !event.member_id) useStudio.getState().patch({ reply: event.payload.text });
      }),
    [],
  );

  // Hold the space bar to talk.
  const voiceRef = useRef(voice);
  voiceRef.current = voice;
  useEffect(() => {
    const typing = (e: KeyboardEvent) => e.target instanceof HTMLElement && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable);
    const down = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || e.repeat || typing(e)) return;
      e.preventDefault();
      voiceRef.current.start();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code !== 'Space' || typing(e)) return;
      e.preventDefault();
      voiceRef.current.stop();
    };
    window.addEventListener('keydown', down);
    window.addEventListener('keyup', up);
    return () => {
      window.removeEventListener('keydown', down);
      window.removeEventListener('keyup', up);
    };
  }, []);

  const working = WORKING[phase];
  const hint = mark
    ? mark.width
      ? `Marked: ${mark.width.toFixed(2)} m wide. Now say what goes there: “a sofa”, “a reading chair”.`
      : 'Spot pinned. Now say what goes there.'
    : 'Hold both hands apart over the floor to mark a spot, then say what goes there.';
  const level = voice.listening ? (voice.level ?? 0) : 0;

  return (
    <div className={cn('pointer-events-auto flex w-[min(760px,calc(100vw-2rem))] items-center gap-4 rounded-[28px] py-3 pr-4 pl-3', GLASS_PANEL)} data-testid="studio-voice" data-phase={phase}>
      <button
        type="button"
        onClick={voice.toggle}
        disabled={!voice.supported}
        aria-pressed={voice.listening}
        aria-label={voice.listening ? 'Stop listening' : 'Talk'}
        title={voice.supported ? 'Tap and talk, or hold the space bar' : 'Speech recognition is not available in this browser: type instead'}
        className={cn(
          'relative grid h-16 w-16 shrink-0 place-items-center rounded-full transition-colors disabled:opacity-40',
          voice.listening ? 'bg-leaf text-main' : 'bg-white/12 text-white ring-1 ring-white/35 ring-inset hover:bg-white/20',
        )}
      >
        {/* How loud it hears you. */}
        <span aria-hidden className="absolute inset-0 rounded-full ring-2 ring-leaf transition-transform duration-100" style={{ transform: `scale(${1 + level * 0.55})`, opacity: voice.listening ? 0.7 : 0 }} />
        <IconMic width={26} height={26} />
      </button>
      <div className="min-w-0 flex-1">
        <p className={cn('truncate font-display text-xl leading-7 font-semibold', heard ? 'text-white' : 'text-white/55')} aria-live="polite">
          {heard ? `“${heard}”` : voice.listening ? 'Listening…' : 'Tap the microphone, or hold space, and ask for a piece'}
        </p>
        <p className="flex min-h-6 items-center gap-2 text-[15px] leading-6 text-white/75" aria-live="polite">
          {working && <span className="h-2 w-2 shrink-0 animate-ping rounded-full bg-leaf" aria-hidden />}
          <span className={cn('truncate', phase === 'error' && 'text-[#ffb4a8]')}>{voice.error ?? reply ?? (working ? `${working}…` : hint)}</span>
        </p>
      </div>
      {piece?.thumbnail && (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={api().assetUrl(piece.thumbnail)} alt="" className="h-16 w-16 shrink-0 rounded-2xl bg-white object-contain p-1" />
      )}
      {mark && !working && (
        <button type="button" onClick={() => useStudio.getState().setMark(null)} className={glassButton()} title="Take the mark off the floor">
          Clear mark
        </button>
      )}
      <form
        className="flex shrink-0 items-center gap-1.5"
        onSubmit={(e) => {
          e.preventDefault();
          if (!draft.trim()) return;
          voice.submit(draft.trim());
          setDraft('');
        }}
      >
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="or type: a sofa"
          aria-label="Type what goes on the mark"
          className="h-10 w-40 rounded-full border border-white/30 bg-white/10 px-3.5 text-sm text-white placeholder:text-white/45 focus:border-leaf focus:outline-none"
        />
        <button type="submit" disabled={!draft.trim()} aria-label="Send" className="grid h-10 w-10 place-items-center rounded-full border border-white/40 text-white hover:bg-white/15 disabled:opacity-40">
          <IconSend width={16} height={16} />
        </button>
      </form>
    </div>
  );
}

function Studio({ twin }: { twin: PropertyTwin }) {
  const roomId = useStudio((s) => s.roomId);
  const pickRoom = useStudio((s) => s.pickRoom);
  const exit = useStudio((s) => s.exit);
  const rooms = useMemo(() => studioRooms(twin), [twin]);
  const room = twin.rooms.find((r) => r.room_id === roomId) ?? null;

  // Hands are what the studio is for: the camera comes on with the room.
  useEffect(() => {
    if (room && useHands.getState().status === 'off') void useHands.getState().start();
  }, [room]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (useStudio.getState().roomId) pickRoom(null);
      else exit();
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [pickRoom, exit]);

  return (
    <div className="fixed inset-0 z-50 overflow-hidden text-white" style={STAGE} data-testid="room-studio" data-room-id={roomId ?? ''} role="dialog" aria-label="Room studio">
      {room && (
        <>
          <div className="studio-fade absolute inset-0">
            <StudioScene key={room.room_id} twin={twin} room={room} />
          </div>
          {/* The floor's grid runs out into the dark at the edges. */}
          <div aria-hidden className="pointer-events-none absolute inset-0 z-10" style={{ background: 'radial-gradient(ellipse 75% 70% at 50% 46%, transparent 58%, #020d0a 100%)' }} />
          <HandCursors />
        </>
      )}

      <div className="pointer-events-none absolute inset-x-4 top-4 z-40 flex items-start justify-between gap-4">
        <div className="pointer-events-auto flex min-w-0 flex-col gap-3">
          <div className="flex items-center gap-3">
            <button type="button" onClick={room ? () => pickRoom(null) : exit} className={cn(glassButton(), 'h-11 gap-1 bg-black/40 pr-5 pl-3 backdrop-blur-md')}>
              <IconChevronLeft width={18} height={18} />
              {room ? 'Rooms' : 'Back to the house'}
            </button>
            {room && (
              <div className="min-w-0">
                <p className="text-[11px] font-semibold tracking-[0.14em] text-leaf uppercase">Room studio · experimental</p>
                <h1 className="truncate text-3xl leading-9 text-white">{roomName(twin, room.room_id)}</h1>
              </div>
            )}
          </div>
          {room && rooms.length > 1 && (
            <div className="flex max-w-[52vw] flex-wrap gap-1.5">
              {rooms.map((r) => (
                <button
                  key={r.room_id}
                  type="button"
                  onClick={() => pickRoom(r.room_id)}
                  aria-current={r.room_id === room.room_id}
                  className={cn('h-8 rounded-full px-3 text-[13px] font-semibold backdrop-blur-md transition-colors', r.room_id === room.room_id ? 'bg-leaf text-main' : 'bg-black/40 text-white/80 ring-1 ring-white/25 ring-inset hover:bg-white/15')}
                >
                  {roomName(twin, r.room_id)}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="pointer-events-auto flex items-start gap-2">
          {room && <HandCam className="w-[340px] max-w-[34vw]" />}
          <button type="button" onClick={exit} aria-label="Leave the room studio" title="Back to the house" className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-black/40 text-white ring-1 ring-white/30 backdrop-blur-md ring-inset hover:bg-white/15">
            <IconClose width={18} height={18} />
          </button>
        </div>
      </div>

      {room ? (
        <>
          <div className="pointer-events-none absolute bottom-4 left-4 z-40 hidden lg:block">
            <GestureGuide />
          </div>
          <div className="pointer-events-none absolute inset-x-0 bottom-4 z-40 flex justify-center px-4 lg:pl-[332px]">
            <VoiceBar key={room.room_id} twin={twin} room={room} />
          </div>
        </>
      ) : (
        <Picker twin={twin} onPick={pickRoom} />
      )}
    </div>
  );
}

/** Mounted with the house; draws nothing until the studio is opened. */
export function RoomStudio({ twin }: { twin: PropertyTwin }) {
  const open = useStudio((s) => s.open);
  // Leaving the house closes it.
  useEffect(() => () => useStudio.getState().exit(), []);
  return open ? <Studio twin={twin} /> : null;
}

/** The way in, for the top bar. */
export function StudioButton({ className }: { className?: string }) {
  return (
    <button type="button" onClick={() => useStudio.getState().enter()} data-testid="studio-open" title="Take one room out of the house: turn it by hand, mark the floor with your hands, and say what goes there" className={className}>
      <IconSparkle width={18} height={18} />
      Studio
    </button>
  );
}
