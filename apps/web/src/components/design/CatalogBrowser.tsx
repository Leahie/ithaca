'use client';

import type { InventoryItem, LibraryPiece } from '@ithaca/schema';
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type Ref } from 'react';
import { Spinner, cn } from '@/components/ui';
import { IconChevronLeft, IconChevronRight, IconSearch, IconTrash } from '@/components/ui/icons';
import { ApiError, api } from '@/lib/api';
import { invalidate } from '@/lib/data/resource';
import { useDebounced, useLibrary, type Library } from '@/lib/library';

/** A furniture card is itself the button: tapping a piece adds it. See docs/style.md. */
export const CARD =
  'group flex w-full min-w-0 flex-col rounded-xl border border-line bg-raised p-1.5 text-left transition-[border-color,transform] hover:border-text active:scale-[0.97] disabled:cursor-default disabled:active:scale-100';
/** The card's name and the line under it: one line each, cut short on a narrow phone. */
export const CARD_NAME = 'mt-1 truncate text-[13px] font-semibold leading-tight';
export const CARD_SUB = 'truncate text-[11px] tabular-nums text-muted';
/** The same two lines a size up, for the big screen's furniture menu (read from across a room). */
const CARD_NAME_LARGE = 'mt-1.5 truncate text-[15px] font-semibold leading-tight';
const CARD_SUB_LARGE = 'truncate text-xs tabular-nums text-muted';

