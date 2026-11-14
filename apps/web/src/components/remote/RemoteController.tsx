'use client';

import type { NoiseScenario, PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { PlanPreview } from '@/components/design/PlanPreview';
import { JobRows, useObjectUpload } from '@/components/me/InventorySection';
import { SessionProvider, useSessionInfo } from '@/components/session/SessionProvider';
import { Badge, Button, Notice, Segmented, Spinner, Toggle, cn } from '@/components/ui';
import { IconCamera, IconCheck, IconDesign, IconMap, IconMic, IconSend, IconSound, IconSun, IconTour } from '@/components/ui/icons';
import { useInventory, useLayouts, useTwin } from '@/lib/data/hooks';
import { visibleLayouts } from '@/lib/layouts';
import { roomArea, roomName, roomsOnFloor } from '@/lib/rooms';
import { usePushToTalk } from '@/lib/speech/usePushToTalk';
import { useFamily, useMemberName } from '@/lib/store/family';
import { useSession } from '@/lib/store/session';
import { RoomIcon } from '@/components/ui/RoomIcon';
import { ArrangePanel } from '@/components/house/ArrangePanel';
import { LightPanel } from './LightPanel';
import { MovePad } from './MovePad';

const AS_BUILT: NoiseScenario = { glazing: null, add_carpet: false, include_furniture: true };

function Card({ title, children, testId }: { title: string; children: ReactNode; testId?: string }) {
  return (
    <section className="rounded-2xl border border-line bg-surface p-4" data-testid={testId}>
      <h2 className="mb-3 text-[11px] font-semibold tracking-wider text-faint uppercase">{title}</h2>
      {children}
    </section>
  );
}

/** Swipe through layout variants; a tap places one. */
function Variants({ twin }: { twin: PropertyTwin }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const shown = useSession((s) => s.viewer.shown_layout_ids);
  const selectedId = useSession((s) => s.viewer.selected_layout_id);
  const dispatch = useSession((s) => s.dispatch);
  const layouts = useLayouts(twin.property_id);
  const inventory = useInventory();
  const items = useMemo(() => new Map((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const { variants } = useMemo(() => visibleLayouts(layouts.data ?? [], shown, roomId), [layouts.data, shown, roomId]);

  if (!layouts.data) return layouts.error ? <p className="text-sm text-danger">{layouts.error.message}</p> : <Spinner label="Loading layouts…" />;
  if (!variants.length) return <p className="text-sm leading-5 text-muted">No layouts yet. Ask the agent to lay out a room.</p>;

  return (
    <div className="-mx-4 flex snap-x snap-mandatory gap-3 overflow-x-auto px-4 pb-1" data-testid="variant-swiper">
      {variants.map((l, i) => {
        const room = twin.rooms.find((r) => r.room_id === l.room_id);
        const selected = l.layout_id === selectedId;
        return (
          <article key={l.layout_id} data-layout-id={l.layout_id} data-selected={selected} className={cn('w-[82%] shrink-0 snap-center overflow-hidden rounded-xl border', selected ? 'border-sun' : 'border-line')}>
            <div className="aspect-[4/3] bg-ink">
              {l.thumbnail_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={l.thumbnail_url} alt={`Layout ${i + 1}`} className="h-full w-full object-cover" />
              ) : (
                room && <PlanPreview room={room} openings={twin.openings.filter((o) => o.room_id === room.room_id)} placements={l.placements} items={items} labels={false} className="h-full w-full p-3" />
              )}
            </div>
            <div className="space-y-2 p-3">
              <div className="flex items-baseline justify-between text-sm">
                <span className="font-medium">
                  {room ? roomName(twin, room.room_id) : 'Room'} · {i + 1}/{variants.length}
                </span>
                <span className="text-xs text-muted">fit {(l.score * 100).toFixed(0)}%</span>
              </div>
              <p className="line-clamp-2 text-xs leading-4 text-muted">{l.rationale}</p>
              <Button variant={selected ? 'subtle' : 'primary'} className="w-full" disabled={selected} onClick={() => dispatch('layout.select', { layout_id: l.layout_id })}>
                {selected ? (
                  <>
                    <IconCheck /> Placed
                  </>
                ) : (
                  'Place in the house'
                )}
              </Button>
            </div>
          </article>
        );
      })}
    </div>
  );
}

function Capture() {
  const input = useRef<HTMLInputElement>(null);
  const { upload, busy, error, rows, dismiss } = useObjectUpload();
  return (
    <div className="space-y-3">
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        data-testid="capture-input"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void upload(f);
          e.target.value = '';
        }}
      />
      <Button variant="subtle" size="lg" className="w-full" disabled={busy} onClick={() => input.current?.click()}>
        <IconCamera width={18} height={18} /> {busy ? 'Uploading…' : 'Photograph a piece of furniture'}
      </Button>
      {error && <p className="text-xs text-danger">{error}</p>}
      <JobRows rows={rows} dismiss={dismiss} />
    </div>
  );
}

function SoundControls({ twin }: { twin: PropertyTwin }) {
  const viewerRoom = useSession((s) => s.viewer.room_id);
  const sound = useSession((s) => s.viewer.sound_scenario);
  const dispatch = useSession((s) => s.dispatch);
  const roomId = sound?.room_id ?? viewerRoom ?? twin.rooms.find((r) => /bedroom/i.test(r.label))?.room_id ?? twin.rooms[0]?.room_id;
  const scenario = sound?.scenario ?? AS_BUILT;
  const room = twin.rooms.find((r) => r.room_id === roomId);
  if (!roomId) return <p className="text-sm text-muted">No rooms.</p>;
  const set = (patch: Partial<NoiseScenario>) => dispatch('sound.setScenario', { room_id: roomId, scenario: { ...scenario, ...patch } });
  type G = 'asbuilt' | 'single' | 'double' | 'triple';
  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">Scenario for {roomName(twin, room?.room_id)}. The sound plays on the desktop.</p>
      <Segmented<G>
        className="w-full [&>button]:flex-1 [&>button]:justify-center"
        label="Window glazing"
        value={scenario.glazing ?? 'asbuilt'}
        options={[
          { value: 'asbuilt', label: 'As built' },
          { value: 'single', label: 'Single' },
          { value: 'double', label: 'Double' },
          { value: 'triple', label: 'Triple' },
        ]}
        onChange={(g) => set({ glazing: g === 'asbuilt' ? null : g })}
      />
      <Toggle label="Add carpet" checked={!!scenario.add_carpet} onChange={(add_carpet) => set({ add_carpet })} />
    </div>
  );
}

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
          <p key={c.id} className={aside}>On the big screen: {c.pieces.map((x) => x.label).join(', ')}</p>
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
  { view: 'move', label: 'Move', icon: IconTour },
  { view: 'rooms', label: 'Rooms', icon: IconMap },
  { view: 'guide', label: 'Guide', icon: IconMic },
  { view: 'furniture', label: 'Furniture', icon: IconDesign },
  { view: 'light', label: 'Light', icon: IconSun },
];

