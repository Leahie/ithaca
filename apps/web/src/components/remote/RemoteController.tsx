'use client';

import type { PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState } from 'react';
import { SessionProvider, useSessionInfo } from '@/components/session/SessionProvider';
import { Badge, cn, Notice, Segmented, Spinner, Toggle } from '@/components/ui';
import { IconChat, IconDesign, IconMap, IconMic, IconMute, IconPause, IconPlay, IconSend, IconSound, IconSun, IconTour } from '@/components/ui/icons';
import { useTwin } from '@/lib/data/hooks';
import { roomArea, roomName, roomsOnFloor, sweepRooms } from '@/lib/rooms';
import { useAnswerMic } from '@/lib/speech/useAnswerMic';
import { usePushToTalk } from '@/lib/speech/usePushToTalk';
import { useFamily, useMemberName } from '@/lib/store/family';
import { useSession } from '@/lib/store/session';
import { togglePlaying, toggleListening } from '@/lib/store/usePlayback';
import { RoomIcon } from '@/components/ui/RoomIcon';
import { FurnitureRoom } from './FurnitureRoom';
import { LightPanel, OFF, ON } from './LightPanel';
import { CatalogSuggestion } from '@/components/house/CatalogSuggestion';
import { MovePad } from './MovePad';
import { SoundPicker } from './SoundPicker';

/**
 * The conversation as messages: yours on the right, the guide's on the left, newest at the bottom
 * and kept in view. The box to type in stays put under it.
 */
function Messages({ memberId }: { memberId: string | null }) {
  const chat = useSession((s) => s.chat);
  const interim = useSession((s) => s.interim);
  const nameOf = useMemberName();
  const end = useRef<HTMLDivElement>(null);
  const mine = memberId
    ? chat.filter((c) => (c.kind === 'ask' ? c.from_member_id === memberId || c.to_member_id === memberId : c.kind === 'note' || c.member_id === memberId))
    : chat;
  const lastText = mine[mine.length - 1];
  const tail = lastText && 'text' in lastText ? lastText.text.length : 0;
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' });
  }, [mine.length, tail, interim?.text]);

  const left = 'max-w-[85%] self-start rounded-2xl rounded-bl-md border border-line bg-raised px-3.5 py-2 text-[15px] leading-[1.45]';
  const right = 'max-w-[85%] self-end rounded-2xl rounded-br-md bg-main px-3.5 py-2 text-[15px] leading-[1.45] text-on-main';
  const aside = 'self-center text-center text-xs text-muted';
  return (
    <div className="flex min-h-full flex-col justify-end gap-2" aria-live="polite" data-testid="remote-feed">
      {!mine.length && !interim && <p className={aside}>Say or type anything. For example: “Take me to the kitchen.”</p>}
      {mine.map((c) =>
        c.kind === 'user' ? (
          <p key={c.id} className={right}>{c.text}</p>
        ) : c.kind === 'agent' ? (
          <p key={c.id} className={cn(left, c.streaming && 'streaming-caret')}>{c.text}</p>
        ) : c.kind === 'action' ? (
          <p key={c.id} className={aside}>✓ {c.summary}</p>
        ) : c.kind === 'note' ? (
          <p key={c.id} className={left}>
            <span className="font-semibold">{nameOf(c.member_id) ? `${nameOf(c.member_id)}'s guide` : 'The guide'}</span> on the family board: {c.text}
          </p>
        ) : c.kind === 'ask' ? (
          <p key={c.id} className={left}>
            <span className="font-semibold">{nameOf(c.to_member_id) ?? 'Another member'}'s guide</span>: {c.answer ?? 'thinking…'}
          </p>
        ) : c.kind === 'layouts' ? (
          <p key={c.id} className={aside}>{c.layout_ids.length} layout option{c.layout_ids.length === 1 ? '' : 's'} on the big screen</p>
        ) : c.kind === 'catalog' ? (
          // The pieces the guide suggests, with pictures: tap one to add it to the room.
          <div key={c.id} className="w-full self-stretch" data-testid="remote-catalog">
            <CatalogSuggestion propertyId={c.property_id} roomId={c.room_id} nearItemId={c.near_item_id} pieces={c.pieces} />
          </div>
        ) : (
          <p key={c.id} className="self-center text-xs text-danger">{c.message}</p>
        ),
      )}
      {interim && <p className={cn(right, 'opacity-60')}>{interim.text || 'Listening…'}</p>}
      <div ref={end} />
    </div>
  );
}