/** The piece's picture, with a + in the corner that turns into "Adding…" and then a check; a trash can while managing. */
export function CardImage({ src, state }: { src: string; state: 'idle' | 'adding' | 'added' | 'delete' }) {
  return (
    <span className="relative block">
      {/* A small remote thumbnail: next/image would need the host configured. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={src} alt="" loading="lazy" className={`aspect-square w-full rounded-lg bg-surface object-contain ${state === 'adding' ? 'opacity-50' : ''}`} />
      {state === 'adding' ? (
        <span className="absolute inset-0 grid place-items-center text-xs font-semibold">Adding…</span>
      ) : state === 'delete' ? (
        <span aria-hidden className="absolute top-1.5 right-1.5 grid h-6 w-6 place-items-center rounded-full bg-danger text-white">
          <IconTrash width={13} height={13} />
        </span>
      ) : (
        <span aria-hidden className={`absolute top-1.5 right-1.5 grid h-6 w-6 place-items-center rounded-full text-sm font-semibold ${state === 'added' ? 'bg-leaf text-main' : 'bg-main text-on-main opacity-80 group-hover:opacity-100'}`}>
          {state === 'added' ? '✓' : '+'}
        </span>
      )}
    </span>
  );
}

/** Every word must appear in the name or the kind: "oak table", "lamp". */
export const matches = (query: string, ...fields: string[]) => {
  const hay = fields.join(' ').toLowerCase();
  return query.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w));
};

/** The search box over a furniture grid. `large`: the furniture menu's, wide and tall. */
export function SearchBox({ value, onChange, placeholder, large = false, inputRef }: { value: string; onChange(value: string): void; placeholder: string; large?: boolean; inputRef?: Ref<HTMLInputElement> }) {
  return (
    <label className="relative block">
      <IconSearch width={large ? 20 : 16} height={large ? 20 : 16} className={cn('pointer-events-none absolute top-1/2 -translate-y-1/2 text-muted', large ? 'left-4' : 'left-3')} />
      <input ref={inputRef} type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={placeholder} enterKeyHint="search"
        className={cn('w-full rounded-full border border-line bg-raised placeholder:text-faint focus:border-line-strong focus:outline-none', large ? 'h-12 pr-4 pl-12 text-base' : 'h-9 pr-3 pl-9 text-sm')} />
    </label>
  );
}

/** The server's refusal in words: `{"violations": ["…"]}` or `{"detail": "…"}` comes back as JSON text. */
export const reason = (err: unknown): string => {
  const text = err instanceof Error ? err.message : String(err);
  return /"violations":\s*\["([^"]+)"/.exec(text)?.[1] ?? /"detail":\s*"([^"]+)"/.exec(text)?.[1] ?? text;
};

/** Why a library piece could not be added, by the answer's status (the model is fetched on first use). */
function addError(err: unknown, label: string): string {
  const status = err instanceof ApiError ? err.status : 0;
  if (status === 404) return `${label} is no longer in the library.`;
  if (status === 422) return `${label} cannot be added: its 3D model could not be sized.`;
  if (status === 503) return `The 3D model of ${label} could not be downloaded. Try again in a moment.`;
  return reason(err);
}

/** Width × depth × height. Small things (a vase, a lamp) in centimetres: "0.1×0.1×0.3 m" says nothing. */
export function pieceSize({ dims_m: { w, d, h } }: Pick<LibraryPiece, 'dims_m'>): string {
  if (Math.max(w, d, h) < 1) return `${Math.round(w * 100)}×${Math.round(d * 100)}×${Math.round(h * 100)} cm`;
  return `${w.toFixed(1)}×${d.toFixed(1)}×${h.toFixed(1)} m`;
}

/** The design without its colour: "Parson sofa, beige" is "Parson sofa" when the colour is beige. */
export function designName(piece: LibraryPiece): string {
  const cut = piece.label.lastIndexOf(',');
  const tail = piece.label.slice(cut + 1).trim().toLowerCase();
  return cut > 0 && piece.color && tail === piece.color.trim().toLowerCase() ? piece.label.slice(0, cut) : piece.label;
}

/** The credit lines of the collections these pieces come from, worded as each collection asks. */
export const credits = (pieces: readonly LibraryPiece[]): string[] => [...new Set(pieces.map((p) => p.source).filter(Boolean))];

const HEX = /^#[0-9a-f]{3,8}$/i;

/** A piece's or a look's main colours, as a few small dots. */
export function ColourDots({ palette, max = 3, large = false }: { palette: readonly string[] | undefined; max?: number; large?: boolean }) {
  const colours = (palette ?? []).filter((c) => HEX.test(c)).slice(0, max);
  if (!colours.length) return null;
  return (
    <span aria-hidden className={cn('flex shrink-0', large ? 'gap-1' : 'gap-0.5')}>
      {colours.map((c, i) => (
        <i key={`${c}${i}`} className={cn('rounded-full border border-black/15', large ? 'h-4 w-4' : 'h-2.5 w-2.5')} style={{ backgroundColor: c }} />
      ))}
    </span>
  );
}

type AddState = 'idle' | 'adding' | 'added';

/**
 * A library piece as a card: the card adds it. The name, the size, up to three colour dots; a
 * design that comes in several colours has a small "N colours" control on its picture, which
 * opens them (`onFamily`; left out inside a design's own colours, where the colour is named).
 */
export function LibraryCard({ piece, state, onAdd, onFamily, where, disabled = false, large = false }: {
  piece: LibraryPiece;
  state: AddState;
  onAdd(piece: LibraryPiece): void;
  onFamily?(piece: LibraryPiece): void;
  /** Where a tap puts it, for the label: "the living room", "storage". */
  where?: string;
  disabled?: boolean;
  large?: boolean;
}) {
  const colours = onFamily && piece.family && (piece.variants ?? 1) > 1 ? piece.variants : 0;
  const size = pieceSize(piece);
  return (
    <div className="relative h-full">
      <button type="button" disabled={disabled || state === 'adding'} onClick={() => onAdd(piece)} aria-busy={state === 'adding'} data-library-id={piece.library_id} data-state={state}
        aria-label={`Add ${state === 'added' ? 'another ' : ''}${piece.label}${where ? ` to ${where}` : ''}`} title={[piece.brand, ...(piece.styles ?? [])].filter(Boolean).join(' · ') || undefined}
        className={cn(CARD, 'h-full')}>
        <CardImage src={piece.thumbnail_url} state={state} />
        <span className={large ? CARD_NAME_LARGE : CARD_NAME}>{piece.label}</span>
        <span className="flex items-center gap-1.5">
          <span className={cn(large ? CARD_SUB_LARGE : CARD_SUB, 'min-w-0 flex-1')}>{!onFamily && piece.color ? `${piece.color} · ${size}` : size}</span>
          <ColourDots palette={piece.palette} />
        </span>
      </button>
      {colours ? (
        // Beside the card's button, not inside it (a button may not hold a button), on a box the
        // size of the picture: every card keeps the same height, with or without it.
        <span className="pointer-events-none absolute inset-x-[7px] top-[7px] block aspect-square">
          <button type="button" onClick={() => onFamily?.(piece)} aria-label={`See the ${colours} colours of ${designName(piece)}`} data-testid="family-open"
            className="pointer-events-auto absolute bottom-1.5 left-1.5 inline-flex h-6 items-center gap-0.5 rounded-full border border-line bg-raised/95 pr-1 pl-2 text-[11px] font-semibold hover:border-text">
            {colours} colours
            <IconChevronRight width={11} height={11} />
          </button>
        </span>
      ) : null}
    </div>
  );
}

/** A card's outline while the first page loads: the same box, so the grid does not jump when the pieces arrive. */
function CardSkeleton({ large }: { large: boolean }) {
  return (
    <div aria-hidden className="flex animate-pulse flex-col rounded-xl border border-line bg-raised p-1.5">
      <span className="block aspect-square w-full rounded-lg bg-ink" />
      <span className={cn('w-3/4 rounded-full bg-ink', large ? 'mt-2 h-4' : 'mt-1.5 h-3.5')} />
      <span className={cn('mt-1 w-1/2 rounded-full bg-ink', large ? 'mb-px h-3.5' : 'h-3')} />
    </div>
  );
}

/**
 * Marks the last stretch of a list: once it scrolls into view (in whichever container scrolls),
 * the next page is asked for, a screen before the end is reached. It covers the end of the list
 * rather than sitting under it, so the page is on its way before the last row shows.
 */
function LoadMark({ library }: { library: Library }) {
  const mark = useRef<HTMLSpanElement>(null);
  const { loadMore } = library;
  const idle = library.hasMore && !library.loadingMore && !library.moreError;
  useEffect(() => {
    const el = mark.current;
    if (!el || !idle || typeof IntersectionObserver === 'undefined') return;
    const seen = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) loadMore();
    });
    seen.observe(el);
    return () => seen.disconnect();
  }, [idle, loadMore]);
  return <span ref={mark} aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-[60rem] max-h-full" />;
}

