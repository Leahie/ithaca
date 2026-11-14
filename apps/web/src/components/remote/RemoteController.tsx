'use client';

import type { NoiseScenario, PropertyTwin, ViewerMode } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { PlanPreview } from '@/components/design/PlanPreview';
import { JobRows, useObjectUpload } from '@/components/me/InventorySection';
import { SessionProvider, useSessionInfo } from '@/components/session/SessionProvider';
import { TimeScrubber } from '@/components/sunlab/TimeScrubber';
import { Badge, Button, Notice, Segmented, Spinner, Toggle, cn } from '@/components/ui';
import { IconCamera, IconCheck, IconDesign, IconMap, IconMic, IconSend, IconSound, IconSun, IconTour } from '@/components/ui/icons';
import { useInventory, useLayouts, useTwin } from '@/lib/data/hooks';
import { visibleLayouts } from '@/lib/layouts';
import { roomName, roomsOnFloor } from '@/lib/rooms';
import { usePushToTalk } from '@/lib/speech/usePushToTalk';
import { useFamily, useMemberName } from '@/lib/store/family';
import { useSession } from '@/lib/store/session';
import { LookPad } from './LookPad';

const MODES: { mode: ViewerMode; label: string; icon: typeof IconTour }[] = [
  { mode: 'tour', label: 'Tour', icon: IconTour },
  { mode: 'sun', label: 'Sun', icon: IconSun },
  { mode: 'sound', label: 'Sound', icon: IconSound },
  { mode: 'neighborhood', label: 'Area', icon: IconMap },
  { mode: 'design', label: 'Design', icon: IconDesign },
];

const AS_BUILT: NoiseScenario = { glazing: null, add_carpet: false, include_furniture: true };

function Card({ title, children, testId }: { title: string; children: ReactNode; testId?: string }) {
  return (
    <section className="rounded-2xl border border-line bg-surface p-4" data-testid={testId}>
      <h2 className="mb-3 text-[11px] font-semibold tracking-wider text-faint uppercase">{title}</h2>
      {children}
    </section>
  );
}

