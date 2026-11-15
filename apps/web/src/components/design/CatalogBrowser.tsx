'use client';

import type { CatalogPiece, InventoryItem } from '@ithaca/schema';
import { useMemo, useState } from 'react';
import { IconSearch, IconTrash } from '@/components/ui/icons';
import { api } from '@/lib/api';
import { invalidate, useResource } from '@/lib/data/resource';

const kind = (itemClass: string) => itemClass.replace(/_/g, ' ');
const size = (p: CatalogPiece) => `${p.dims_m.w.toFixed(1)}×${p.dims_m.d.toFixed(1)}×${p.dims_m.h.toFixed(1)} m`;

/** A furniture card is itself the button: tapping a piece adds it. See docs/style.md. */
export const CARD =
  'group flex w-full min-w-0 flex-col rounded-xl border border-line bg-raised p-1.5 text-left transition-[border-color,transform] hover:border-text active:scale-[0.97] disabled:cursor-default disabled:active:scale-100';
/** The card's name and the line under it: one line each, cut short on a narrow phone. */
export const CARD_NAME = 'mt-1 truncate text-[13px] font-semibold leading-tight';
export const CARD_SUB = 'truncate text-[11px] tabular-nums text-muted';

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

/** The search box over a furniture grid. */
export function SearchBox({ value, onChange, placeholder }: { value: string; onChange(value: string): void; placeholder: string }) {
  return (
    <label className="relative block">
      <IconSearch width={16} height={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" />
      <input type="search" value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} aria-label={placeholder} enterKeyHint="search"
        className="h-9 w-full rounded-full border border-line bg-raised pr-3 pl-9 text-sm placeholder:text-faint focus:border-line-strong focus:outline-none" />
    </label>
  );
}

/** The catalog's looks, in the order the API lists them; a piece with none goes last. */
function byStyle(pieces: CatalogPiece[]): { style: string; pieces: CatalogPiece[] }[] {
  const groups = new Map<string, CatalogPiece[]>();
  for (const p of pieces) {
    const style = p.style ?? 'Other';
    groups.set(style, [...(groups.get(style) ?? []), p]);
  }
  return [...groups].map(([style, list]) => ({ style, pieces: list }));
}

/**
 * Ready-made furniture to add without a photo: a grid with a search box, grouped by look
 * (farmhouse, industrial, coastal…). The search also matches a look or a set by name, so
 * "glam" or "Parson" narrows the grid to it. Adding a piece puts it in the user's inventory and
 * hands it to `onAdded`, which places it in the room. `scroll`: the grid scrolls on its own; off
 * when the container already scrolls (a modal).
 */
export function CatalogBrowser({ onAdded, scroll = true }: { onAdded(item: InventoryItem): void | Promise<void>; scroll?: boolean }) {
  const catalog = useResource('catalog', () => api().listCatalog());
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const shown = useMemo(
    () => byStyle((catalog.data ?? []).filter((p) => matches(query, p.label, kind(p.item_class), p.style ?? '', ...(p.sets ?? [])))),
    [catalog.data, query],
  );

  const add = async (piece: CatalogPiece) => {
    setBusy(piece.catalog_id);
    setError(null);
    try {
      const item = await api().addFromCatalog(piece.catalog_id);
      invalidate('inventory');
      await onAdded(item);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };

  if (catalog.error) return <p className="text-sm text-danger">The catalog could not be loaded: {catalog.error.message}</p>;
  if (!catalog.data) return <p className="text-sm text-muted">Loading the catalog…</p>;
  if (!catalog.data.length) return <p className="text-sm text-muted">The catalog is empty. It needs the API (fixture mode has none).</p>;

  return (
    <div className="flex flex-col gap-2">
      <SearchBox value={query} onChange={setQuery} placeholder="Search sofas, beds, farmhouse, glam…" />
      {!shown.length && <p className="py-4 text-center text-sm text-muted">Nothing in the catalog matches “{query.trim()}”.</p>}
      <div className={`flex flex-col gap-3 ${scroll ? 'max-h-[40vh] overflow-y-auto pr-1' : ''}`} data-testid="catalog-grid">
        {shown.map((group) => (
          <section key={group.style} aria-label={group.style} data-style={group.style}>
            <h3 className="mb-1.5 flex items-baseline justify-between px-0.5">
              <span className="text-[11px] font-semibold tracking-wider text-faint uppercase">{group.style}</span>
              <span className="text-[11px] text-faint tabular-nums">{group.pieces.length}</span>
            </h3>
            <ul className="grid grid-cols-2 gap-2">
              {group.pieces.map((p) => (
                <li key={p.catalog_id}>
                  <button type="button" disabled={busy !== null} onClick={() => void add(p)} aria-label={`Add ${p.label} to the room`} aria-busy={busy === p.catalog_id}
                    title={p.sets?.length ? `Part of: ${p.sets.join(', ')}` : undefined} className={CARD}>
                    <CardImage src={p.thumbnail_url} state={busy === p.catalog_id ? 'adding' : 'idle'} />
                    <span className={CARD_NAME}>{p.label}</span>
                    <span className={CARD_SUB}>{size(p)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      <p className="text-[11px] text-faint">Pieces from matching collections, in several looks, at real size. 3D models: Amazon Berkeley Objects (Amazon.com), CC BY 4.0.</p>
    </div>
  );
}