/**
 * Library pieces as a grid of cards that loads the next page as its end comes near. The first
 * page shows as card outlines; a new search dims the old pieces until its own arrive.
 */
export function LibraryGrid({ library, columns, stateOf, onAdd, onFamily, where, skeletons = 12, disabled = false, large = false }: {
  library: Library;
  /** The grid's column and gap classes. */
  columns: string;
  stateOf(libraryId: string): AddState;
  onAdd(piece: LibraryPiece): void;
  onFamily?(piece: LibraryPiece): void;
  where?: string;
  /** How many outlines stand in for the first page. */
  skeletons?: number;
  disabled?: boolean;
  large?: boolean;
}) {
  const first = library.loading && library.items.length === 0;
  return (
    <>
      <div className="relative">
        <ul className={cn('grid transition-opacity', columns, library.loading && !first && 'opacity-50')} aria-busy={library.loading}>
          {first
            ? Array.from({ length: skeletons }, (_, i) => (
                <li key={i}>
                  <CardSkeleton large={large} />
                </li>
              ))
            : library.items.map((p) => (
                <li key={p.library_id}>
                  <LibraryCard piece={p} state={stateOf(p.library_id)} onAdd={onAdd} onFamily={onFamily} where={where} disabled={disabled} large={large} />
                </li>
              ))}
        </ul>
        <LoadMark library={library} />
      </div>
      {library.loadingMore && (
        <p className="flex justify-center py-4">
          <Spinner label="Loading more…" />
        </p>
      )}
      {library.moreError && (
        <p role="alert" className="py-3 text-center text-sm text-danger">
          More could not be loaded: {library.moreError}{' '}
          <button type="button" onClick={library.loadMore} className="font-semibold underline underline-offset-2">
            Try again
          </button>
        </p>
      )}
    </>
  );
}

/**
 * Adding library pieces, one after the other in the order they were tapped: a piece's model is
 * downloaded the first time it is used (5 to 20 seconds), and nobody should have to wait for one
 * before tapping the next. Each piece is added to the family's furniture, then handed to
 * `onAdded` (which puts it in the room). `stateOf` is what a card shows; an added card keeps its check.
 */
