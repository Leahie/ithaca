'use client';

import type { InventoryItem, JobProgressPayload, LibraryPiece, PhotoPoint, PropertyTwin, Room } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import { furnitureChanged } from '@/components/house/ArrangePanel';
import { JobRows, useObjectUpload } from '@/components/me/InventorySection';
import { Button, GLASS, MODAL_BACKDROP, MODAL_SCROLL, Notice, Segmented, Spinner, cn, useScrollLock } from '@/components/ui';
import {
  IconArmchair,
  IconBackpack,
  IconBag,
  IconBed,
  IconBlocks,
  IconCamera,
  IconChair,
  IconCheck,
  IconChevronLeft,
  IconClose,
  IconCube,
  IconDesk,
  IconFrame,
  IconGear,
  IconGrid,
  IconLamp,
  IconParasol,
  IconPlant,
  IconRug,
  IconSofa,
  IconSparkle,
  IconStorage,
  IconTable,
  IconVase,
} from '@/components/ui/icons';
import { api } from '@/lib/api';
import { apiUserId } from '@/lib/api/user';
import { config } from '@/lib/config';
import { useHouseFurniture, useInventory, useLayouts } from '@/lib/data/hooks';
import { invalidate, useResource } from '@/lib/data/resource';
import { bounds } from '@/lib/geometry/polygon';
import { previewLayout } from '@/lib/layouts';
import { useDebounced, useJobProgress, useLibrary, type Library } from '@/lib/library';
import { roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';
import { CARD, CARD_NAME, CARD_SUB, CardImage, ColourDots, LibraryGrid, SearchBox, credits, designName, matches, reason, useFamilyView, useLibraryAdds } from './CatalogBrowser';
import { OwnRoom } from './OwnRoom';
import { PhotoButtons, TapToPick, preparePhoto } from './PhotoPiece';

type Tab = 'browse' | 'furnish' | 'yours' | 'photo';
type Level = 'essential' | 'full' | 'lavish';
type IconType = typeof IconBag;
type Said = { tone: 'ok' | 'error'; text: string };

// Shopping bag: the library. Backpack: what the family owns (docs/style.md, Tabs).
const TABS: { tab: Tab; label: string; icon: IconType }[] = [
  { tab: 'browse', label: 'Browse', icon: IconBag },
  { tab: 'furnish', label: 'Furnish this room', icon: IconSparkle },
  { tab: 'yours', label: 'Yours', icon: IconBackpack },
  { tab: 'photo', label: 'From a photo', icon: IconCamera },
];

const SECTION_ICON: Record<string, IconType> = {
  sofas: IconSofa,
  armchairs: IconArmchair,
  chairs: IconChair,
  tables: IconTable,
  desks: IconDesk,
  beds: IconBed,
  storage: IconStorage,
  lighting: IconLamp,
  rugs: IconRug,
  wall: IconFrame,
  plants: IconPlant,
  decor: IconVase,
  outdoor: IconParasol,
  kids: IconBlocks,
};
/**
 * The menu's sections as the library lists them, shown until its own list arrives (which then
 * wins, in its order and words): the rail reads as a menu from the first frame, and nothing moves
 * when the counts come in.
 */
const SECTIONS: { key: string; label: string }[] = [
  { key: 'sofas', label: 'Sofas' },
  { key: 'armchairs', label: 'Armchairs' },
  { key: 'chairs', label: 'Chairs & stools' },
  { key: 'tables', label: 'Tables' },
  { key: 'desks', label: 'Desks' },
  { key: 'beds', label: 'Beds' },
  { key: 'storage', label: 'Storage' },
  { key: 'lighting', label: 'Lighting' },
  { key: 'rugs', label: 'Rugs' },
  { key: 'wall', label: 'Wall art & mirrors' },
  { key: 'plants', label: 'Plants' },
  { key: 'decor', label: 'Decor' },
  { key: 'outdoor', label: 'Outdoor' },
  { key: 'kids', label: 'Kids' },
];

const LEVELS: readonly { value: Level; label: string; title: string }[] = [
  { value: 'essential', label: 'Essentials', title: 'Only what the room needs' },
  { value: 'full', label: 'Fully furnished', title: 'A finished room' },
  { value: 'lavish', label: 'Lavish', title: 'Layered, with more of everything' },
];

const FINISHED = new Set(['done', 'failed']);
const count = (n: number) => n.toLocaleString('en-US');
const kind = (i: InventoryItem) => i.item_class.replace(/_/g, ' ');
const size = (i: InventoryItem) => `${i.dims_m.w.toFixed(1)}×${i.dims_m.d.toFixed(1)} m`;
/** The host (the big screen's user) has no member name. */
const SHARED = 'Shared';
const HEADING = 'text-[11px] font-semibold tracking-wider text-faint uppercase';
/** A paper card with a small serif title, as "My room from a photo" (OwnRoom) is drawn. */
const PAPER_CARD = 'rounded-2xl border border-line bg-raised p-3';
const PAPER_TITLE = 'font-display text-[15px] leading-tight font-semibold';

/** A room being furnished by the designer. */
interface FurnishJob {
  job_id: string;
  /** The room's name and the look's, as they were when it started. */
  room: string;
  look: string;
}

/**
 * The furniture menu of the big screen: one large window for everything that brings furniture
 * into the house.
 *
 *   Browse              the library: thousands of real 3D models from open collections, by section,
 *                       collection and search. A tap adds a piece and puts it in the room in view.
 *   Furnish this room   a look to choose; the designer picks the pieces and arranges them.
 *   Yours               the family's pieces that stand in no room: place one, or delete it.
 *   From a photo        the person's own room, or one piece, rebuilt in 3D from a photo.
 *
 * The window stays open while pieces are added, so several can be. It is mounted the first time
 * it opens and then only hidden: the search, the section, the scroll and the checks on added
 * cards are still there the next time. Jobs it started (a room being furnished, a photo being
 * rebuilt) are followed here whether it is open or not; closed, a running one shows as a glass
 * pill over the house.
 */
export function FurnitureMenu({ twin, open, onOpen, onClose, onFurnished, overHouse = true }: {
  twin: PropertyTwin;
  open: boolean;
  onOpen(): void;
  onClose(): void;
  /** The designer has furnished a room: its options are on every screen, for one to be kept. */
  onFurnished(): void;
  /** The house is what shows behind: a running job may float over it. */
  overHouse?: boolean;
}) {
  const pid = twin.property_id;
  const viewRoomId = useSession((s) => s.viewer.room_id);
  const selectedId = useSession((s) => s.viewer.selected_layout_id);
  const dispatch = useSession((s) => s.dispatch);
  const layouts = useLayouts(pid);
  const house = useHouseFurniture(pid);
  const inventory = useInventory();
  const preview = previewLayout(layouts.data, selectedId);
  const room = twin.rooms.find((r) => r.room_id === (viewRoomId ?? preview?.room_id));
  const name = room ? roomName(twin, room.room_id) : null;
  const where = name ? `the ${name.toLowerCase()}` : null;
  const stored = useMemo(() => {
    const inHouse = new Set((house.data ?? []).map((p) => p.item_id));
    return (inventory.data ?? []).filter((i) => !inHouse.has(i.item_id));
  }, [house.data, inventory.data]);

  const [opened, setOpened] = useState(false);
  if (open && !opened) setOpened(true);
  const [tab, setTab] = useState<Tab>('browse');
  const [query, setQuery] = useState('');
  const [ownQuery, setOwnQuery] = useState('');
  const [category, setCategory] = useState<string | null>(null);
  const [source, setSource] = useState<string | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const [photoSaid, setPhotoSaid] = useState<Said | null>(null);
  const [managing, setManaging] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null);
  const search = useRef<HTMLInputElement>(null);

  const typed = query.trim();
  const late = useDebounced(typed);
  // An emptied box applies at once; words wait for a pause in typing.
  const q = typed ? late : '';
  const library = useLibrary({ q, category, source }, opened);

  // How many collections there are pieces from. Counted when nothing narrows the list: a search
  // leaves the collections it finds nothing in at zero.
  const [libraries, setLibraries] = useState<number | null>(null);
  useEffect(() => {
    if (!q && !category && !library.loading && library.sources.length) setLibraries(library.sources.filter((s) => s.count > 0).length);
  }, [q, category, library.loading, library.sources]);
  const collections = libraries ?? library.sources.length;
  const sizeLine =
    library.totalAll === null
      ? library.error
        ? 'The library could not be reached.'
        : 'Loading the library…'
      : library.totalAll === 0
        ? 'The library is empty: it needs the API.'
        : `${count(library.totalAll)} pieces from ${collections} open 3D ${collections === 1 ? 'library' : 'libraries'}`;

  /** Into the room, where the server sees fit for what it is: a rug under the seating, a lamp on a free table, a picture on the wall. */
  const putInRoom = async (item: InventoryItem, into: Room) => {
    const b = bounds(into.polygon);
    await api().placePiece(pid, item.item_id, { x: (b.minX + b.maxX) / 2, z: (b.minZ + b.maxZ) / 2, room_id: into.room_id, floor_id: into.floor_id, snap: true, auto: true });
    // What is really in the house has changed: a layout being tried makes way, or the piece would not show.
    if (preview) dispatch('layout.select', { layout_id: null });
  };

  /** A new piece goes into the room in view, or stays in storage when there is none (or no spot for it). Every screen is told. */
  const settle = async (item: InventoryItem, label: string, say: (said: Said) => void) => {
    if (!room) {
      say({ tone: 'ok', text: `${label} is in storage (Yours). Walk into a room to place it.` });
    } else {
      try {
        await putInRoom(item, room);
        say({ tone: 'ok', text: `${label} is in ${where}.` });
      } catch (err) {
        say({ tone: 'error', text: `${label} is in storage (Yours), not in ${where}: ${reason(err)}` });
      }
    }
    furnitureChanged(pid);
  };

  const placeStored = async (item: InventoryItem) => {
    if (!room) throw new Error('Walk into a room first, then place the piece.');
    await putInRoom(item, room);
    furnitureChanged(pid);
  };
  const deleteForGood = async (item: InventoryItem) => {
    await api().deleteInventoryItem(item.item_id);
    furnitureChanged(pid);
  };

  // A photo of one piece: it is cut out, built in 3D and put in the room. A photo of the person's
  // own room: the server sets the room up itself. Followed here, so closing the window loses nothing.
  const photos = useObjectUpload(
    (itemId) => {
      void api().getInventoryItem(itemId).then((item) => settle(item, item.label, setPhotoSaid), (err: unknown) => setPhotoSaid({ tone: 'error', text: reason(err) }));
    },
    () => {
      if (preview) dispatch('layout.select', { layout_id: null });
      furnitureChanged(pid);
    },
  );

  // The designer at work on a room.
  const [job, setJob] = useState<FurnishJob | null>(null);
  const progress = useJobProgress(job?.job_id ?? null);
  const furnished = useRef(onFurnished);
  furnished.current = onFurnished;
  const done = !!job && progress?.status === 'done';
  useEffect(() => {
    if (!done) return;
    // The server has added the pieces and shows the options on every screen itself. Loaded again
    // here too, for a session that dropped for a moment and did not hear it.
    invalidate('inventory');
    invalidate(`furniture:${pid}`);
    invalidate('layouts:');
    invalidate('layout:');
    setJob(null);
    furnished.current();
  }, [done, pid]);

  const furnish = async (look: string, lookName: string, level: Level): Promise<string | null> => {
    if (!room || !name) return 'Walk into a room first, then choose a look.';
    try {
      const ref = await api().furnishRoom(pid, { room_id: room.room_id, look, level });
      setJob({ job_id: ref.job_id, room: name, look: lookName });
      return null;
    } catch (err) {
      return reason(err);
    }
  };

  const me = apiUserId();
  const mayDelete = (i: InventoryItem) => i.user_id === me || me === config.userId;
  const canManage = stored.some(mayDelete);
  useEffect(() => {
    if (!canManage) setManaging(false);
  }, [canManage]);
  useEffect(() => setConfirming(null), [tab, managing]);

  const boxed = tab === 'yours' ? ownQuery : query;
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (confirming) setConfirming(null);
      // In the search box, Escape empties it first.
      else if (document.activeElement === search.current && boxed) (tab === 'yours' ? setOwnQuery : setQuery)('');
      else onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose, confirming, boxed, tab]);
  useScrollLock(open);
  // The window opens ready to be typed into.
  useEffect(() => {
    if (open) search.current?.focus();
  }, [open]);

  // Closed, with something still being made: a glass pill over the house says so and opens the window again.
  const building = photos.rows.find((r) => !FINISHED.has(r.progress?.status ?? 'queued'));
  const failed = progress?.status === 'failed';
  const pill: { tab: Tab; text: string; pct: number | null } | null = job
    ? { tab: 'furnish', text: failed ? `The ${job.room.toLowerCase()} could not be furnished` : `Furnishing the ${job.room.toLowerCase()}`, pct: failed ? null : (progress?.pct ?? 0) }
    : building
      ? { tab: 'photo', text: building.room ? 'Rebuilding your room from the photo' : 'Building a piece from your photo', pct: building.progress?.pct ?? 0 }
      : null;

  return (
    <>
      {!open && overHouse && pill && (
        <div className="pointer-events-none absolute inset-x-0 top-3 z-20 flex justify-center px-4">
          <button
            type="button"
            onClick={() => {
              setTab(pill.tab);
              onOpen();
            }}
            data-testid="furniture-progress"
            className={cn('pointer-events-auto inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm hover:bg-black/50', GLASS)}
          >
            {pill.pct !== null && <i aria-hidden className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/30 border-t-white" />}
            <span className={pill.pct === null ? 'text-coral' : 'font-semibold'}>{pill.text}</span>
            {pill.pct !== null && <span className="text-white/75 tabular-nums">{Math.round(pill.pct)}%</span>}
          </button>
        </div>
      )}
      {opened &&
        createPortal(
          // Hidden, not unmounted, while closed (see above). The inline style wins over the backdrop's `flex`.
          <div className={MODAL_BACKDROP} style={open ? undefined : { display: 'none' }} onClick={(e) => e.target === e.currentTarget && onClose()}>
            <section role="dialog" aria-modal aria-label="Furniture" data-testid="furniture-menu" data-tab={tab}
              className="flex h-[min(92dvh,58rem)] w-full max-w-[82rem] flex-col overflow-hidden rounded-3xl border border-line bg-surface shadow-float">
              <header className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-3 px-6 pt-5 pb-4">
                <div className="min-w-0">
                  <h2 className="font-display text-2xl leading-tight font-semibold">Furniture</h2>
                  <p className="text-sm text-muted tabular-nums" data-testid="library-size">{sizeLine}</p>
                </div>
                <div className="min-w-[14rem] flex-1">
                  <SearchBox
                    large
                    inputRef={search}
                    value={boxed}
                    onChange={(v) => {
                      if (tab === 'yours') return setOwnQuery(v);
                      // A search is always of the whole library: it shows under Browse.
                      setQuery(v);
                      setTab('browse');
                    }}
                    placeholder={tab === 'yours' ? 'Search your furniture' : library.totalAll ? `Search all ${count(library.totalAll)} pieces: sofa, oak, green, mid-century…` : 'Search the library: sofa, oak, green, mid-century…'}
                  />
                </div>
                <p className="flex h-10 shrink-0 items-center gap-1.5 rounded-full bg-ink px-4 text-sm" data-testid="adding-to">
                  <span className="text-muted">Adding to:</span>
                  <span className="font-semibold">{name ?? 'storage'}</span>
                </p>
                <button type="button" onClick={onClose} aria-label="Close the furniture menu" title="Close"
                  className="grid h-11 w-11 shrink-0 place-items-center rounded-full border border-line bg-raised transition-colors hover:border-text">
                  <IconClose width={18} height={18} />
                </button>
              </header>

              <div className="flex shrink-0 items-center gap-3 px-6 pb-4">
                <div role="tablist" aria-label="Where from" className="flex gap-1 rounded-full bg-ink p-1">
                  {TABS.map(({ tab: t, label, icon: Icon }) => (
                    <button key={t} type="button" role="tab" aria-selected={t === tab} aria-label={label} title={label} data-tab={t} onClick={() => setTab(t)}
                      className={cn(
                        'flex h-11 items-center gap-2 rounded-full px-5 font-display text-base font-semibold whitespace-nowrap transition-colors',
                        t === tab ? 'bg-raised text-text shadow-[0_2px_0_var(--color-line)]' : 'text-muted hover:text-text',
                      )}>
                      <Icon width={19} height={19} className="shrink-0" />
                      {/* The open tab always says what it is; the others do where there is room. */}
                      <span className={t === tab ? undefined : 'hidden lg:inline'}>{label}</span>
                      {t === 'yours' && stored.length > 0 && <span className="rounded-full bg-ink px-1.5 text-xs leading-5 font-semibold text-muted tabular-nums">{stored.length}</span>}
                      {t === 'furnish' && job && !failed && <i aria-hidden className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-main" />}
                    </button>
                  ))}
                </div>
                {tab === 'yours' && canManage && (
                  <button type="button" aria-pressed={managing} onClick={() => setManaging((m) => !m)} data-testid="manage-toggle"
                    className={cn('ml-auto flex h-10 items-center gap-1.5 rounded-full border px-4 text-sm font-semibold', managing ? 'border-leaf-deep/40 bg-leaf/15 text-leaf-deep' : 'border-line bg-raised text-text hover:border-text')}>
                    {managing ? <IconCheck width={15} height={15} /> : <IconGear width={15} height={15} />}
                    {managing ? 'Done' : 'Manage'}
                  </button>
                )}
              </div>

              <div className={cn('min-h-0 flex-1 border-t border-line', tab === 'browse' ? 'flex' : 'hidden')} role="tabpanel" aria-label="Browse">
                <Browse
                  library={library}
                  q={q}
                  category={category}
                  source={source}
                  onCategory={setCategory}
                  onSource={setSource}
                  where={where}
                  said={said}
                  onAdd={() => setSaid(null)}
                  onAdded={(item, piece) => settle(item, piece.label, setSaid)}
                />
              </div>
              <div className={cn('min-h-0 flex-1 flex-col border-t border-line', tab === 'furnish' ? 'flex' : 'hidden')} role="tabpanel" aria-label="Furnish this room">
                <Furnish room={name} job={job} progress={progress} onStart={furnish} onReset={() => setJob(null)} />
              </div>
              <div className={cn('min-h-0 flex-1 flex-col border-t border-line', tab === 'yours' ? 'flex' : 'hidden')} role="tabpanel" aria-label="Yours">
                <Yours
                  stored={stored}
                  loaded={!!inventory.data && !!house.data}
                  query={ownQuery}
                  where={where}
                  managing={managing}
                  confirming={confirming}
                  onConfirming={setConfirming}
                  mayDelete={mayDelete}
                  onPlace={placeStored}
                  onDelete={deleteForGood}
                />
              </div>
              <div className={cn('min-h-0 flex-1 flex-col border-t border-line', tab === 'photo' ? 'flex' : 'hidden')} role="tabpanel" aria-label="From a photo">
                <Photo twin={twin} room={room} where={where} photos={photos} said={photoSaid} />
              </div>
            </section>
          </div>,
          document.body,
        )}
    </>
  );
}

/** One row of the rail: a section of the library, with how many pieces the search finds in it. */
function RailRow({ icon: Icon, label, n, on, onClick }: { icon: IconType; label: string; /** Null: not counted yet. */ n: number | null; on: boolean; onClick(): void }) {
  return (
    <li>
      <button type="button" aria-pressed={on} onClick={onClick}
        className={cn(
          'flex h-10 w-full items-center gap-3 rounded-xl px-3 text-left text-[15px] font-semibold transition-colors',
          on ? 'bg-raised text-text shadow-[0_2px_0_var(--color-line)]' : 'text-muted hover:text-text',
          n === 0 && !on && 'opacity-45',
        )}>
        <Icon width={20} height={20} className="shrink-0" />
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {/* A fixed width: the names do not move when the counts arrive or change with the search. */}
        <span className="w-12 shrink-0 text-right text-[13px] font-normal text-muted tabular-nums">{n === null ? '' : count(n)}</span>
      </button>
    </li>
  );
}

/**
 * The library. On the left the menu itself: every section with its count, and under it the
 * collections the pieces come from. On the right the pieces, a page at a time as the grid is
 * scrolled. A design that comes in several colours opens them in place of the grid, with a way back.
 */
function Browse({ library, q, category, source, onCategory, onSource, where, said, onAdd, onAdded }: {
  library: Library;
  /** The search as it was sent (after the pause in typing). */
  q: string;
  category: string | null;
  source: string | null;
  onCategory(key: string | null): void;
  onSource(key: string | null): void;
  /** "the living room", or null when the user is in no room. */
  where: string | null;
  /** What became of the last piece added. */
  said: Said | null;
  onAdd(): void;
  onAdded(item: InventoryItem, piece: LibraryPiece): Promise<void>;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const view = useFamilyView(() => scroller.current);
  const colours = useLibrary({ family: view.family?.family }, !!view.family);
  const adds = useLibraryAdds(onAdded);
  const shown = view.family ? colours : library;

  // A new search is a new list: out of a design's colours, back to the top.
  useEffect(() => {
    view.close(true);
    if (scroller.current) scroller.current.scrollTop = 0;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  /** A press on the rail always lands at the top of that list, also from inside a design's colours. */
  const leave = () => {
    view.close(true);
    if (scroller.current) scroller.current.scrollTop = 0;
  };
  const counted = library.categories.length > 0;
  const sections = counted ? library.categories.map((c) => ({ key: c.key, label: c.label, n: c.count as number | null })) : SECTIONS.map((s) => ({ ...s, n: null }));
  const section = sections.find((s) => s.key === category)?.label;
  const collection = library.sources.find((s) => s.key === source)?.label;
  const first = shown.loading && shown.items.length === 0;
  const nothing = !shown.loading && !shown.error && shown.items.length === 0;
  const lines = credits(shown.items);
  const narrowed = `${section ? ` in ${section}` : ''}${collection ? ` from ${collection}` : ''}`;

  return (
    <>
      <nav aria-label="Sections of the library" className="flex w-64 shrink-0 flex-col border-r border-line" data-testid="library-rail">
        <div className={cn(MODAL_SCROLL, 'px-3 pt-3 pb-3')}>
          <ul className="space-y-0.5 rounded-2xl bg-ink p-1.5">
            <RailRow icon={IconGrid} label="All furniture" n={counted ? library.categories.reduce((n, c) => n + c.count, 0) : null} on={!category}
              onClick={() => {
                leave();
                onCategory(null);
              }}
            />
            {sections.map((s) => (
              <RailRow
                key={s.key}
                icon={SECTION_ICON[s.key] ?? IconCube}
                label={s.label}
                n={s.n}
                on={category === s.key}
                onClick={() => {
                  leave();
                  onCategory(s.key);
                }}
              />
            ))}
          </ul>
          {library.sources.length > 0 && (
            <>
              <h3 className={cn(HEADING, 'mt-4 mb-1 px-3')}>Collections</h3>
              <ul className="space-y-0.5" data-testid="library-sources">
                {[{ key: null as string | null, label: 'All collections', count: library.sources.reduce((n, s) => n + s.count, 0) }, ...library.sources].map((s) => (
                  <li key={s.key ?? 'all'}>
                    <button
                      type="button"
                      aria-pressed={source === s.key}
                      onClick={() => {
                        leave();
                        onSource(s.key);
                      }}
                      className={cn('flex h-8 w-full items-center gap-2.5 rounded-lg px-3 text-left text-[13px] transition-colors', source === s.key ? 'bg-ink font-semibold text-text' : 'text-muted hover:text-text', s.count === 0 && source !== s.key && 'opacity-45')}>
                      <i aria-hidden className={cn('h-2 w-2 shrink-0 rounded-full border', source === s.key ? 'border-main bg-main' : 'border-line-strong')} />
                      <span className="min-w-0 flex-1 truncate">{s.label}</span>
                      <span className="shrink-0 text-xs font-normal text-muted tabular-nums">{count(s.count)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
        {/* Whose models these are: always in view, as the collections' licences ask. */}
        <p className="min-h-14 shrink-0 border-t border-line px-4 py-2.5 text-[11px] leading-4 text-faint" data-testid="library-credit">
          {lines.length ? `3D models: ${lines.join('; ')}.` : 'Real pieces at real size, from open 3D collections.'}
        </p>
      </nav>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-3 px-5">
          {view.family ? (
            <>
              <button type="button" onClick={() => view.close()} data-testid="family-back"
                className="inline-flex h-9 shrink-0 items-center gap-1 rounded-full border border-line bg-raised pr-4 pl-2.5 text-sm font-semibold transition-colors hover:border-text">
                <IconChevronLeft width={16} height={16} /> Back
              </button>
              <p className="min-w-0 truncate text-[15px] font-semibold">
                {designName(view.family)}
                <span className="font-normal text-muted tabular-nums"> · {colours.loading ? view.family.variants : colours.total} colours</span>
              </p>
            </>
          ) : (
            <p className="min-w-0 truncate text-[15px]" aria-live="polite">
              <span className="font-semibold">{q ? `“${q}”` : (section ?? 'All furniture')}</span>
              {!library.error && (
                <span className="text-muted tabular-nums">
                  {' '}
                  {/* Designs, not pieces: a design in five colours is one card here, and five in the size of the library above. */}
                  · {first ? 'Loading…' : `${count(library.total)} ${library.total === 1 ? 'design' : 'designs'}${q ? narrowed : collection ? ` from ${collection}` : ''}`}
                </span>
              )}
            </p>
          )}
        </div>

        <div ref={scroller} className={cn(MODAL_SCROLL, 'px-5 pb-5')} data-testid="library-grid">
          {shown.error ? (
            <div className="grid h-full place-items-center">
              <Notice tone="error" title="The library could not be loaded" action={<Button size="sm" onClick={shown.retry}>Try again</Button>}>
                {shown.error}
              </Notice>
            </div>
          ) : nothing ? (
            <div className="grid h-full place-items-center text-center">
              <div className="max-w-md space-y-3">
                <p className="font-display text-lg font-semibold">
                  {view.family
                    ? 'This design has no other colours now.'
                    : library.totalAll === 0
                      ? 'The library is empty.'
                      : q
                        ? `Nothing in the library matches “${q}”${narrowed}.`
                        : `Nothing${narrowed || ' in the library'} yet.`}
                </p>
                {!view.family && library.totalAll === 0 && <p className="text-sm text-muted">It needs the API (fixture mode has none).</p>}
                {!view.family && library.totalAll !== 0 && q && <p className="text-sm text-muted">Try fewer words, or another word for it: a kind, a colour, a material, a look.</p>}
                {!view.family && (category || source) && (
                  <Button
                    size="sm"
                    onClick={() => {
                      onCategory(null);
                      onSource(null);
                    }}
                  >
                    {q ? 'Search all furniture' : 'Show all furniture'}
                  </Button>
                )}
              </div>
            </div>
          ) : (
            <LibraryGrid
              library={shown}
              columns="grid-cols-[repeat(auto-fill,minmax(11.5rem,1fr))] gap-3"
              large
              stateOf={adds.stateOf}
              onAdd={(piece) => {
                onAdd();
                adds.add(piece);
              }}
              onFamily={view.family ? undefined : view.open}
              where={where ?? 'storage'}
              skeletons={view.family ? Math.min(view.family.variants ?? 5, 15) : 15}
            />
          )}
        </div>

        {/* One line, always there: what is happening to the piece just tapped, or where pieces go. */}
        <div className="flex min-h-12 shrink-0 items-center border-t border-line px-5 py-2 text-sm" data-testid="furniture-status">
          {adds.current ? (
            <p role="status" className="flex items-center gap-2.5">
              <Spinner />
              <span>
                Getting the 3D model of {adds.current.label}
                {adds.waiting > 1 ? `, then ${adds.waiting - 1} more` : ''}. The first time a piece is used, this can take 20 seconds.
              </span>
            </p>
          ) : adds.error ? (
            <p role="alert" className="text-danger">{adds.error}</p>
          ) : said ? (
            <p role="status" className={said.tone === 'error' ? 'text-danger' : 'font-semibold text-leaf-deep'}>{said.text}</p>
          ) : where ? (
            <p className="text-muted">Tap a piece to put it in {where}. The menu stays open, so you can add several.</p>
          ) : (
            <p className="text-muted">You are in no room, so pieces go to storage (Yours). Walk into a room to have them placed.</p>
          )}
        </div>
      </div>
    </>
  );
}

/**
 * A whole room at once: choose a look (or say one), and how full the room should be. The designer
 * picks the pieces from the library, adds them and arranges them; its progress shows here, and
 * when it is done the window closes on the room with the options to keep.
 */
function Furnish({ room, job, progress, onStart, onReset }: {
  /** The room in view, by name; null when there is none. */
  room: string | null;
  job: FurnishJob | null;
  progress: JobProgressPayload | undefined;
  /** Starts the designer. Resolves to why it could not start, or null. */
  onStart(look: string, lookName: string, level: Level): Promise<string | null>;
  /** Back to the looks after a failure. */
  onReset(): void;
}) {
  const looks = useResource('library:looks', () => api().listLooks());
  const [level, setLevel] = useState<Level>('full');
  const [words, setWords] = useState('');
  const [starting, setStarting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const the = room ? `the ${room.toLowerCase()}` : 'the room';

  const start = async (look: string, lookName: string) => {
    setStarting(look);
    setError(null);
    const refused = await onStart(look, lookName, level);
    setStarting(null);
    if (refused) setError(refused);
    else setWords('');
  };
  const ownWords = (e: FormEvent) => {
    e.preventDefault();
    const look = words.trim();
    if (look && room && !starting) void start(look, `“${look}”`);
  };

  if (job) {
    const status = progress?.status ?? 'queued';
    const pct = status === 'done' ? 100 : (progress?.pct ?? 0);
    const why = progress?.result_ref ?? progress?.note;
    return (
      <div className={cn(MODAL_SCROLL, 'grid place-items-center px-6 py-6')}>
        <div className="w-full max-w-xl rounded-2xl border border-line bg-raised p-6" data-testid="furnish-progress" data-status={status}>
          <p className={HEADING}>{job.look}</p>
          <h3 className="mt-1 font-display text-xl font-semibold">
            {status === 'failed' ? `The ${job.room.toLowerCase()} could not be furnished` : status === 'done' ? `The ${job.room.toLowerCase()} is furnished` : `Furnishing the ${job.room.toLowerCase()}`}
          </h3>
          <div className="mt-4 h-2 overflow-hidden rounded-full bg-line">
            <div className={cn('h-full rounded-full transition-[width] duration-500', status === 'failed' ? 'bg-danger' : 'bg-sun')} style={{ width: `${pct}%` }} />
          </div>
          {status === 'failed' ? (
            <>
              <p role="alert" className="mt-3 text-sm text-danger">{why ? `${why.replace(/\.$/, '')}.` : 'The designer could not finish this room.'}</p>
              <Button className="mt-4" onClick={onReset}>Choose a look again</Button>
            </>
          ) : (
            <>
              <p role="status" className="mt-3 flex items-center gap-2.5 text-[15px]">
                <Spinner />
                <span>{progress?.note ?? (status === 'queued' ? 'Waiting for the designer…' : 'Choosing the pieces…')}</span>
                <span className="ml-auto text-sm text-muted tabular-nums">{Math.round(pct)}%</span>
              </p>
              <p className="mt-2 text-sm text-muted">This takes from 20 seconds to 2 minutes. When it is done this window closes, and the room shows the options to keep. You can close it now and look around: it carries on.</p>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={cn(MODAL_SCROLL, 'px-6 pt-5 pb-6')} data-testid="furnish-looks">
      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        <div className="min-w-0 flex-1 basis-80">
          <h3 className="font-display text-xl font-semibold">{room ? `Furnish ${the}` : 'Furnish a room'}</h3>
          <p className="mt-0.5 text-sm text-muted">Choose a look. The designer picks pieces from the library, arranges them, and shows you options. The room changes only when you keep one.</p>
        </div>
        <div>
          <p className={cn(HEADING, 'mb-1.5')}>How full</p>
          <Segmented label="How full the room should be" value={level} options={LEVELS} onChange={(v) => setLevel(v)} />
        </div>
      </div>

      {!room && (
        <div className="mt-4">
          <Notice tone="warn" title="No room in view">
            Walk into a room, or pick one in the room list, then choose a look for it.
          </Notice>
        </div>
      )}
      {error && <p role="alert" className="mt-4 text-sm text-danger">{error}</p>}

      <form onSubmit={ownWords} className="mt-4 flex items-center gap-2 rounded-full border border-line bg-raised py-1.5 pr-1.5 pl-5 focus-within:border-line-strong">
        <IconSparkle width={18} height={18} className="shrink-0 text-muted" />
        <input value={words} onChange={(e) => setWords(e.target.value)} aria-label="A look in your own words" enterKeyHint="go"
          placeholder="Or say it in your own words: warm wood, a green velvet sofa, lots of plants…"
          className="h-10 min-w-0 flex-1 bg-transparent text-[15px] placeholder:text-faint focus:outline-none" />
        <Button type="submit" variant="primary" disabled={!room || !words.trim() || starting !== null}>
          {starting !== null && starting === words.trim() ? 'Starting…' : 'Furnish'}
        </Button>
      </form>

      {looks.error ? (
        <p role="alert" className="mt-6 text-sm text-danger">The looks could not be loaded: {looks.error.message}</p>
      ) : !looks.data ? (
        <ul className="mt-5 grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-4" aria-busy>
          {Array.from({ length: 8 }, (_, i) => (
            <li key={i} aria-hidden className="animate-pulse rounded-2xl border border-line bg-raised p-2.5">
              <span className="block aspect-[4/3] w-full rounded-xl bg-ink" />
              <span className="mt-3 block h-5 w-1/2 rounded-full bg-ink" />
              <span className="mt-2 mb-1 block h-9 w-full rounded-lg bg-ink" />
            </li>
          ))}
        </ul>
      ) : looks.data.length === 0 ? (
        <p className="mt-6 text-sm text-muted">No looks are listed here: they come from the API (fixture mode has none).</p>
      ) : (
        <ul className="mt-5 grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-4">
          {looks.data.map((look) => {
            const pictures = (look.thumbnail_urls ?? []).slice(0, 4);
            return (
              <li key={look.key}>
                <button type="button" disabled={!room || starting !== null} onClick={() => void start(look.key, look.name)} aria-label={`Furnish ${the} in the ${look.name} look`} aria-busy={starting === look.key} data-look={look.key}
                  className="group flex h-full w-full flex-col rounded-2xl border border-line bg-raised p-2.5 text-left transition-[border-color,transform] hover:border-text active:scale-[0.98] disabled:cursor-default disabled:opacity-60 disabled:hover:border-line disabled:active:scale-100">
                  {/* Four of the look's pieces. Always four boxes, so every card is the same shape whatever loads. */}
                  <span className="relative grid aspect-[4/3] w-full grid-cols-2 grid-rows-2 gap-px overflow-hidden rounded-xl bg-line">
                    {Array.from({ length: 4 }, (_, i) =>
                      pictures[i] ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img key={i} src={pictures[i]} alt="" loading="lazy" className="h-full min-h-0 w-full bg-surface object-contain" />
                      ) : (
                        <span key={i} className="bg-surface" />
                      ),
                    )}
                    {starting === look.key && <span className="absolute inset-0 grid place-items-center bg-raised/70 text-sm font-semibold">Starting…</span>}
                  </span>
                  <span className="mt-2.5 flex items-center gap-2 px-1">
                    <span className="min-w-0 flex-1 truncate font-display text-lg leading-tight font-semibold">{look.name}</span>
                    <ColourDots palette={look.palette} max={5} large />
                  </span>
                  <span className="mt-1 line-clamp-2 min-h-10 px-1 pb-0.5 text-sm leading-5 text-muted">{look.blurb}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * The family's pieces that stand in no room, as cards: a tap puts one in the room in view.
 * Manage turns the cards into delete buttons, each with its own confirmation (docs/style.md,
 * Furniture cards); the big screen is the host's, so every piece may be deleted here.
 */
function Yours({ stored, loaded, query, where, managing, confirming, onConfirming, mayDelete, onPlace, onDelete }: {
  stored: InventoryItem[];
  /** The furniture and what stands in the house are both known. */
  loaded: boolean;
  query: string;
  where: string | null;
  managing: boolean;
  /** The piece whose delete is waiting for a yes. */
  confirming: string | null;
  onConfirming(itemId: string | null): void;
  mayDelete(item: InventoryItem): boolean;
  onPlace(item: InventoryItem): Promise<void>;
  onDelete(item: InventoryItem): Promise<void>;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [said, setSaid] = useState<Said | null>(null);
  const groups = useMemo(() => {
    const by = new Map<string, InventoryItem[]>();
    for (const i of stored) {
      if (!matches(query, i.label, kind(i), i.owner_name ?? '')) continue;
      const who = i.owner_name ?? SHARED;
      by.set(who, [...(by.get(who) ?? []), i]);
    }
    return [...by];
  }, [stored, query]);
  // One family, one heap: the owner's name only says something when there are several.
  const headed = groups.length > 1 || (groups[0] !== undefined && groups[0][0] !== SHARED);

  const run = async (item: InventoryItem, fn: (item: InventoryItem) => Promise<void>, after: string) => {
    setBusy(item.item_id);
    setSaid(null);
    try {
      await fn(item);
      onConfirming(null);
      setSaid({ tone: 'ok', text: after });
    } catch (err) {
      setSaid({ tone: 'error', text: reason(err) });
    } finally {
      setBusy(null);
    }
  };

  const card = (i: InventoryItem) => {
    const sub = `${kind(i)} · ${size(i)}`;
    const title = (
      <span className={CARD_NAME}>
        {i.label}
        {i.tag && <span className="ml-1.5 font-mono text-[11px] font-normal text-muted">#{i.tag}</span>}
      </span>
    );
    if (confirming === i.item_id) {
      return (
        <div role="group" aria-label={`Delete ${i.label} for good?`} className="flex h-full w-full flex-col rounded-xl border border-danger/50 bg-raised p-1.5">
          <CardImage src={i.source_image_url} state="delete" />
          <span className={CARD_NAME}>Delete {i.label}?</span>
          <span className={CARD_SUB}>It leaves every house.</span>
          <div className="mt-1.5 flex gap-1.5">
            <button type="button" disabled={busy !== null} onClick={() => void run(i, onDelete, `${i.label} is deleted.`)} className="flex h-8 flex-1 items-center justify-center rounded-full bg-danger text-xs font-semibold text-white disabled:opacity-60">
              {busy === i.item_id ? <Spinner /> : 'Delete'}
            </button>
            <button type="button" disabled={busy !== null} onClick={() => onConfirming(null)} className="h-8 flex-1 rounded-full border border-line bg-raised text-xs font-semibold">
              Keep
            </button>
          </div>
        </div>
      );
    }
    if (managing) {
      return (
        <button type="button" disabled={busy !== null || !mayDelete(i)} onClick={() => onConfirming(i.item_id)}
          aria-label={mayDelete(i) ? `Delete ${i.label}` : `${i.label}: only ${i.owner_name ?? 'its owner'} can delete it`}
          title={mayDelete(i) ? undefined : `Only ${i.owner_name ?? 'its owner'} can delete it`} className={cn(CARD, 'h-full', !mayDelete(i) && 'opacity-45')}>
          <CardImage src={i.source_image_url} state={mayDelete(i) ? 'delete' : 'idle'} />
          {title}
          <span className={CARD_SUB}>{sub}</span>
        </button>
      );
    }
    return (
      <button type="button" disabled={busy !== null || !where} onClick={() => void run(i, onPlace, `${i.label} is in ${where}.`)} aria-label={`Put ${i.label} in ${where ?? 'a room'}`} aria-busy={busy === i.item_id} className={cn(CARD, 'h-full')}>
        <CardImage src={i.source_image_url} state={busy === i.item_id ? 'adding' : 'idle'} />
        {title}
        <span className={CARD_SUB}>{sub}</span>
      </button>
    );
  };

  return (
    <div className={cn(MODAL_SCROLL, 'px-6 pt-4 pb-6')} data-testid="furniture-yours">
      <p className="flex min-h-6 items-center text-sm" role={said ? 'status' : undefined}>
        {said ? (
          <span className={said.tone === 'error' ? 'text-danger' : 'font-semibold text-leaf-deep'}>{said.text}</span>
        ) : stored.length > 0 ? (
          <span className="text-muted">
            {managing
              ? 'Tap a piece to delete it for good. A piece standing in a room comes out of it first (Arrange).'
              : where
                ? `These stand in no room. Tap one to put it in ${where}.`
                : 'These stand in no room. Walk into a room, then tap one to put it there.'}
          </span>
        ) : null}
      </p>
      {!loaded ? (
        <p className="mt-6 flex justify-center">
          <Spinner label="Loading your furniture…" />
        </p>
      ) : stored.length === 0 ? (
        <p className="mx-auto mt-10 max-w-md text-center text-[15px] text-muted">Nothing is in storage: every piece the family owns stands in a room. Add some from Browse, or from a photo.</p>
      ) : groups.length === 0 ? (
        <p className="mx-auto mt-10 max-w-md text-center text-[15px] text-muted">None of your furniture matches “{query.trim()}”.</p>
      ) : (
        groups.map(([who, list]) => (
          <section key={who} aria-label={who === SHARED ? 'Shared furniture' : `${who}'s furniture`} className="mt-3">
            {headed && <h3 className={cn(HEADING, 'mb-1.5')}>{who} · {list.length}</h3>}
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(10.5rem,1fr))] gap-3">
              {list.map((i) => (
                <li key={i.item_id}>{card(i)}</li>
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}

/**
 * Furniture from a photo: the room the person lives in now, rebuilt and set up in the room in
 * view (it replaces what stands there, so it asks first), or one piece tapped in a photo. The
 * jobs are the menu's own, so their progress is still here after the window was closed.
 */
function Photo({ twin, room, where, photos, said }: {
  twin: PropertyTwin;
  room: Room | undefined;
  where: string | null;
  photos: ReturnType<typeof useObjectUpload>;
  /** What became of the last piece built from a photo. */
  said: Said | null;
}) {
  const [photo, setPhoto] = useState<File | null>(null);
  const send = async (at: PhotoPoint | undefined) => {
    if (!photo) return;
    if (await photos.upload(photo, at, at ? 'The piece you tapped' : 'Every piece in the photo')) setPhoto(null);
  };

  return (
    <div className={cn(MODAL_SCROLL, 'px-6 pt-5 pb-6')} data-testid="furniture-photo">
      <div className="grid items-start gap-5 lg:grid-cols-2">
        {room ? (
          <OwnRoom roomName={roomName(twin, room.room_id)} busy={photos.busy} onSend={(file) => photos.uploadRoom(file, twin.property_id, room.room_id)} />
        ) : (
          <section className={PAPER_CARD} aria-label="My room from a photo">
            <h3 className={PAPER_TITLE}>My room from a photo</h3>
            <p className="mt-1 text-[13px] leading-snug text-muted">One photo of the room you live in now, rebuilt in 3D and set up in a room of this house. Walk into the room it should go in first.</p>
          </section>
        )}
        <section className={PAPER_CARD} aria-label="One piece from a photo">
          <h3 className={PAPER_TITLE}>One piece from a photo</h3>
          {photo ? (
            <div className="mt-2">
              <TapToPick file={photo} busy={photos.busy} onSubmit={(at) => void send(at)} onCancel={() => setPhoto(null)} />
            </div>
          ) : (
            <>
              <p className="mt-1 text-[13px] leading-snug text-muted">
                A photo of a piece, or a screenshot of a product page. Tap the piece in it: it is cut out, built in 3D and {where ? `put in ${where}` : 'kept in storage'}.
              </p>
              <div className="mt-2">
                <PhotoButtons disabled={photos.busy} onPicked={(f) => void preparePhoto(f).then(setPhoto)} />
              </div>
            </>
          )}
        </section>
      </div>
      {photos.error && <p role="alert" className="mt-4 text-sm text-danger">{photos.error}</p>}
      {said && <p role="status" className={cn('mt-4 text-sm', said.tone === 'error' ? 'text-danger' : 'font-semibold text-leaf-deep')}>{said.text}</p>}
      {photos.rows.length > 0 && (
        <div className="mt-4 max-w-2xl">
          <JobRows rows={photos.rows} dismiss={photos.dismiss} />
        </div>
      )}
    </div>
  );
}
