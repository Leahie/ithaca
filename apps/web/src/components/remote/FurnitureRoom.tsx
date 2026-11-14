'use client';

import type { InventoryItem, LayoutVariant, PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { CatalogBrowser } from '@/components/design/CatalogBrowser';
import { PlanEditor, type PlanControls } from '@/components/design/PlanEditor';
import { furnitureChanged } from '@/components/house/ArrangePanel';
import { JobRows, useObjectUpload } from '@/components/me/InventorySection';
import { compassWord } from '@/components/sunlab/TimeScrubber';
import { Spinner, cn } from '@/components/ui';
import { IconArrowUp, IconCamera, IconCheck, IconClose, IconEye, IconList, IconPlus, IconSend, IconSparkle, IconTrash, IconTurnLeft, IconTurnRight } from '@/components/ui/icons';
import { api } from '@/lib/api';
import { useHouseFurniture, useInventory, useLayouts } from '@/lib/data/hooks';
import { compassToInterior, interiorDirectionToAzimuth } from '@/lib/geometry/frames';
import { bounds, frontDirection } from '@/lib/geometry/polygon';
import { previewLayout, visibleLayouts } from '@/lib/layouts';
import { roomName } from '@/lib/rooms';
import { floorSweeps } from '@/lib/showcase/rooms';
import { useFamily } from '@/lib/store/family';
import { useSession } from '@/lib/store/session';
import { OFF, ON } from './LightPanel';

const NUDGE_M = 0.1;
const LETTERS = 'ABCDEFGH';
const ISLAND = 'rounded-2xl border border-line bg-surface shadow-lg';

/** The server's refusal in words: `{"violations": ["…"]}` comes back as JSON text. */
const reason = (err: unknown) => {
  const text = err instanceof Error ? err.message : String(err);
  return /"violations":\s*\["([^"]+)"/.exec(text)?.[1] ?? text;
};

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
    <section className={cn(ISLAND, 'flex max-h-[58dvh] flex-col overflow-hidden')} aria-label={title}>
      <header className="flex shrink-0 items-center gap-2 border-b border-line px-4 py-2">
        <h2 className="text-[11px] font-semibold tracking-wider text-faint uppercase">{title}</h2>
        <div className="ml-auto flex items-center gap-2">
          {action}
          <RoundButton label="Close" onClick={onClose} className="h-8 w-8">
            <IconClose width={16} height={16} />
          </RoundButton>
        </div>
      </header>
      <div className="min-h-0 overflow-y-auto px-4 py-3">{children}</div>
    </section>
  );
}

type SheetKind = 'add' | 'room' | 'suggest';

/**
 * The phone's furniture screen: the room you are in, from above, filling the screen, with north on
 * a compass and each wall lettered by its side. An island at the bottom adds pieces, lists what is
 * in the room and asks the guide for layouts; tapping a piece swaps it for tools to turn, move or
 * remove that piece. While the guide's options are up, the island picks between them, keeps one,
 * or tells the guide what you like about one so it can suggest more.
 */
