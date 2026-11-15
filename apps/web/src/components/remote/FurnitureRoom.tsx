'use client';

import type { InventoryItem, LayoutVariant, PhotoPoint, PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { CARD, CARD_NAME, CARD_SUB, CardImage, CatalogBrowser, SearchBox, matches } from '@/components/design/CatalogBrowser';
import { OwnRoom } from '@/components/design/OwnRoom';
import { PhotoButtons, TapToPick, preparePhoto } from '@/components/design/PhotoPiece';
import { PlanEditor, SIZE_STEP, type PlanControls } from '@/components/design/PlanEditor';
import { furnitureChanged } from '@/components/house/ArrangePanel';
import { JobRows, useObjectUpload } from '@/components/me/InventorySection';
import { MODAL_BACKDROP, MODAL_SCROLL, Spinner, cn, useScrollLock } from '@/components/ui';
import { IconArrowUp, IconBackpack, IconBag, IconCheck, IconClose, IconCube, IconEye, IconGear, IconList, IconMic, IconPeople, IconPlus, IconSend, IconSparkle, IconTrash, IconTurnLeft, IconTurnRight } from '@/components/ui/icons';
import { api } from '@/lib/api';
import { apiUserId } from '@/lib/api/user';
import { config } from '@/lib/config';
import { useHouseFurniture, useInventory, useLayouts } from '@/lib/data/hooks';
import { compassToInterior, interiorDirectionToAzimuth } from '@/lib/geometry/frames';
import { bounds, frontDirection } from '@/lib/geometry/polygon';
import { previewLayout, visibleLayouts } from '@/lib/layouts';
import { roomName } from '@/lib/rooms';
import { compassWord } from '@/lib/sun/position';
import { floorSweeps } from '@/lib/showcase/rooms';
import { useFamily } from '@/lib/store/family';
import { useSession } from '@/lib/store/session';
import { OFF, ON } from './LightPanel';
import { LookStick } from './LookStick';

const NUDGE_M = 0.1;
const LETTERS = 'ABCDEFGH';
const ISLAND = 'rounded-2xl border border-line bg-surface shadow-lg';

/** The server's refusal in words: `{"violations": ["…"]}` or `{"detail": "…"}` comes back as JSON text. */
const reason = (err: unknown) => {
  const text = err instanceof Error ? err.message : String(err);
  return /"violations":\s*\["([^"]+)"/.exec(text)?.[1] ?? /"detail":\s*"([^"]+)"/.exec(text)?.[1] ?? text;
};

/** Facing a spot on the plan, the big screen stands at least this far from it (on top of it, it would look at its feet). */
const VIEW_MIN_M = 1.0;

function RoundButton({ label, onClick, children, on = false, disabled = false, className }: { label: string; onClick(): void; children: ReactNode; on?: boolean; disabled?: boolean; className?: string }) {
  return (
    <button type="button" aria-label={label} title={label} disabled={disabled} onClick={onClick}
      className={cn('flex h-10 w-10 shrink-0 items-center justify-center rounded-full border transition-transform active:scale-95 disabled:opacity-40', on ? ON : OFF, className)}>
      {children}
    </button>
  );
}

/** North on the plan: the needle turns with the house, the letters stay upright. */
function Compass({ angle }: { angle: number }) {
  const at = (deg: number, r: number) => [Math.sin(((angle + deg) * Math.PI) / 180) * r, -Math.cos(((angle + deg) * Math.PI) / 180) * r] as const;
  return (
    <div className="pointer-events-none flex h-14 w-14 items-center justify-center rounded-full border border-line bg-surface/95 shadow-sm" data-testid="compass" aria-label={`North is ${Math.round(angle)} degrees clockwise from the top of the plan`}>
      <svg viewBox="-28 -28 56 56" width={56} height={56} aria-hidden>
        <g transform={`rotate(${angle})`}>
          <path d="M0 -15 L4 0 L0 -2 L-4 0 Z" fill="#c2412d" />
          <path d="M0 15 L4 0 L0 2 L-4 0 Z" fill="#9aa7a1" />
        </g>
        {(['N', 'E', 'S', 'W'] as const).map((l, i) => {
          const [x, y] = at(i * 90, 21);
          return (
            <text key={l} x={x} y={y} textAnchor="middle" dominantBaseline="central" fontSize={l === 'N' ? 10 : 8} fontWeight={700} fill={l === 'N' ? '#c2412d' : '#55625c'}>
              {l}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

function Sheet({ title, onClose, children, action }: { title: string; onClose(): void; children: ReactNode; action?: ReactNode }) {
  return (
    <section className={cn(ISLAND, 'overflow-hidden')} aria-label={title}>
      <header className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-2">
        <h2 className="text-[11px] font-semibold tracking-wider text-faint uppercase">{title}</h2>
        <div className="ml-auto flex items-center gap-2">
          {action}
          <RoundButton label="Close" onClick={onClose} className="h-8 w-8">
            <IconClose width={16} height={16} />
          </RoundButton>
        </div>
      </header>
      {/* The max-height is on the scroller itself: mobile Safari won't shrink a flex child to fit one. */}
      <div className="max-h-[50dvh] touch-pan-y overflow-y-auto overscroll-contain px-4 py-3">{children}</div>
    </section>
  );
}

type SheetKind = 'add' | 'room' | 'suggest';

/** The phone's microphone, shared with the talk button in the bar. */
export type Talk = { supported: boolean; listening: boolean; toggle(): void; stop(): void };

/**
 * The phone's furniture screen: the room you are in, from above, filling the screen, with north on
 * a compass and each wall lettered by its side. An island at the bottom adds pieces, lists what is
 * in the room and asks the guide for layouts (by voice: Suggest opens the microphone, and the
 * guide knows the phone is on this screen); tapping a piece swaps it for tools to turn, move or
 * remove that piece. While the guide's options are up, the island picks between them, keeps one,
 * or tells the guide what you like about one so it can suggest more.
 */
export function FurnitureRoom({ twin, talk }: { twin: PropertyTwin; talk?: Talk }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const shown = useSession((s) => s.viewer.shown_layout_ids);
  const selectedLayoutId = useSession((s) => s.viewer.selected_layout_id);
  const chat = useSession((s) => s.chat);
  const dispatch = useSession((s) => s.dispatch);
  const memberId = useFamily((s) => s.me?.member_id ?? null);
  const house = useHouseFurniture(twin.property_id);
  const inventory = useInventory();
  const layouts = useLayouts(twin.property_id);

  const room = twin.rooms.find((r) => r.room_id === roomId);
  const pieces = useMemo(() => (house.data ?? []).filter((p) => p.room_id === room?.room_id), [house.data, room?.room_id]);
  const items = useMemo(() => new Map<string, InventoryItem>((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const openings = useMemo(() => (room ? twin.openings.filter((o) => o.room_id === room.room_id || o.other_room_id === room.room_id) : []), [twin.openings, room]);
  const inHouse = useMemo(() => new Set((house.data ?? []).map((p) => p.item_id)), [house.data]);
  const stored = (inventory.data ?? []).filter((i) => !inHouse.has(i.item_id));

  // The guide's options for this room, lettered in the order it gave them.
  const preview = previewLayout(layouts.data, selectedLayoutId);
  const options = useMemo(() => {
    if (!room) return [];
    const { variants, scope } = visibleLayouts(layouts.data ?? [], shown, room.room_id);
    const mine =
      scope === 'agent'
        ? variants.filter((l) => l.room_id === room.room_id && l.status !== 'chosen').sort((a, b) => shown.indexOf(a.layout_id) - shown.indexOf(b.layout_id))
        : [];
    return mine.length ? mine : preview && preview.room_id === room.room_id ? [preview] : [];
  }, [layouts.data, shown, room, preview]);
  const trying = preview && preview.room_id === room?.room_id ? preview : undefined;

  const [sheet, setSheet] = useState<SheetKind | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState<{ tone: 'ok' | 'error'; text: string } | null>(null);
  const [asked, setAsked] = useState<{ after: string | null; voice?: boolean } | null>(null);
  const [above, setAbove] = useState(false);
  const controls = useRef<PlanControls | null>(null);
  const me = apiUserId();

  // A new room starts with nothing selected and no sheet open.
  useEffect(() => {
    setSelected(null);
    setSheet(null);
  }, [room?.room_id]);
  // The big screen looks down on the room while "From above" is on.
  useEffect(() => {
    dispatch('furniture.overview', { room_id: above && room ? room.room_id : null });
  }, [above, room?.room_id, dispatch]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => dispatch('furniture.overview', { room_id: null }), [dispatch]);
  // The big screen points out the selected piece.
  useEffect(() => {
    dispatch('furniture.select', { item_id: selected });
  }, [selected, dispatch]);
  useEffect(() => () => dispatch('furniture.select', { item_id: null }), [dispatch]);
  useEffect(() => {
    if (options.length) setAsked(null);
  }, [options.length]);

  // North, turned onto the plan: +x is right and +z is down the page.
  const north = useMemo(() => {
    const [x, , z] = compassToInterior(twin.interior_to_world, 0, 0);
    return (Math.atan2(x, -z) * 180) / Math.PI;
  }, [twin.interior_to_world]);

  // Since the last request, for this phone's person: whether they have spoken yet, and the guide's
  // latest words (its thinking out loud while it works, then its answer).
  const since = useMemo(() => {
    if (!asked) return [];
    const from = asked.after ? chat.findIndex((c) => c.id === asked.after) + 1 : 0;
    return chat.slice(from).filter((c) => (c.kind === 'user' || c.kind === 'agent') && (!memberId || c.member_id === memberId || c.member_id === null));
  }, [asked, chat, memberId]);
  const spoke = since.some((c) => c.kind === 'user');
  const reply = useMemo(() => {
    const said = since.filter((c) => c.kind === 'agent');
    const last = said[said.length - 1];
    return last && last.kind === 'agent' ? last : null;
  }, [since]);

  const ask = (text: string) => {
    setAsked({ after: chat[chat.length - 1]?.id ?? null });
    setNote(null);
    dispatch('chat.user', { text });
  };
  /** Suggest by voice: the microphone opens, and what they say goes to the guide like any message. */
  const askByVoice = () => {
    if (!talk) return;
    if (talk.listening) return talk.stop();
    setAsked({ after: chat[chat.length - 1]?.id ?? null, voice: true });
    setNote(null);
    talk.toggle();
  };
  const waitingForWords = !!asked?.voice && !spoke;
  // The microphone closed without hearing anything: nothing was asked.
  useEffect(() => {
    if (!waitingForWords || talk?.listening) return;
    const t = window.setTimeout(() => setAsked(null), 1500);
    return () => window.clearTimeout(t);
  }, [waitingForWords, talk?.listening]);

  /**
   * Put a piece in this room. `auto`: the server picks the spot by what the piece is (a rug under
   * the seating, a lamp on a free table, a picture on the wall), and anything else goes to the
   * nearest spot to the middle that breaks no rule. It is then selected, to be dragged from there.
   */
  const place = async (item: InventoryItem) => {
    if (!room) return;
    const b = bounds(room.polygon);
    try {
      const placed = await api().placePiece(twin.property_id, item.item_id, { x: (b.minX + b.maxX) / 2, z: (b.minZ + b.maxZ) / 2, room_id: room.room_id, floor_id: room.floor_id, snap: true, auto: true });
      if (trying) dispatch('layout.select', { layout_id: null });
      furnitureChanged(twin.property_id);
      setSheet(null);
      setSelected(item.item_id);
      viewSpot(placed.position.x, placed.position.z);
      setNote({ tone: 'ok', text: `${item.label} is in. Drag it where you want it.` });
    } catch (err) {
      setNote({ tone: 'error', text: reason(err) });
    }
  };

  const takeOut = async (itemId: string) => {
    try {
      await api().removePiece(twin.property_id, itemId);
      if (selected === itemId) setSelected(null);
      furnitureChanged(twin.property_id);
    } catch (err) {
      setNote({ tone: 'error', text: reason(err) });
    }
  };

  /**
   * Inside (not from above), the big screen follows taps on the plan: it goes to the scan point
   * nearest the spot, at least a step away, and faces it.
   */
  const viewSpot = (x: number, z: number) => {
    if (!room || above) return;
    const near = floorSweeps(twin, room.room_id)
      .map((s) => ({ s, d: Math.hypot(s.position.x - x, s.position.z - z) }))
      .sort((a, b) => a.d - b.d);
    const sweep = (near.find((n) => n.d >= VIEW_MIN_M) ?? near[0])?.s;
    if (sweep) dispatch('viewer.goto', { room_id: room.room_id, at_sweep_id: sweep.sweep_id, look_at: { x, y: room.floor_y + 0.4, z } });
  };
  const select = (id: string | null) => {
    const piece = id && id !== selected ? pieces.find((p) => p.item_id === id) : undefined;
    setSelected(id);
    if (piece) viewSpot(piece.position.x, piece.position.z);
  };

  /** Turn the big screen to a piece: stand a few steps away and face it. */
  const lookAt = (itemId: string) => {
    const p = pieces.find((x) => x.item_id === itemId);
    if (!room || !p) return;
    const away = (sw: { position: { x: number; z: number } }) => Math.hypot(sw.position.x - p.position.x, sw.position.z - p.position.z);
    const sweep = [...floorSweeps(twin, room.room_id)].sort((a, b) => Math.abs(away(a) - 2.5) - Math.abs(away(b) - 2.5))[0];
    if (!sweep) return;
    setAbove(false);
    dispatch('viewer.goto', { room_id: room.room_id, at_sweep_id: sweep.sweep_id, look_at: { x: p.position.x, y: sweep.position.y - 1.0, z: p.position.z } });
  };

  /** Delete a piece for good: it leaves storage and every house (F1, F3). */
  const deleteForGood = async (item: InventoryItem) => {
    await api().deleteInventoryItem(item.item_id);
    furnitureChanged(twin.property_id);
    setNote({ tone: 'ok', text: `${item.label} is deleted.` });
  };

  // A photo: tap the piece in it, SAM cuts it out and builds it in 3D, and it goes straight into this room.
  // A photo of the person's own room: everything in it is built, and the server sets this room up with it.
  const { upload, uploadRoom, busy: uploading, error: uploadError, rows, dismiss } = useObjectUpload(
    (itemId) => {
      void api().getInventoryItem(itemId).then(place, (err: unknown) => setNote({ tone: 'error', text: reason(err) }));
    },
    () => {
      furnitureChanged(twin.property_id);
      setSelected(null);
      setNote({ tone: 'ok', text: 'Your room is set up here. Drag anything to move it.' });
    },
  );
  const [photo, setPhoto] = useState<File | null>(null);
  const sendPhoto = async (at: PhotoPoint | undefined) => {
    if (!photo) return;
    if (await upload(photo, at, at ? 'The piece you tapped' : 'Every piece in the photo')) setPhoto(null);
  };

  if (!room) {
    return <p className="p-6 text-center text-sm text-muted">Walk into a room, or pick one in Rooms, to arrange it.</p>;
  }

  const name = roomName(twin, room.room_id);
  const selectedItem = selected ? items.get(selected) : undefined;
  const selectedPiece = pieces.find((p) => p.item_id === selected);
  const front = selectedPiece ? frontDirection(selectedPiece.yaw_deg) : null;
  const facing = front ? compassWord(interiorDirectionToAzimuth(twin.interior_to_world, [front[0], 0, front[1]])) : null;
  const choosing = options.length > 0;
  const current = trying ?? options[0];
  // The island at the top changes height (one row, a piece's tools, the guide's options): the plan
  // always starts under it, with a gap, so the room is never hidden behind the controls.
  const island = useRef<HTMLDivElement | null>(null);
  const [islandHeight, setIslandHeight] = useState(0);
  useEffect(() => {
    const el = island.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const seen = new ResizeObserver(() => setIslandHeight(el.offsetHeight));
    seen.observe(el);
    return () => seen.disconnect();
  }, []);
  // The same when a piece is selected and its tools are up: nothing sits on the plan but the
  // add sheet, which covers the screen on purpose.
  const planTop = !sheet && islandHeight ? islandHeight + 24 : null;

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden bg-ink" data-testid="furniture-room">
      {/* The plan sits between the island at the top and the compass and stick at the bottom, clear of both. */}
      <div className="absolute inset-x-0 top-[6.5rem] bottom-[8.5rem] px-2" style={planTop ? { top: planTop } : undefined}>
        {trying ? (
          <PlanEditor fill readOnly propertyId={twin.property_id} room={room} openings={openings} pieces={trying.placements} items={items} interiorToWorld={twin.interior_to_world} onTapFloor={({ x, z }) => viewSpot(x, z)} />
        ) : (
          <PlanEditor
            fill
            propertyId={twin.property_id}
            room={room}
            openings={openings}
            pieces={pieces}
            items={items}
            selected={selected}
            controls={controls}
            interiorToWorld={twin.interior_to_world}
            onSelect={(id) => {
              select(id);
              if (id) setSheet(null);
            }}
            onDrag={(id, pose) => dispatch('furniture.drag', pose ? { item_id: id, ...pose } : { item_id: null })}
            onTapFloor={({ x, z }) => viewSpot(x, z)}
          />
        )}
      </div>

      <div className="absolute inset-x-3 bottom-1.5 flex items-end justify-between gap-3">
        <div className="flex items-center gap-2">
          <Compass angle={north} />
          <RoundButton label={above ? 'Back inside on the big screen' : 'See the room from above on the big screen'} on={above} onClick={() => setAbove((a) => !a)}>
            <IconCube width={18} height={18} />
          </RoundButton>
        </div>
        <LookStick />
      </div>

      <div ref={island} className="absolute inset-x-3 top-3 flex flex-col gap-2">
        {sheet === 'add' && (
          <AddModal name={name} stored={stored} me={me} host={me === config.userId} onClose={() => setSheet(null)} onPlace={place} onDelete={deleteForGood}
            ownRoom={<OwnRoom roomName={name} busy={uploading} onSend={(file) => uploadRoom(file, twin.property_id, room.room_id)} />}
            photo={
              (photo || uploadError || rows.length > 0) && (
                <div className="space-y-2">
                  {photo && <TapToPick file={photo} busy={uploading} onSubmit={(at) => void sendPhoto(at)} onCancel={() => setPhoto(null)} />}
                  {uploadError && <p className="text-xs text-danger">{uploadError}</p>}
                  <JobRows rows={rows} dismiss={dismiss} />
                </div>
              )
            }
            photoCard={photo ? null : <PhotoButtons layout="card" disabled={uploading} onPicked={(f) => void preparePhoto(f).then(setPhoto)} />}
          />
        )}
        {choosing && current ? (
          <OptionsIsland
            options={options}
            current={current}
            onTry={(l) => dispatch('layout.select', { layout_id: l.layout_id })}
            onKeep={(l) => {
              dispatch('layout.confirm', { layout_id: l.layout_id });
              dispatch('design.showVariants', { layout_ids: [] });
              setNote({ tone: 'ok', text: 'Kept. It is in the house now.' });
            }}
            onClose={() => {
              dispatch('layout.select', { layout_id: null });
              dispatch('design.showVariants', { layout_ids: [] });
            }}
            onFeedback={(l, letter, why) =>
              ask(`I like option ${letter}${why ? ` because ${why}` : ''}. Suggest a few more layouts for the ${name.toLowerCase()} like it. [layout_id ${l.layout_id}]`)
            }
            onMore={() => ask(`Suggest a few more layouts for the ${name.toLowerCase()}, different from these.`)}
          />
        ) : selectedItem && selectedPiece ? (
          <section className={cn(ISLAND, 'px-3 pt-2.5 pb-3')} aria-label={`Arrange ${selectedItem.label}`} data-testid="piece-tools">
            <div className="flex items-center gap-1.5">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold">{selectedItem.label}{selectedItem.tag && <span className="ml-1.5 font-mono text-xs font-normal text-muted">#{selectedItem.tag}</span>}</p>
                {facing && <p className="text-[11px] font-semibold text-leaf-deep">Faces {facing}</p>}
              </div>
              <RoundButton label="Look at it on the big screen" onClick={() => lookAt(selectedPiece.item_id)}><IconEye width={17} height={17} /></RoundButton>
              <RoundButton label="Take it out of the room" onClick={() => controls.current?.remove()}><IconTrash width={16} height={16} /></RoundButton>
              <RoundButton label="Done" on onClick={() => setSelected(null)}><IconCheck width={17} height={17} /></RoundButton>
            </div>
            <div className="mt-2.5 flex items-start justify-around border-t border-line pt-2.5">
              <ToolGroup title="Turn">
                <div className="grid grid-cols-2 gap-1.5">
                  <RoundButton label="Turn left 15 degrees" onClick={() => controls.current?.turn(15)}><IconTurnLeft width={18} height={18} /></RoundButton>
                  <RoundButton label="Turn right 15 degrees" onClick={() => controls.current?.turn(-15)}><IconTurnRight width={18} height={18} /></RoundButton>
                  <button type="button" aria-label="Turn a quarter" title="Turn a quarter" onClick={() => controls.current?.turn(90)}
                    className={cn('col-span-2 h-10 rounded-full border text-xs font-semibold transition-transform active:scale-95', OFF)}>
                    90°
                  </button>
                </div>
              </ToolGroup>
              <div className="w-px self-stretch bg-line" aria-hidden />
              <ToolGroup title={`Move · ${NUDGE_M * 100} cm`}>
                <div className="grid grid-cols-3 gap-1.5">
                  <RoundButton label="Move up the plan" onClick={() => controls.current?.nudge(0, -NUDGE_M)} className="col-start-2"><IconArrowUp width={16} height={16} /></RoundButton>
                  <RoundButton label="Move left" onClick={() => controls.current?.nudge(-NUDGE_M, 0)} className="col-start-1"><IconArrowUp width={16} height={16} className="-rotate-90" /></RoundButton>
                  <RoundButton label="Move down the plan" onClick={() => controls.current?.nudge(0, NUDGE_M)}><IconArrowUp width={16} height={16} className="rotate-180" /></RoundButton>
                  <RoundButton label="Move right" onClick={() => controls.current?.nudge(NUDGE_M, 0)}><IconArrowUp width={16} height={16} className="rotate-90" /></RoundButton>
                </div>
              </ToolGroup>
              <div className="w-px self-stretch bg-line" aria-hidden />
              <ToolGroup title={`Size · ${Math.round((selectedItem.scale ?? 1) * 100)}%`}>
                <div className="grid grid-cols-1 gap-1.5">
                  <button type="button" aria-label="Make it bigger" onClick={() => controls.current?.resize(SIZE_STEP)}
                    className={cn('h-10 w-10 rounded-full border text-lg font-semibold transition-transform active:scale-95', OFF)}>+</button>
                  <button type="button" aria-label="Make it smaller" onClick={() => controls.current?.resize(1 / SIZE_STEP)}
                    className={cn('h-10 w-10 rounded-full border text-lg font-semibold transition-transform active:scale-95', OFF)}>−</button>
                </div>
              </ToolGroup>
            </div>
          </section>
        ) : (
          <nav className={cn(ISLAND, 'grid grid-cols-3 gap-1 p-1.5')} aria-label="Furniture" data-testid="furniture-island">
            <IslandButton label="Add" onClick={() => setSheet('add')}><IconPlus width={20} height={20} /></IslandButton>
            <IslandButton label={`In room · ${pieces.length}`} onClick={() => setSheet('room')}><IconList width={20} height={20} /></IslandButton>
            {talk?.supported ? (
              <IslandButton label={talk.listening ? 'Listening' : 'Suggest'} onClick={askByVoice} live={talk.listening} disabled={!!asked && !reply && !waitingForWords}>
                <IconMic width={20} height={20} />
              </IslandButton>
            ) : (
              <IslandButton label="Suggest" onClick={() => setSheet('suggest')} disabled={!!asked && !reply}><IconSparkle width={20} height={20} /></IslandButton>
            )}
          </nav>
        )}

        {sheet === 'room' ? (
          <Sheet title={`In the ${name.toLowerCase()} · ${pieces.length}`} onClose={() => setSheet(null)}>
            {pieces.length ? (
              <ul className="space-y-1.5">
                {pieces.map((p) => {
                  const item = items.get(p.item_id);
                  return (
                    <li key={p.item_id} className="flex items-center gap-2 rounded-xl border border-line bg-raised py-1 pr-1 pl-3">
                      <button type="button" className="min-w-0 flex-1 py-1 text-left" onClick={() => { select(p.item_id); setSheet(null); }}>
                        <span className="block truncate text-sm font-semibold">{item?.label ?? 'A piece'}</span>
                        {/* The piece's own number: what the guide calls it, and what you can call it too. */}
                        {item && (
                          <span className="block truncate text-xs text-muted">
                            {item.tag ? <span className="font-mono">#{item.tag} · </span> : null}
                            {item.owner_name ? `${item.owner_name}'s · ` : ''}
                            {/* A lamp on a desk, a picture on the wall: it says so (and moves with the desk). */}
                            {p.on_item_id ? `on the ${(items.get(p.on_item_id)?.label ?? 'piece under it').toLowerCase()}` : p.lift_m ? `on the wall, ${p.lift_m.toFixed(1)} m up` : `${item.dims_m.w.toFixed(1)}×${item.dims_m.d.toFixed(1)} m`}
                          </span>
                        )}
                      </button>
                      <RoundButton label={`Take ${item?.label ?? 'it'} out of the room`} onClick={() => void takeOut(p.item_id)} className="h-8 w-8">
                        <IconTrash width={15} height={15} />
                      </RoundButton>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-sm text-muted">Nothing here yet.</p>
            )}
          </Sheet>
        ) : sheet === 'suggest' ? (
          <SuggestSheet
            name={name}
            empty={!pieces.length}
            onClose={() => setSheet(null)}
            onAsk={(text) => { ask(text); setSheet(null); }}
          />
        ) : null}

        {/* A room being rebuilt from a photo takes a minute and a half: its progress stays in view after the Add window is closed. */}
        {sheet !== 'add' && rows.some((r) => r.room) && (
          <div className={cn(ISLAND, 'p-2')} data-testid="own-room-progress">
            <JobRows rows={rows.filter((r) => r.room)} dismiss={dismiss} />
          </div>
        )}

        {(note || asked) && !sheet && (
          <div role="status" className={cn(ISLAND, 'flex items-start gap-2 px-3 py-2 text-sm')}>
            <p className={cn('min-w-0 flex-1 leading-5', note?.tone === 'error' ? 'text-danger' : 'text-text')} data-testid="furniture-status">
              {note ? note.text : reply ? reply.text : waitingForWords ? (
                <>
                  Tell the guide what you want for the {name.toLowerCase()}, or say “surprise me”.
                  <button type="button" className="ml-1.5 font-semibold text-leaf-deep underline underline-offset-2"
                    onClick={() => { talk?.stop(); setAsked(null); setSheet('suggest'); }}>
                    Type instead
                  </button>
                </>
              ) : (
                <span className="inline-flex items-center gap-2 text-muted"><Spinner /> The guide is thinking…</span>
              )}
            </p>
            <button type="button" aria-label="Dismiss" onClick={() => { setNote(null); setAsked(null); }} className="shrink-0 text-muted">
              <IconClose width={14} height={14} />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function ToolGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div role="group" aria-label={title} className="flex flex-col items-center gap-1.5">
      <span className="text-[10px] font-semibold tracking-wider text-faint uppercase">{title}</span>
      {children}
    </div>
  );
}

function IslandButton({ label, onClick, children, disabled = false, live = false }: { label: string; onClick(): void; children: ReactNode; disabled?: boolean; live?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} aria-pressed={live || undefined}
      className={cn('flex flex-col items-center gap-0.5 rounded-xl py-1.5 text-leaf-deep active:bg-leaf/15 disabled:opacity-40', live && 'mic-live bg-leaf/20')}>
      {children}
      <span className="text-[11px] font-semibold text-text">{label}</span>
    </button>
  );
}

type AddTab = 'mine' | 'family' | 'catalog';
const ADD_TABS: { tab: AddTab; label: string; icon: typeof IconBag }[] = [
  { tab: 'mine', label: 'My furniture', icon: IconBackpack },
  { tab: 'family', label: "Family's", icon: IconPeople },
  { tab: 'catalog', label: 'Catalog', icon: IconBag },
];
/** The host (the big screen's user) has no member name. */
const SHARED = 'Shared';

const kind = (i: InventoryItem) => i.item_class.replace(/_/g, ' ');
const size = (i: InventoryItem) => `${i.dims_m.w.toFixed(1)}×${i.dims_m.d.toFixed(1)} m`;

/**
 * Adding a piece, as a modal over the room: a backpack tab for what you own (and a photo of
 * something new), a people tab for the rest of the family's, a shopping-bag tab for the catalog.
 * Tapping a card puts it in the room. Manage turns the cards into delete buttons, for the pieces
 * you may delete (yours; the host may delete anyone's).
 */
function AddModal({ name, stored, me, host, onClose, onPlace, onDelete, photo, photoCard, ownRoom }: {
  name: string;
  /** The family's pieces that are in no room of this house. */
  stored: InventoryItem[];
  /** Who this phone speaks for (X-User-Id). */
  me: string;
  /** This phone is the host's: it may delete every piece of the family. */
  host: boolean;
  onClose(): void;
  onPlace(item: InventoryItem): Promise<void>;
  onDelete(item: InventoryItem): Promise<void>;
  /** The hidden camera input and upload progress. */
  photo: ReactNode;
  /** The "From a photo" card that opens the camera. */
  photoCard: ReactNode;
  /** "My room from a photo": the whole room the person lives in now, set up here. */
  ownRoom: ReactNode;
}) {
  const own = stored.filter((i) => i.user_id === me);
  const others = stored.filter((i) => i.user_id !== me);
  const [tab, setTab] = useState<AddTab>(own.length ? 'mine' : others.length ? 'family' : 'catalog');
  const [query, setQuery] = useState('');
  const [managing, setManaging] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hit = (i: InventoryItem) => matches(query, i.label, kind(i), i.owner_name ?? '');
  const mine = own.filter(hit);
  const family = useMemo(() => {
    const groups = new Map<string, InventoryItem[]>();
    for (const i of others.filter(hit)) {
      const who = i.owner_name ?? SHARED;
      groups.set(who, [...(groups.get(who) ?? []), i]);
    }
    return [...groups];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stored, me, query]);
  const mayDelete = (i: InventoryItem) => i.user_id === me || host;
  const list = tab === 'mine' ? own : others;
  const canManage = tab !== 'catalog' && list.some(mayDelete);

  useEffect(() => {
    setConfirming(null);
    setError(null);
  }, [tab, managing]);
  useEffect(() => {
    if (!canManage) setManaging(false);
  }, [canManage]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (confirming) setConfirming(null);
      else onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, confirming]);
  useScrollLock();

  const run = async (item: InventoryItem, fn: (item: InventoryItem) => Promise<void>) => {
    setBusy(item.item_id);
    setError(null);
    try {
      await fn(item);
      setConfirming(null);
    } catch (err) {
      setError(reason(err));
    } finally {
      setBusy(null);
    }
  };

  const card = (i: InventoryItem, sub: string) =>
    confirming === i.item_id ? (
      <div role="group" aria-label={`Delete ${i.label} for good?`} className="flex h-full w-full flex-col rounded-xl border border-danger/50 bg-raised p-2">
        <CardImage src={i.source_image_url} state="delete" />
        <span className={CARD_NAME}>Delete {i.label}?</span>
        <span className={CARD_SUB}>It leaves every house.</span>
        <div className="mt-1.5 flex gap-1.5">
          <button type="button" disabled={busy !== null} onClick={() => void run(i, onDelete)} className="flex h-8 flex-1 items-center justify-center rounded-full bg-danger text-xs font-semibold text-white disabled:opacity-60">
            {busy === i.item_id ? <Spinner /> : 'Delete'}
          </button>
          <button type="button" disabled={busy !== null} onClick={() => setConfirming(null)} className={cn('h-8 flex-1 rounded-full border text-xs font-semibold', OFF)}>
            Keep
          </button>
        </div>
      </div>
    ) : managing ? (
      <button type="button" disabled={busy !== null || !mayDelete(i)} onClick={() => setConfirming(i.item_id)}
        aria-label={mayDelete(i) ? `Delete ${i.label}` : `${i.label}: only ${i.owner_name ?? 'its owner'} can delete it`}
        title={mayDelete(i) ? undefined : `Only ${i.owner_name ?? 'its owner'} can delete it`} className={cn(CARD, !mayDelete(i) && 'opacity-45')}>
        <CardImage src={i.source_image_url} state={mayDelete(i) ? 'delete' : 'idle'} />
        <span className={CARD_NAME}>{i.label}</span>
        <span className={CARD_SUB}>{sub}</span>
      </button>
    ) : (
      <button type="button" disabled={busy !== null} onClick={() => void run(i, onPlace)} aria-label={`Add ${i.label} to the ${name.toLowerCase()}`} aria-busy={busy === i.item_id} className={CARD}>
        <CardImage src={i.source_image_url} state={busy === i.item_id ? 'adding' : 'idle'} />
        <span className={CARD_NAME}>{i.label}</span>
        <span className={CARD_SUB}>{sub}</span>
      </button>
    );

  const empty = (text: string) => <p className="pt-1 text-center text-[13px] text-muted">{text}</p>;

  return createPortal(
    <div className={MODAL_BACKDROP} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <section role="dialog" aria-modal aria-label={`Add to the ${name.toLowerCase()}`} data-testid="add-modal"
        className="flex h-[min(85dvh,44rem)] w-full max-w-md flex-col overflow-hidden rounded-3xl border border-line bg-surface shadow-float">
        <header className="flex shrink-0 items-center gap-2 px-3 pt-3 pb-2">
          <h2 className="min-w-0 truncate font-display text-sm font-semibold">{managing ? 'Manage furniture' : `Add to the ${name.toLowerCase()}`}</h2>
          <div className="ml-auto flex items-center gap-2">
            {canManage && (
              <button type="button" aria-pressed={managing} onClick={() => setManaging((m) => !m)} data-testid="manage-toggle"
                className={cn('flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-semibold', managing ? ON : OFF)}>
                {managing ? <IconCheck width={14} height={14} /> : <IconGear width={14} height={14} />}
                {managing ? 'Done' : 'Manage'}
              </button>
            )}
            <RoundButton label="Close" onClick={onClose} className="h-8 w-8">
              <IconClose width={16} height={16} />
            </RoundButton>
          </div>
        </header>
        <div role="tablist" aria-label="Where from" className="mx-3 flex shrink-0 gap-1 rounded-full bg-ink p-1">
          {ADD_TABS.map(({ tab: t, label, icon: Icon }) => (
            <button key={t} type="button" role="tab" aria-selected={t === tab} aria-label={label} title={label} data-tab={t} onClick={() => setTab(t)}
              className={cn('flex h-9 min-w-0 items-center justify-center gap-1.5 rounded-full text-xs font-semibold whitespace-nowrap transition-colors',
                t === tab ? 'flex-[2] bg-raised text-text shadow-[0_2px_0_var(--color-line)]' : 'flex-1 text-muted')}>
              <Icon width={17} height={17} className="shrink-0" />
              {t === tab && <span>{label}</span>}
            </button>
          ))}
        </div>
        <div className={cn(MODAL_SCROLL, 'space-y-2 px-3 pt-3 pb-4')}>
          {managing && <p className="text-xs text-muted">Tap a piece to delete it for good. Pieces standing in a room come out of it first (In room).</p>}
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          {tab === 'mine' ? (
            <>
              {!managing && ownRoom}
              {!managing && photo}
              {own.length > 0 && <SearchBox value={query} onChange={setQuery} placeholder="Search your furniture" />}
              <ul className="grid grid-cols-2 gap-2">
                {!managing && photoCard && <li>{photoCard}</li>}
                {mine.map((i) => <li key={i.item_id}>{card(i, `${kind(i)} · ${size(i)}`)}</li>)}
              </ul>
              {!own.length && empty('Everything you own is already in the house. Take a photo to add something new, or look in the catalog.')}
              {own.length > 0 && !mine.length && empty(`None of your furniture matches “${query.trim()}”.`)}
            </>
          ) : tab === 'family' ? (
            <>
              {others.length > 0 && <SearchBox value={query} onChange={setQuery} placeholder="Search the family's furniture" />}
              {family.map(([who, pieces]) => (
                <section key={who} aria-label={who === SHARED ? 'Shared furniture' : `${who}'s furniture`} className="space-y-1.5 pt-1">
                  <h3 className="text-[11px] font-semibold tracking-wider text-faint uppercase">{who} · {pieces.length}</h3>
                  <ul className="grid grid-cols-2 gap-2">
                    {pieces.map((i) => <li key={i.item_id}>{card(i, `${kind(i)} · ${size(i)}`)}</li>)}
                  </ul>
                </section>
              ))}
              {!others.length && empty("Nothing of the rest of the family's is waiting: it is all in the house, or they have not added any yet.")}
              {others.length > 0 && !family.length && empty(`None of the family's furniture matches “${query.trim()}”.`)}
            </>
          ) : (
            <CatalogBrowser onAdded={onPlace} scroll={false} />
          )}
        </div>
      </section>
    </div>,
    document.body,
  );
}

const WISHES = ['Keep the walkway clear', 'Bed on the north wall', 'Desk by the window', 'Room for guests'];

function SuggestSheet({ name, empty, onClose, onAsk }: { name: string; empty: boolean; onClose(): void; onAsk(text: string): void }) {
  const [wish, setWish] = useState('');
  const send = () => {
    const base = empty
      ? `Furnish the ${name.toLowerCase()} with pieces from our furniture and suggest 3 layouts.`
      : `Suggest 3 better layouts for the ${name.toLowerCase()} with the furniture that is in it now.`;
    onAsk(wish.trim() ? `${base} ${wish.trim()}` : base);
  };
  return (
    <Sheet title="Ask the guide for layouts" onClose={onClose}>
      <textarea value={wish} onChange={(e) => setWish(e.target.value)} rows={2} aria-label="What matters to you"
        placeholder="Anything to keep in mind? Walls go by the compass: “the sofa on the west wall”."
        className="w-full resize-none rounded-xl border border-line bg-raised px-3 py-2 text-[15px] placeholder:text-faint focus:border-line-strong focus:outline-none" />
      <div className="mt-2 flex flex-wrap gap-1.5">
        {WISHES.map((w) => (
          <button key={w} type="button" onClick={() => setWish((t) => (t.trim() ? `${t.trim()}. ${w}` : w))} className={cn('rounded-full border px-2.5 py-1 text-xs font-semibold', OFF)}>
            {w}
          </button>
        ))}
      </div>
      <button type="button" onClick={send} className="mt-3 flex h-11 w-full items-center justify-center gap-2 rounded-full bg-main text-sm font-semibold text-on-main">
        <IconSparkle width={16} height={16} /> Suggest layouts
      </button>
    </Sheet>
  );
}

function OptionsIsland({ options, current, onTry, onKeep, onClose, onFeedback, onMore }: {
  options: LayoutVariant[];
  current: LayoutVariant;
  onTry(l: LayoutVariant): void;
  onKeep(l: LayoutVariant): void;
  onClose(): void;
  onFeedback(l: LayoutVariant, letter: string, why: string): void;
  onMore(): void;
}) {
  const [why, setWhy] = useState('');
  const index = Math.max(0, options.findIndex((l) => l.layout_id === current.layout_id));
  const letter = LETTERS[index] ?? String(index + 1);
  return (
    <section className={cn(ISLAND, 'space-y-2 px-3 py-2.5')} aria-label="Layout options" data-testid="layout-options">
      <div className="flex items-center gap-1.5">
        <div className="flex gap-1.5" role="group" aria-label="Options">
          {options.map((l, i) => (
            <button key={l.layout_id} type="button" aria-pressed={l.layout_id === current.layout_id} onClick={() => onTry(l)}
              className={cn('h-9 w-9 rounded-full border text-sm font-bold', l.layout_id === current.layout_id ? 'border-main bg-main text-on-main' : OFF)}>
              {LETTERS[i] ?? i + 1}
            </button>
          ))}
        </div>
        <button type="button" onClick={() => onKeep(current)} className="ml-auto h-9 rounded-full bg-main px-4 text-sm font-semibold text-on-main">
          Keep {letter}
        </button>
        <RoundButton label="Back to the room as it is" onClick={onClose} className="h-9 w-9">
          <IconClose width={16} height={16} />
        </RoundButton>
      </div>
      {current.rationale && <p className="line-clamp-2 text-xs leading-4 text-muted">{current.rationale}</p>}
      <form className="flex items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); onFeedback(current, letter, why.trim()); setWhy(''); }}>
        <input value={why} onChange={(e) => setWhy(e.target.value)} placeholder={`I like ${letter} because…`} aria-label={`What you like about option ${letter}`} enterKeyHint="send"
          className="h-9 min-w-0 flex-1 rounded-full border border-line bg-raised px-3 text-sm placeholder:text-faint focus:border-line-strong focus:outline-none" />
        <RoundButton label={`Tell the guide what you like about ${letter}`} onClick={() => { onFeedback(current, letter, why.trim()); setWhy(''); }} className="h-9 w-9">
          <IconSend width={15} height={15} />
        </RoundButton>
        <button type="button" onClick={onMore} className={cn('h-9 shrink-0 rounded-full border px-3 text-xs font-semibold', OFF)}>More</button>
      </form>
    </section>
  );
}