type RemoteView = 'move' | 'rooms' | 'guide' | 'furniture' | 'light';
const VIEWS: { view: RemoteView; label: string; icon: typeof IconTour }[] = [
  { view: 'rooms', label: 'Rooms', icon: IconMap },
  { view: 'guide', label: 'Guide', icon: IconChat },
  { view: 'move', label: 'Move', icon: IconTour },
  { view: 'furniture', label: 'Furniture', icon: IconDesign },
  { view: 'light', label: 'Env', icon: IconSun },
];

/** The floor shown on the Rooms screen: the one picked there, else the one you are on. */
function shownFloor(twin: PropertyTwin, picked: string | null, roomId: string | null): string | undefined {
  return picked ?? twin.rooms.find((r) => r.room_id === roomId)?.floor_id ?? [...twin.floors].sort((a, b) => a.index - b.index)[0]?.floor_id;
}

/** Big blocks to tap, one floor at a time. That floor's map is on the big screen while this is open. */
function RoomBlocks({ twin, floorId, onFloor, onPicked }: { twin: PropertyTwin; floorId: string | undefined; onFloor(floorId: string): void; onPicked(): void }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  const floors = [...twin.floors].sort((a, b) => a.index - b.index);
  const floor = floors.find((f) => f.floor_id === floorId);
  const rooms = floor ? roomsOnFloor(twin, floor.floor_id) : [];
  return (
    <div className="space-y-4" data-testid="remote-rooms">
      {floors.length > 1 && (
        <Segmented<string>
          className="w-full [&>button]:flex-1 [&>button]:justify-center"
          label="Floor"
          value={floorId ?? ''}
          options={floors.map((f) => ({ value: f.floor_id, label: f.label ?? `Floor ${f.index + 1}` }))}
          onChange={onFloor}
        />
      )}
      {floor && (
        <section data-floor-id={floor.floor_id}>
          <h2 className="mb-2 flex items-baseline justify-between px-0.5">
            <span className="font-display text-[15px] font-semibold">{floor.label ?? `Floor ${floor.index + 1}`}</span>
            <span className="text-xs text-faint">{rooms.length} room{rooms.length === 1 ? '' : 's'}</span>
          </h2>
          <div className="grid grid-cols-2 gap-2.5">
            {rooms.map((r) => {
              const on = r.room_id === roomId;
              return (
                <button key={r.room_id} type="button" data-room-id={r.room_id} aria-pressed={on}
                  onClick={() => { dispatch('viewer.goto', { room_id: r.room_id }); onPicked(); }}
                  className={cn('relative flex min-h-[92px] flex-col justify-between gap-3 rounded-2xl border p-3 text-left transition-transform active:scale-[0.97]',
                    on ? ON : 'border-line bg-raised text-text active:bg-ink')}>
                  <span className={cn('flex h-9 w-9 items-center justify-center rounded-xl', on ? 'bg-leaf/25' : 'bg-leaf/15 text-leaf-deep')}>
                    <RoomIcon label={r.label} width={20} height={20} />
                  </span>
                  {on && <span className="absolute top-3 right-3 rounded-full bg-leaf-deep px-2 py-0.5 text-[10px] font-semibold tracking-wide text-white uppercase">Here</span>}
                  <span className="min-w-0">
                    <span className="line-clamp-2 text-[15px] leading-tight font-semibold">{roomName(twin, r.room_id)}</span>
                    <span className="mt-0.5 block font-mono text-[11px] text-muted">{roomArea(r).toFixed(0)} m²</span>
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      )}
    </div>
  );
}

function Remote({ propertyId }: { propertyId: string }) {
  const twin = useTwin(propertyId || null);
  const me = useFamily((s) => s.me);
  const household = useFamily((s) => s.household);
  const status = useSession((s) => s.status);
  const desktopOn = useSession((s) => s.peers.desktop);
  const mode = useSession((s) => s.viewer.mode);
  const dispatch = useSession((s) => s.dispatch);
  const { error: sessionError } = useSessionInfo();
  const ptt = usePushToTalk();
  const heard = useSession((s) => s.interim?.text ?? '');
  // Hands-free answers: when the guide asks something, the microphone opens by itself.
  const [handsFree, setHandsFree] = useState(true);
  useEffect(() => {
    try {
      if (localStorage.getItem('ithaca.handsfree') === 'off') setHandsFree(false);
    } catch {}
  }, []);
  const chooseHandsFree = (on: boolean) => {
    setHandsFree(on);
    try {
      localStorage.setItem('ithaca.handsfree', on ? 'on' : 'off');
    } catch {}
  };
  const answering = useAnswerMic({ enabled: handsFree, memberId: me?.member_id ?? null, listening: ptt.listening, start: ptt.start });
  const [text, setText] = useState('');
  const [view, setView] = useState<RemoteView>('move');
  // Handedness: the walking stick sits under the dominant thumb, the talk button on the other side.
  const [hand, setHand] = useState<'left' | 'right'>('right');
  const [settings, setSettings] = useState(false);
  useEffect(() => {
    try {
      if (localStorage.getItem('ithaca.hand') === 'left') setHand('left');
    } catch {}
  }, []);
  const chooseHand = (h: 'left' | 'right') => {
    setHand(h);
    try {
      localStorage.setItem('ithaca.hand', h);
    } catch {}
  };
  const roomId = useSession((s) => s.viewer.room_id);
  const room = twin.data?.rooms.find((r) => r.room_id === roomId);
  const playback = useSession((s) => s.viewer.playback);

  // The big screen follows the phone: it shows the floor plan, of the floor picked here, while a room is being chosen.
  const [pickedFloor, setPickedFloor] = useState<string | null>(null);
  const floorId = twin.data ? shownFloor(twin.data, pickedFloor, roomId) : undefined;
  useEffect(() => {
    if (status === 'open') dispatch('remote.view', view === 'rooms' && floorId ? { view, floor_id: floorId } : { view });
  }, [view, floorId, status, dispatch]);

  // sweep -> room lookup comes from the twin, as on the desktop.
  useEffect(() => {
    if (twin.data) useSession.getState().init({ sweepRooms: sweepRooms(twin.data) });
  }, [twin.data]);

  // A phone held in the hand should not dim while it is the controller.
  useEffect(() => {
    let lock: { release(): Promise<void> } | null = null;
    const nav = navigator as Navigator & { wakeLock?: { request(type: 'screen'): Promise<{ release(): Promise<void> }> } };
    nav.wakeLock?.request('screen').then(
      (l) => (lock = l),
      () => {},
    );
    return () => void lock?.release();
  }, []);

  const send = () => {
    if (!text.trim()) return;
    dispatch('chat.user', { text: text.trim() });
    setText('');
  };

  return (
    <div className="relative mx-auto flex h-dvh max-w-md flex-col" data-testid="remote" data-status={status} data-mode={mode} data-view={view}>
      <header className="flex shrink-0 items-center justify-between gap-3 border-b border-line bg-ink/95 px-4 pt-[max(12px,env(safe-area-inset-top))] pb-3">
        {/* In a family, the phone says whose it is: "Maya · The Riveras". */}
        <div className="min-w-0 truncate font-serif text-lg font-semibold tracking-tight">{me ? `${me.name} · ${household?.name ?? 'Family'}` : 'Ithaca remote'}</div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge tone={status === 'open' ? (desktopOn ? 'leaf' : 'sun') : 'danger'}>{status === 'open' ? (desktopOn ? 'connected' : 'waiting for desktop') : status}</Badge>
          <button type="button" aria-label="Settings" aria-expanded={settings} data-testid="remote-settings" onClick={() => setSettings(!settings)}
            className={cn('flex h-9 w-9 items-center justify-center rounded-full border border-line', settings ? 'bg-main text-on-main' : 'bg-raised text-text')}>
            <svg viewBox="0 0 24 24" width={18} height={18} fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <circle cx="12" cy="12" r="3.2" />
              <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1.04 1.56V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.11-1.56 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.56-1.04H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.56-1.11 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.01a1.7 1.7 0 0 0 1.03-1.56V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1.04 1.56 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.01a1.7 1.7 0 0 0 1.56 1.03H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1.04z" />
            </svg>
          </button>
        </div>
      </header>
      {settings && (
        <div className="shrink-0 space-y-2 border-b border-line bg-raised px-4 py-3">
          <div className="text-sm font-semibold">Which hand do you use?</div>
          <Segmented<'left' | 'right'>
            className="w-full [&>button]:flex-1 [&>button]:justify-center"
            label="Handedness"
            value={hand}
            options={[{ value: 'left', label: 'Left-handed' }, { value: 'right', label: 'Right-handed' }]}
            onChange={chooseHand}
          />
          <p className="text-xs text-muted">The talk, play and sound buttons sit in the top corner on that side.</p>
          <div className="pt-2">
            <Toggle label="Open the mic when the guide asks a question" checked={handsFree} onChange={chooseHandsFree} />
            <p className="mt-1 text-xs text-muted">After your first tap on the mic, it opens by itself for your answer and closes when you stop talking.</p>
          </div>
          <div className="pt-2 text-sm font-semibold">The guide&apos;s voice</div>
          <Segmented<'female' | 'male'>
            className="w-full [&>button]:flex-1 [&>button]:justify-center"
            label="The guide's voice"
            value={playback.voice}
            options={[{ value: 'female', label: "Woman's voice" }, { value: 'male', label: "Man's voice" }]}
            onChange={(voice) => dispatch('viewer.playback', { voice })}
          />
          <p className="text-xs text-muted">The guide speaks on the big screen.</p>
        </div>
      )}

      {/* Where you are on one side; talk, play the day and sound in the top corner on the dominant hand's side. */}
      <div className={cn('flex shrink-0 items-center gap-3 border-b border-line bg-ink/95 px-3 py-1', hand === 'left' && 'flex-row-reverse')} data-testid="remote-bar">
        <div className={cn('min-w-0 flex-1 truncate', hand === 'left' && 'text-right')} data-testid="remote-room">
          {ptt.listening || ptt.error || answering ? (
            <span className={cn('block text-sm', ptt.error ? 'text-danger' : 'text-text')} data-testid="mic-status">
              <span className="line-clamp-2">{ptt.error ?? (heard ? `“${heard}”` : !ptt.listening ? 'The guide is asking. The mic opens when it finishes.' : ptt.level === null ? 'Opening the microphone…' : 'Listening… talk, then pause')}</span>
              {/* Proof the microphone hears you: the bar moves with your voice. */}
              {!ptt.error && ptt.level !== null && (
                <span className="mt-1 block h-1 w-24 overflow-hidden rounded-full bg-line">
                  <span className="block h-full rounded-full bg-leaf transition-[width] duration-100" style={{ width: `${Math.max(4, ptt.level * 100)}%` }} />
                </span>
              )}
            </span>
          ) : twin.data && room ? (
            <>
              <span className="font-display text-[15px] font-semibold">{roomName(twin.data, room.room_id)}</span>
              <span className="font-mono text-xs text-muted"> · {roomArea(room).toFixed(0)} m²</span>
            </>
          ) : (
            <span className="text-sm text-muted">{twin.data ? 'Not in a room yet' : ''}</span>
          )}
        </div>
        {/* The mic is outermost, in the corner; then play, then sound. */}
        <div className={cn('flex shrink-0 items-center gap-2', hand === 'right' && 'flex-row-reverse')}>
          <button
            type="button"
            disabled={!ptt.supported}
            aria-label={ptt.listening ? 'Stop listening' : 'Talk to the guide'}
            aria-pressed={ptt.listening}
            title={ptt.supported ? 'Tap and talk. It stops when you do.' : 'Speech recognition is not available in this browser'}
            data-testid="ptt"
            className={cn('flex h-9 w-9 items-center justify-center rounded-full transition-transform select-none disabled:opacity-40',
              ptt.listening ? 'mic-live scale-110 bg-leaf text-main' : 'bg-main text-on-main')}
            onClick={ptt.toggle}
          >
            <IconMic width={18} height={18} />
          </button>
          <button type="button" disabled={!desktopOn} aria-label={playback.playing ? 'Pause the day' : 'Play the day'} aria-pressed={playback.playing} data-testid="remote-play"
            title={desktopOn ? undefined : 'Open the house on the big screen first'} onClick={togglePlaying}
            className={cn('flex h-9 w-9 items-center justify-center rounded-full border disabled:opacity-40', playback.playing ? ON : OFF)}>
            {playback.playing ? <IconPause width={15} height={15} /> : <IconPlay width={15} height={15} />}
          </button>
          <button type="button" disabled={!desktopOn} aria-label={playback.listening ? 'Turn the sound off' : 'Turn the sound on'} aria-pressed={playback.listening} data-testid="remote-sound"
            title={desktopOn ? undefined : 'Open the house on the big screen first'} onClick={toggleListening}
            className={cn('flex h-9 w-9 items-center justify-center rounded-full border disabled:opacity-40', playback.listening ? ON : OFF)}>
            {playback.listening ? <IconSound width={18} height={18} /> : <IconMute width={18} height={18} />}
          </button>
        </div>
      </div>

      {view === 'move' && (
        <div className="min-h-0 flex-1">
          <MovePad />
        </div>
      )}
      {view === 'furniture' && twin.data && <FurnitureRoom twin={twin.data} />}
      {view === 'guide' && (
        <div className="flex min-h-0 flex-1 flex-col" data-testid="remote-guide">
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
            <Messages memberId={me?.member_id ?? null} />
          </div>
          <form className="flex shrink-0 items-center gap-2 border-t border-line bg-raised px-3 py-2" onSubmit={(e) => { e.preventDefault(); send(); }}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Message" aria-label="Message the guide" enterKeyHint="send"
              className="h-11 min-w-0 flex-1 rounded-full border border-line bg-surface px-4 text-base placeholder:text-faint focus:border-line-strong focus:outline-none" />
            {text.trim() ? (
              <button type="submit" aria-label="Send" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-main text-on-main">
                <IconSend width={18} height={18} />
              </button>
            ) : (
              <button type="button" disabled={!ptt.supported} aria-label={ptt.listening ? 'Stop listening' : 'Talk to the guide'} aria-pressed={ptt.listening} data-testid="ptt-inline"
                className={cn('flex h-11 w-11 shrink-0 items-center justify-center rounded-full select-none disabled:opacity-40', ptt.listening ? 'mic-live scale-110 bg-leaf text-main' : 'bg-main text-on-main')}
                onClick={ptt.toggle}>
                <IconMic width={20} height={20} />
              </button>
            )}
          </form>
        </div>
      )}
      <main className={cn('min-h-0 flex-1 space-y-3 overflow-y-auto px-4 pt-4 pb-6', (view === 'move' || view === 'guide' || (view === 'furniture' && twin.data)) && 'hidden')}>
        {sessionError && (
          <Notice tone="error" title="Could not join the session">
            {sessionError}. Pairing codes are single-use and expire after 10 minutes: ask for a new code on the desktop.
          </Notice>
        )}
        {twin.error && (
          <Notice tone="error" title="Could not load the home">
            {twin.error.message}
          </Notice>
        )}
        {!twin.data && !twin.error && <Spinner label="Loading the home…" />}
        {twin.data && view === 'rooms' && (
          <RoomBlocks twin={twin.data} floorId={floorId} onFloor={setPickedFloor} onPicked={() => { setPickedFloor(null); setView('move'); }} />
        )}
        {twin.data && view === 'light' && (
          <>
            <LightPanel twin={twin.data} />
            <SoundPicker twin={twin.data} />
          </>
        )}
      </main>

      <footer className="shrink-0 border-t border-line bg-ink/95">
        <nav aria-label="Remote screens" className="grid grid-cols-5 pb-[max(2px,env(safe-area-inset-bottom))]">
          {VIEWS.map(({ view: v, label, icon: Icon }) => (
            <button key={v} type="button" data-tab={v} aria-pressed={v === view} aria-label={label} title={label} onClick={() => setView(v)}
              className={cn('flex items-center justify-center py-2', v === view ? 'text-sun-deep' : 'text-muted')}>
              <Icon width={22} height={22} strokeWidth={v === view ? 2.2 : 1.8} />
            </button>
          ))}
        </nav>
      </footer>
    </div>
  );
}

interface Props {
  sessionId: string;
  token: string;
  propertyId: string;
  /** A family member's phone: `token` is already its client token, no pairing step. */
  member?: boolean;
}

/**
 * Phone controller (A2): a pure remote. It holds no state the desktop does not also hold, and
 * rebuilds its view from `session.state` whenever it (re)connects.
 */
export function RemoteController({ sessionId, token, propertyId, member = false }: Props) {
  const join = useMemo(() => ({ sessionId, token, prejoined: member }), [sessionId, token, member]);
  if (!token) {
    return (
      <main className="mx-auto flex min-h-dvh max-w-md items-center p-6">
        <Notice tone="warn" title="This link has no pairing code">
          Open the home on a computer, press “Pair phone”, and scan the QR code shown there.
        </Notice>
      </main>
    );
  }
  return (
    <SessionProvider client="phone" propertyId={propertyId} join={join}>
      <Remote propertyId={propertyId} />
    </SessionProvider>
  );
}