export function FurnitureRoom({ twin }: { twin: PropertyTwin }) {
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
  const [asked, setAsked] = useState<{ after: string | null } | null>(null);
  const controls = useRef<PlanControls | null>(null);

  // A new room starts with nothing selected and no sheet open.
  useEffect(() => {
    setSelected(null);
    setSheet(null);
  }, [room?.room_id]);
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

  // The guide's latest words since the last request, for this phone's person.
  const reply = useMemo(() => {
    if (!asked) return null;
    const from = asked.after ? chat.findIndex((c) => c.id === asked.after) + 1 : 0;
    const said = chat.slice(from).filter((c) => c.kind === 'agent' && (!memberId || c.member_id === memberId || c.member_id === null));
    const last = said[said.length - 1];
    return last && last.kind === 'agent' ? last : null;
  }, [asked, chat, memberId]);

  const ask = (text: string) => {
    setAsked({ after: chat[chat.length - 1]?.id ?? null });
    setNote(null);
    dispatch('chat.user', { text });
  };

  /** Put a piece in this room: the server finds the nearest spot to the middle that breaks no rule. */
  const place = async (item: InventoryItem) => {
    if (!room) return;
    const b = bounds(room.polygon);
    try {
      await api().placePiece(twin.property_id, item.item_id, { x: (b.minX + b.maxX) / 2, z: (b.minZ + b.maxZ) / 2, room_id: room.room_id, floor_id: room.floor_id, snap: true });
      if (trying) dispatch('layout.select', { layout_id: null });
      furnitureChanged(twin.property_id);
      setSheet(null);
      setSelected(item.item_id);
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

  /** Turn the big screen to a piece: stand a few steps away and face it. */
  const lookAt = (itemId: string) => {
    const p = pieces.find((x) => x.item_id === itemId);
    if (!room || !p) return;
    const away = (sw: { position: { x: number; z: number } }) => Math.hypot(sw.position.x - p.position.x, sw.position.z - p.position.z);
    const sweep = [...floorSweeps(twin, room.room_id)].sort((a, b) => Math.abs(away(a) - 2.5) - Math.abs(away(b) - 2.5))[0];
    if (sweep) dispatch('viewer.goto', { room_id: room.room_id, at_sweep_id: sweep.sweep_id, look_at: { x: p.position.x, y: sweep.position.y - 1.0, z: p.position.z } });
  };

  // A photo that has become a piece goes straight into this room.
  const { upload, busy: uploading, error: uploadError, rows, dismiss } = useObjectUpload();
  const camera = useRef<HTMLInputElement>(null);
  const placedJobs = useRef(new Set<string>());
  useEffect(() => {
    for (const r of rows) {
      const itemId = r.progress?.status === 'done' ? r.progress.result_ref?.split(',')[0] : undefined;
      if (!itemId || placedJobs.current.has(r.job_id)) continue;
      placedJobs.current.add(r.job_id);
      void api().getInventoryItem(itemId).then(place, (err: unknown) => setNote({ tone: 'error', text: reason(err) }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

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

  return (
    <div className="relative min-h-0 flex-1 overflow-hidden bg-ink" data-testid="furniture-room">
      <div className="absolute inset-x-0 top-0 bottom-28 px-2 pt-2">
        {trying ? (
          <PlanEditor fill readOnly propertyId={twin.property_id} room={room} openings={openings} pieces={trying.placements} items={items} interiorToWorld={twin.interior_to_world} />
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
              setSelected(id);
              if (id) setSheet(null);
            }}
          />
        )}
      </div>

      <div className="absolute top-3 right-3">
        <Compass angle={north} />
      </div>
      <div className="absolute inset-x-3 bottom-3 flex flex-col gap-2">
        {!pieces.length && !trying && house.data && !sheet && !note && !asked && (
          <p className="pointer-events-none text-center text-sm text-muted">Nothing in the {name.toLowerCase()} yet. Tap Add to bring in a piece.</p>
        )}
        {(note || asked) && !sheet && (
          <div role="status" className={cn(ISLAND, 'flex items-start gap-2 px-3 py-2 text-sm')}>
            <p className={cn('min-w-0 flex-1 leading-5', note?.tone === 'error' ? 'text-danger' : 'text-text')}>
              {note ? note.text : reply ? reply.text : (
                <span className="inline-flex items-center gap-2 text-muted"><Spinner /> The guide is working on layouts…</span>
              )}
            </p>
            <button type="button" aria-label="Dismiss" onClick={() => { setNote(null); setAsked(null); }} className="shrink-0 text-muted">
              <IconClose width={14} height={14} />
            </button>
          </div>
        )}

        {sheet === 'add' ? (
          <Sheet
            title={`Add to the ${name.toLowerCase()}`}
            onClose={() => setSheet(null)}
            action={
              <RoundButton label={uploading ? 'Uploading…' : 'Add a piece from a photo'} onClick={() => camera.current?.click()} disabled={uploading} on={uploading} className="h-8 w-8">
                {uploading ? <Spinner /> : <IconCamera width={16} height={16} />}
              </RoundButton>
            }
          >
            <input ref={camera} type="file" accept="image/*" capture="environment" className="sr-only" data-testid="capture-input"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ''; }} />
            {(uploadError || rows.length > 0) && (
              <div className="mb-3 space-y-2">
                {uploadError && <p className="text-xs text-danger">{uploadError}</p>}
                <JobRows rows={rows} dismiss={dismiss} />
              </div>
            )}
            {stored.length > 0 && (
              <>
                <h3 className="mb-1.5 text-xs font-semibold text-muted">Your furniture</h3>
                <ul className="mb-3 grid grid-cols-2 gap-2">
                  {stored.map((i) => (
                    <li key={i.item_id}>
                      <button type="button" onClick={() => void place(i)} className="flex w-full flex-col rounded-xl border border-line bg-raised px-3 py-2 text-left active:scale-[0.98]">
                        <span className="truncate text-sm font-semibold">{i.label}</span>
                        <span className="truncate text-xs text-muted">{i.owner_name ?? i.item_class.replace(/_/g, ' ')} · {i.dims_m.w.toFixed(1)}×{i.dims_m.d.toFixed(1)} m</span>
                      </button>
                    </li>
                  ))}
                </ul>
                <h3 className="mb-1.5 text-xs font-semibold text-muted">From the catalog</h3>
              </>
            )}
            <CatalogBrowser onAdded={place} />
          </Sheet>
        ) : sheet === 'room' ? (
          <Sheet title={`In the ${name.toLowerCase()} · ${pieces.length}`} onClose={() => setSheet(null)}>
            {pieces.length ? (
              <ul className="space-y-1.5">
                {pieces.map((p) => {
                  const item = items.get(p.item_id);
                  return (
                    <li key={p.item_id} className="flex items-center gap-2 rounded-xl border border-line bg-raised py-1 pr-1 pl-3">
                      <button type="button" className="min-w-0 flex-1 py-1 text-left" onClick={() => { setSelected(p.item_id); setSheet(null); }}>
                        <span className="block truncate text-sm font-semibold">{item?.label ?? 'A piece'}</span>
                        {item && <span className="block truncate text-xs text-muted">{item.owner_name ? `${item.owner_name}'s · ` : ''}{item.dims_m.w.toFixed(1)}×{item.dims_m.d.toFixed(1)} m</span>}
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
        ) : choosing && current ? (
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
          <section className={cn(ISLAND, 'px-3 py-2.5')} aria-label={`Arrange ${selectedItem.label}`} data-testid="piece-tools">
            <div className="mb-2 flex items-center gap-2">
              <span className="min-w-0 truncate text-sm font-semibold">{selectedItem.label}</span>
              {facing && <span className="shrink-0 rounded-full bg-leaf/15 px-2 py-0.5 text-[11px] font-semibold text-leaf-deep">faces {facing}</span>}
              <RoundButton label="Done" onClick={() => setSelected(null)} className="ml-auto h-8 w-8">
                <IconCheck width={16} height={16} />
              </RoundButton>
            </div>
            <div className="flex items-center justify-between gap-1.5">
              <RoundButton label="Turn left 15 degrees" onClick={() => controls.current?.turn(15)}><IconTurnLeft width={18} height={18} /></RoundButton>
              <RoundButton label="Turn right 15 degrees" onClick={() => controls.current?.turn(-15)}><IconTurnRight width={18} height={18} /></RoundButton>
              <RoundButton label="Turn a quarter" onClick={() => controls.current?.turn(90)}><span className="text-xs font-semibold">90°</span></RoundButton>
              <div className="grid grid-cols-3 grid-rows-2 gap-1" role="group" aria-label="Move">
                <span />
                <RoundButton label="Move up the plan" onClick={() => controls.current?.nudge(0, -NUDGE_M)} className="h-8 w-8"><IconArrowUp width={14} height={14} /></RoundButton>
                <span />
                <RoundButton label="Move left" onClick={() => controls.current?.nudge(-NUDGE_M, 0)} className="h-8 w-8"><IconArrowUp width={14} height={14} className="-rotate-90" /></RoundButton>
                <RoundButton label="Move down the plan" onClick={() => controls.current?.nudge(0, NUDGE_M)} className="h-8 w-8"><IconArrowUp width={14} height={14} className="rotate-180" /></RoundButton>
                <RoundButton label="Move right" onClick={() => controls.current?.nudge(NUDGE_M, 0)} className="h-8 w-8"><IconArrowUp width={14} height={14} className="rotate-90" /></RoundButton>
              </div>
              <RoundButton label="Look at it on the big screen" onClick={() => lookAt(selectedPiece.item_id)}><IconEye width={18} height={18} /></RoundButton>
              <RoundButton label="Take it out of the room" onClick={() => controls.current?.remove()}><IconTrash width={17} height={17} /></RoundButton>
            </div>
          </section>
        ) : (
          <nav className={cn(ISLAND, 'grid grid-cols-3 gap-1 p-1.5')} aria-label="Furniture" data-testid="furniture-island">
            <IslandButton label="Add" onClick={() => setSheet('add')}><IconPlus width={20} height={20} /></IslandButton>
            <IslandButton label={`In room · ${pieces.length}`} onClick={() => setSheet('room')}><IconList width={20} height={20} /></IslandButton>
            <IslandButton label="Suggest" onClick={() => setSheet('suggest')} disabled={!!asked && !reply}><IconSparkle width={20} height={20} /></IslandButton>
          </nav>
        )}
      </div>
    </div>
  );
}

function IslandButton({ label, onClick, children, disabled = false }: { label: string; onClick(): void; children: ReactNode; disabled?: boolean }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled}
      className="flex flex-col items-center gap-0.5 rounded-xl py-1.5 text-leaf-deep active:bg-leaf/15 disabled:opacity-40">
      {children}
      <span className="text-[11px] font-semibold text-text">{label}</span>
    </button>
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