export function useLibraryAdds(onAdded: (item: InventoryItem, piece: LibraryPiece) => void | Promise<void>) {
  const [waiting, setWaiting] = useState<LibraryPiece[]>([]);
  const [added, setAdded] = useState<ReadonlySet<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const line = useRef<Promise<void>>(Promise.resolve());
  const inLine = useRef(new Set<string>());
  const latest = useRef(onAdded);
  latest.current = onAdded;

  const add = useCallback((piece: LibraryPiece) => {
    const id = piece.library_id;
    if (inLine.current.has(id)) return;
    inLine.current.add(id);
    setWaiting((w) => [...w, piece]);
    setError(null);
    line.current = line.current.then(async () => {
      try {
        const item = await api().addFromLibrary(id);
        invalidate('inventory');
        setAdded((s) => new Set(s).add(id));
        try {
          await latest.current(item, piece);
        } catch (err) {
          setError(reason(err));
        }
      } catch (err) {
        setError(addError(err, piece.label));
      } finally {
        inLine.current.delete(id);
        setWaiting((w) => w.filter((p) => p.library_id !== id));
      }
    });
  }, []);

  const stateOf = useCallback((id: string): AddState => (waiting.some((p) => p.library_id === id) ? 'adding' : added.has(id) ? 'added' : 'idle'), [waiting, added]);
  /** `current`: the piece whose model is being fetched now. `waiting`: how many are in line, it included. */
  return { add, stateOf, current: waiting[0] ?? null, waiting: waiting.length, error };
}

/**
 * One design's colours, opened in place of the list and closed again. The list comes back where
 * it was scrolled to (`toTop`: unless what it lists has changed). `scroller`: the element that scrolls.
 */
export function useFamilyView(scroller: () => HTMLElement | null) {
  const [family, setFamily] = useState<LibraryPiece | null>(null);
  const back = useRef(0);
  const mounted = useRef(false);
  useLayoutEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    const el = scroller();
    if (el) el.scrollTop = family ? 0 : back.current;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [family]);
  const open = (piece: LibraryPiece) => {
    back.current = scroller()?.scrollTop ?? 0;
    setFamily(piece);
  };
  const close = (toTop = false) => {
    if (toTop) back.current = 0;
    setFamily(null);
  };
  return { family, open, close };
}

/** The nearest element above `el` that scrolls up and down. */
function scrollParent(el: HTMLElement | null): HTMLElement | null {
  for (let p = el?.parentElement ?? null; p; p = p.parentElement) {
    if (/auto|scroll/.test(getComputedStyle(p).overflowY)) return p;
  }
  return null;
}

/**
 * Furniture to add without a photo, for the phone: the library (thousands of real 3D models from
 * open collections) behind a search box, one row of section pills that scrolls sideways, and a
 * two-column grid that loads more as it is scrolled. The search covers the whole library and
 * keeps the chosen section. Adding a piece puts it in the family's furniture and hands it to
 * `onAdded`, which places it in the room. `scroll`: the grid scrolls on its own; off when the
 * container already scrolls (a modal), and then the search and the sections stay at its top.
 *
 * The big screen's furniture menu (FurnitureMenu) is the same library with more room around it.
 */