/** Big blocks to tap, floor by floor. The map itself is on the big screen while this is open. */
function RoomBlocks({ twin, onPicked }: { twin: PropertyTwin; onPicked(): void }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  // The floor you are on comes first, then the rest from the ground up.
  const here = twin.rooms.find((r) => r.room_id === roomId)?.floor_id;
  const floors = [...twin.floors].sort((a, b) => Number(b.floor_id === here) - Number(a.floor_id === here) || a.index - b.index);
  return (
    <div className="space-y-5" data-testid="remote-rooms">
      <p className="text-sm text-muted">The map is on the big screen. Tap where you want to go.</p>
      {floors.map((floor) => (
        <section key={floor.floor_id}>
          <h2 className="mb-2 text-[11px] font-semibold tracking-wider text-faint uppercase">{floor.label ?? `Floor ${floor.index + 1}`}{floor.floor_id === here && ' · you are here'}</h2>
          <div className="grid grid-cols-2 gap-2">
            {roomsOnFloor(twin, floor.floor_id).map((r) => (
              <button key={r.room_id} type="button" data-room-id={r.room_id} aria-pressed={r.room_id === roomId}
                onClick={() => { dispatch('viewer.goto', { room_id: r.room_id }); onPicked(); }}
                className={cn('flex min-h-16 items-center gap-3 rounded-[var(--radius-frame)] border px-3 py-2.5 text-left',
                  r.room_id === roomId ? 'border-main bg-main text-on-main' : 'border-line bg-raised text-text active:bg-ink')}>
                <RoomIcon label={r.label} width={26} height={26} className={cn('shrink-0', r.room_id === roomId ? 'text-leaf' : 'text-leaf-deep')} />
                <span className="flex min-w-0 flex-col">
                  <span className="text-[15px] leading-5 font-semibold">{roomName(twin, r.room_id)}</span>
                  <span className={cn('font-mono text-xs', r.room_id === roomId ? 'text-on-main/70' : 'text-muted')}>{roomArea(r).toFixed(0)} m²</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      ))}
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

  // The big screen follows the phone: it shows the floor plan while a room is being chosen.
  useEffect(() => {
    if (status === 'open') dispatch('remote.view', { view });
  }, [view, status, dispatch]);

  // sweep -> room lookup comes from the twin, as on the desktop.
  useEffect(() => {
    if (twin.data) useSession.getState().init({ sweepRooms: Object.fromEntries(twin.data.sweeps.map((s) => [s.sweep_id, s.room_id ?? null])) });
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
        <div className="flex min-w-0 items-center gap-2 font-display text-base font-semibold">
          <span className="h-3 w-3 shrink-0 rounded-full bg-sun shadow-[0_0_0_1.5px_var(--color-main)]" />
          {/* In a family, the phone says whose it is: "Maya · The Riveras". */}
          <span className="truncate">{me ? `${me.name} · ${household?.name ?? 'Family'}` : 'Ithaca remote'}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge tone={status === 'open' ? (desktopOn ? 'leaf' : 'sun') : 'danger'}>{status === 'open' ? (desktopOn ? 'connected' : 'waiting for desktop') : status}</Badge>
          <button type="button" aria-label="Settings" aria-expanded={settings} data-testid="remote-settings" onClick={() => setSettings(!settings)}
            className="flex h-9 items-center rounded-full border border-line bg-raised px-3 text-sm">
            Settings
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
          <p className="text-xs text-muted">The walking stick sits under that thumb; the talk button moves to the other corner.</p>
        </div>
      )}

      {view === 'move' && (
        <div className="min-h-0 flex-1">
          <MovePad side={hand} hint={twin.data ? `You are in ${roomName(twin.data, roomId)}` : ''} />
        </div>
      )}
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
              <button type="button" disabled={!ptt.supported} aria-label="Hold to talk to the guide" data-testid="ptt-inline"
                className={cn('flex h-11 w-11 shrink-0 touch-none items-center justify-center rounded-full select-none disabled:opacity-40', ptt.listening ? 'scale-110 bg-leaf text-main' : 'bg-main text-on-main')}
                onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); ptt.start(); }}
                onPointerUp={ptt.stop} onPointerCancel={ptt.stop} onContextMenu={(e) => e.preventDefault()}>
                <IconMic width={20} height={20} />
              </button>
            )}
          </form>
        </div>
      )}
      <main className={cn('min-h-0 flex-1 space-y-3 overflow-y-auto px-4 pt-4 pb-24', (view === 'move' || view === 'guide') && 'hidden')}>
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
        {twin.data && view === 'rooms' && <RoomBlocks twin={twin.data} onPicked={() => setView('move')} />}
        {twin.data && view === 'furniture' && (
          <>
            {/* The room you are standing in, from above: drag, turn, add and remove pieces. */}
            <ArrangePanel twin={twin.data} inline />
            <Card title="Layout options from the guide" testId="remote-layouts">
              <Variants twin={twin.data} />
            </Card>
            <Card title="Add a piece from a photo">
              <Capture />
            </Card>
          </>
        )}
        {twin.data && view === 'light' && (
          <>
            <LightPanel twin={twin.data} />
            <Card title="Sound">
              <SoundControls twin={twin.data} />
            </Card>
          </>
        )}
      </main>

      {/* Talk floats over every screen, in the corner away from the walking stick. */}
      <div className={cn('pointer-events-none absolute bottom-[calc(68px+env(safe-area-inset-bottom))] z-30 flex items-end gap-2', hand === 'right' ? 'left-4' : 'right-4 flex-row-reverse', view === 'guide' && 'hidden')}>
        <button
          type="button"
          disabled={!ptt.supported}
          aria-label="Hold to talk to the guide"
          title={ptt.supported ? 'Hold to talk' : 'Speech recognition is not available in this browser'}
          data-testid="ptt"
          className={cn('pointer-events-auto flex h-14 w-14 touch-none items-center justify-center rounded-full shadow-float transition-transform select-none disabled:opacity-40',
            ptt.listening ? 'scale-110 bg-leaf text-main' : 'bg-main text-on-main')}
          onPointerDown={(e) => {
            e.currentTarget.setPointerCapture(e.pointerId);
            ptt.start();
          }}
          onPointerUp={ptt.stop}
          onPointerCancel={ptt.stop}
          onContextMenu={(e) => e.preventDefault()}
        >
          <IconMic width={24} height={24} />
        </button>
        {(ptt.listening || ptt.error) && (
          <span className={cn('mb-3 rounded-full bg-raised px-3 py-1 text-xs shadow-float', ptt.error ? 'text-danger' : 'text-text')}>{ptt.error ?? 'Listening…'}</span>
        )}
      </div>
      <footer className="shrink-0 border-t border-line bg-ink/95">
        <nav aria-label="Remote screens" className="grid grid-cols-5 pb-[max(8px,env(safe-area-inset-bottom))]">
          {VIEWS.map(({ view: v, label, icon: Icon }) => (
            <button key={v} type="button" data-tab={v} aria-pressed={v === view} onClick={() => setView(v)}
              className={cn('flex flex-col items-center gap-1 pt-2.5 pb-1 text-[11px]', v === view ? 'font-semibold text-sun-deep' : 'text-muted')}>
              <Icon width={20} height={20} />
              {label}
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