function Rooms({ twin }: { twin: PropertyTwin }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  return (
    <div className="space-y-3">
      {[...twin.floors]
        .sort((a, b) => b.index - a.index)
        .map((floor) => (
          <div key={floor.floor_id}>
            <div className="mb-1.5 text-xs text-muted">Floor {floor.index + 1}</div>
            <div className="flex flex-wrap gap-1.5">
              {roomsOnFloor(twin, floor.floor_id).map((r) => (
                  <button
                    key={r.room_id}
                    type="button"
                    data-room-id={r.room_id}
                    aria-pressed={r.room_id === roomId}
                    onClick={() => dispatch('viewer.goto', { room_id: r.room_id })}
                    className={cn('min-h-10 rounded-lg border px-3 text-sm', r.room_id === roomId ? 'border-sun bg-sun/15 text-sun-deep' : 'border-line text-muted active:bg-raised')}
                  >
                    {roomName(twin, r.room_id)}
                  </button>
              ))}
            </div>
          </div>
        ))}
    </div>
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
 * The latest thing the agent said or did, so the phone user sees their words landed. In a family,
 * a phone shows its own member's conversation and the family board; the big screen shows all.
 */
function AgentFeed({ memberId }: { memberId: string | null }) {
  const chat = useSession((s) => s.chat);
  const interim = useSession((s) => s.interim);
  const nameOf = useMemberName();
  const mine = memberId
    ? chat.filter((c) => (c.kind === 'ask' ? c.from_member_id === memberId || c.to_member_id === memberId : c.kind === 'note' || c.member_id === memberId))
    : chat;
  const recent = mine.slice(-4);
  if (!recent.length && !interim) return <p className="text-sm leading-5 text-muted">Hold the button below and speak. For example: “Take me to the kitchen.”</p>;
  return (
    <div className="space-y-2" aria-live="polite" data-testid="remote-feed">
      {recent.map((c) =>
        c.kind === 'user' ? (
          <p key={c.id} className="text-right text-sm text-muted">
            “{c.text}”
          </p>
        ) : c.kind === 'agent' ? (
          <p key={c.id} className={cn('text-sm leading-5', c.streaming && 'streaming-caret')}>
            {c.text}
          </p>
        ) : c.kind === 'action' ? (
          <div key={c.id}>
            <Badge tone="sun">
              <IconCheck width={11} height={11} />
              {c.summary}
            </Badge>
          </div>
        ) : c.kind === 'note' ? (
          <p key={c.id} className="rounded-[var(--radius-frame)] border border-line bg-raised px-3 py-2 text-sm leading-5">
            <span className="font-semibold">{nameOf(c.member_id) ? `${nameOf(c.member_id)}'s agent` : 'The guide'}</span> on the family board: {c.text}
          </p>
        ) : c.kind === 'ask' ? (
          <p key={c.id} className="rounded-[var(--radius-frame)] border border-line bg-raised px-3 py-2 text-sm leading-5">
            <span className="font-semibold">{nameOf(c.to_member_id) ?? 'Another member'}'s agent</span>: {c.answer ?? 'thinking…'}
          </p>
        ) : c.kind === 'catalog' ? (
          // Picture cards are for the big screen; the phone just says they are there.
          <p key={c.id} className="text-sm text-muted">
            Showing {c.pieces.length} piece{c.pieces.length === 1 ? '' : 's'} on the big screen: {c.pieces.map((x) => x.label).join(', ')}.
          </p>
        ) : (
          <p key={c.id} className="text-sm text-danger">
            {c.message}
          </p>
        ),
      )}
      {interim && <p className="text-right text-sm text-faint italic">{interim.text || 'Listening…'}</p>}
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
    <div className="mx-auto flex min-h-dvh max-w-md flex-col" data-testid="remote" data-status={status} data-mode={mode}>
      <header className="sticky top-0 z-20 border-b border-line bg-ink/95 px-4 pt-[max(12px,env(safe-area-inset-top))] pb-3 backdrop-blur">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2 font-display text-base font-semibold">
            <span className="h-3 w-3 shrink-0 rounded-full bg-sun shadow-[0_0_0_1.5px_var(--color-main)]" />
            {/* In a family, the phone says whose it is: "Maya · The Riveras". */}
            <span className="truncate">{me ? `${me.name} · ${household?.name ?? 'Family'}` : 'Ithaca remote'}</span>
          </div>
          <Badge tone={status === 'open' ? (desktopOn ? 'leaf' : 'sun') : 'danger'}>{status === 'open' ? (desktopOn ? 'connected' : 'waiting for desktop') : status}</Badge>
        </div>
        <nav aria-label="Views" className="mt-3 grid grid-cols-5 gap-1 rounded-xl border border-line bg-surface p-1">
          {MODES.map(({ mode: m, label, icon: Icon }) => (
            <button
              key={m}
              type="button"
              data-tab={m}
              aria-pressed={m === mode}
              onClick={() => dispatch('viewer.setMode', { mode: m })}
              className={cn('flex flex-col items-center gap-1 rounded-lg py-2 text-[11px]', m === mode ? 'bg-raised text-sun-deep' : 'text-muted')}
            >
              <Icon width={18} height={18} />
              {label}
            </button>
          ))}
        </nav>
      </header>

      <main className="flex-1 space-y-3 px-4 py-4 pb-40">
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
        {twin.data && (
          <>
            {(mode === 'tour' || mode === 'sun') && (
              <Card title="Look around">
                <LookPad />
              </Card>
            )}
            {mode !== 'neighborhood' && (
              <Card title="Time of day" testId="remote-time">
                <TimeScrubber twin={twin.data} compact />
              </Card>
            )}
            {mode === 'sound' && (
              <Card title="Sound">
                <SoundControls twin={twin.data} />
              </Card>
            )}
            {(mode === 'design' || mode === 'tour' || mode === 'sun') && (
              <Card title="Layouts" testId="remote-layouts">
                <Variants twin={twin.data} />
              </Card>
            )}
            {mode !== 'neighborhood' && (
              <Card title="Rooms" testId="remote-rooms">
                <Rooms twin={twin.data} />
              </Card>
            )}
            <Card title="Add furniture">
              <Capture />
            </Card>
          </>
        )}
      </main>

      <footer className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-ink/95 backdrop-blur">
        <div className="mx-auto max-w-md space-y-3 px-4 pt-3 pb-[max(12px,env(safe-area-inset-bottom))]">
          <AgentFeed memberId={me?.member_id ?? null} />
          {ptt.error && <p className="text-xs text-danger">{ptt.error}</p>}
          <div className="flex items-center gap-2">
            <form
              className="flex min-w-0 flex-1 gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                send();
              }}
            >
              <input
                value={text}
                onChange={(e) => setText(e.target.value)}
                placeholder="Type to the agent"
                aria-label="Message the agent"
                className="h-12 min-w-0 flex-1 rounded-xl border border-line bg-surface px-3 text-base placeholder:text-faint focus:border-line-strong focus:outline-none"
              />
              {text.trim() && (
                <Button type="submit" variant="subtle" size="lg" square aria-label="Send">
                  <IconSend width={18} height={18} />
                </Button>
              )}
            </form>
            <Button
              variant={ptt.listening ? 'primary' : 'subtle'}
              size="lg"
              disabled={!ptt.supported}
              aria-label="Hold to talk to the agent"
              title={ptt.supported ? 'Hold to talk' : 'Speech recognition is not available in this browser'}
              data-testid="ptt"
              className={cn('touch-none select-none', ptt.listening && 'scale-105')}
              onPointerDown={(e) => {
                e.currentTarget.setPointerCapture(e.pointerId);
                ptt.start();
              }}
              onPointerUp={ptt.stop}
              onPointerCancel={ptt.stop}
              onContextMenu={(e) => e.preventDefault()}
            >
              <IconMic width={20} height={20} /> {ptt.listening ? 'Listening' : 'Hold to talk'}
            </Button>
          </div>
        </div>
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