export function CatalogBrowser({ onAdded, scroll = true }: { onAdded(item: InventoryItem): void | Promise<void>; scroll?: boolean }) {
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState<string | null>(null);
  const typed = query.trim();
  const late = useDebounced(typed);
  // An emptied box applies at once; words wait for a pause in typing.
  const q = typed ? late : '';
  const root = useRef<HTMLDivElement>(null);
  const grid = useRef<HTMLDivElement>(null);
  const view = useFamilyView(() => (scroll ? grid.current : scrollParent(root.current)));
  const library = useLibrary({ q, category });
  const colours = useLibrary({ family: view.family?.family }, !!view.family);
  const adds = useLibraryAdds(onAdded);
  const shown = view.family ? colours : library;

  // A new search or section starts at the top of its own list.
  useEffect(() => {
    if (scroll && grid.current) grid.current.scrollTop = 0;
  }, [q, category, scroll]);

  const section = library.categories.find((c) => c.key === category)?.label;
  const all = library.categories.reduce((n, c) => n + c.count, 0);
  const nothing = !shown.loading && !shown.error && shown.items.length === 0;
  const lines = credits(shown.items);
  const pill = (on: boolean, empty: boolean) =>
    cn('inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border px-3 text-xs font-semibold whitespace-nowrap', on ? 'border-main bg-main text-on-main' : 'border-line bg-raised text-text', empty && !on && 'opacity-45');

  return (
    <div ref={root} className="flex flex-col gap-2" data-testid="library-browser">
      <div className={cn('flex flex-col gap-2', !scroll && 'sticky top-0 z-10 -mx-3 -mt-1 bg-surface px-3 pt-1 pb-1')}>
        <SearchBox
          value={query}
          onChange={(v) => {
            setQuery(v);
            view.close(true);
          }}
          placeholder={library.totalAll ? `Search ${library.totalAll.toLocaleString('en-US')} pieces: sofa, oak, green…` : 'Search sofas, lamps, oak, green…'}
        />
        {view.family ? (
          <div className="flex h-8 items-center gap-2">
            <button type="button" onClick={() => view.close()} className="inline-flex h-8 shrink-0 items-center gap-1 rounded-full border border-line bg-raised pr-3 pl-2 text-xs font-semibold">
              <IconChevronLeft width={14} height={14} /> Back
            </button>
            <p className="min-w-0 truncate text-[13px] font-semibold">
              {designName(view.family)} <span className="font-normal text-muted">· {view.family.variants} colours</span>
            </p>
          </div>
        ) : (
          <div role="group" aria-label="Sections" className="flex gap-1.5 overflow-x-auto overscroll-x-contain [scrollbar-width:none] [&::-webkit-scrollbar]:hidden" data-testid="library-sections">
            <button type="button" aria-pressed={!category} onClick={() => setCategory(null)} className={pill(!category, false)}>
              All
              {library.categories.length > 0 && <span className="font-normal tabular-nums opacity-70">{all.toLocaleString('en-US')}</span>}
            </button>
            {library.categories.map((c) => (
              <button key={c.key} type="button" aria-pressed={category === c.key} onClick={() => setCategory(c.key)} data-category={c.key} className={pill(category === c.key, c.count === 0)}>
                {c.label}
                <span className="font-normal tabular-nums opacity-70">{c.count.toLocaleString('en-US')}</span>
              </button>
            ))}
          </div>
        )}
        {adds.current && (
          <p role="status" className="flex items-center gap-2 text-xs text-muted">
            <Spinner />
            <span>Getting the 3D model of {adds.current.label}. The first time a piece is used, this can take 20 seconds.</span>
          </p>
        )}
        {adds.error && <p role="alert" className="text-sm text-danger">{adds.error}</p>}
      </div>
      {shown.error ? (
        <p role="alert" className="py-4 text-center text-sm text-danger">
          The library could not be loaded: {shown.error}{' '}
          <button type="button" onClick={shown.retry} className="font-semibold underline underline-offset-2">
            Try again
          </button>
        </p>
      ) : nothing && !view.family && library.totalAll === 0 ? (
        <p className="py-4 text-center text-sm text-muted">The library is empty. It needs the API (fixture mode has none).</p>
      ) : nothing ? (
        <p className="py-4 text-center text-sm text-muted">
          {q && !view.family ? `Nothing in the library matches “${q}”${section ? ` in ${section}` : ''}.` : `Nothing in ${view.family ? 'this design' : (section ?? 'the library')} yet.`}
          {category && !view.family && (
            <>
              {' '}
              <button type="button" onClick={() => setCategory(null)} className="font-semibold text-text underline underline-offset-2">
                Look in every section
              </button>
            </>
          )}
        </p>
      ) : null}
      <div ref={grid} className={scroll ? 'max-h-[40vh] overflow-y-auto overscroll-contain pr-1' : undefined} data-testid="catalog-grid">
        <LibraryGrid
          library={shown}
          columns="grid-cols-2 gap-2"
          stateOf={adds.stateOf}
          onAdd={adds.add}
          onFamily={view.family ? undefined : view.open}
          where="the room"
          skeletons={view.family ? Math.min(view.family.variants ?? 4, 8) : 6}
          // One at a time here: the phone's screen closes this window once a piece is in the room.
          disabled={adds.waiting > 0}
        />
      </div>
      <p className="text-[11px] text-faint">Real pieces at real size, from open 3D collections.{lines.length > 0 && ` 3D models: ${lines.join('; ')}.`}</p>
    </div>
  );
}
