'use client';

import type { CatalogPiece, InventoryItem } from '@ithaca/schema';
import { useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { invalidate, useResource } from '@/lib/data/resource';

const kind = (itemClass: string) => itemClass.replace(/_/g, ' ');
const size = (p: CatalogPiece) => `${p.dims_m.w.toFixed(2)} × ${p.dims_m.d.toFixed(2)} × ${p.dims_m.h.toFixed(2)} m`;

/**
 * Ready-made furniture to add without a photo: a grid filtered by kind. Adding a piece puts it in
 * the user's inventory and hands it to `onAdded`, which places it in the room.
 */
export function CatalogBrowser({ onAdded }: { onAdded(item: InventoryItem): void | Promise<void> }) {
  const catalog = useResource('catalog', () => api().listCatalog());
  const [filter, setFilter] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const kinds = useMemo(() => [...new Set((catalog.data ?? []).map((p) => p.item_class))], [catalog.data]);
  const shown = (catalog.data ?? []).filter((p) => !filter || p.item_class === filter);

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
      <div className="flex flex-wrap gap-1" role="group" aria-label="Kind of furniture">
        {[null, ...kinds].map((k) => (
          <button
            key={k ?? 'all'}
            type="button"
            aria-pressed={filter === k}
            onClick={() => setFilter(k)}
            className={`rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${filter === k ? 'bg-sun text-main' : 'bg-ink text-muted hover:text-text'}`}
          >
            {k ? kind(k) : 'All'}
          </button>
        ))}
      </div>
      <ul className="grid max-h-[40vh] grid-cols-2 gap-2 overflow-y-auto pr-1">
        {shown.map((p) => (
          <li key={p.catalog_id} className="flex flex-col rounded-xl bg-ink p-2">
            {/* A small remote thumbnail: next/image would need the host configured. */}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={p.thumbnail_url} alt="" loading="lazy" className="aspect-square w-full rounded-lg bg-surface object-contain" />
            <span className="mt-1 text-sm font-semibold leading-tight">{p.label}</span>
            <span className="text-xs tabular-nums text-muted">{size(p)}</span>
            <button
              type="button"
              disabled={busy !== null}
              onClick={() => void add(p)}
              className="mt-1.5 rounded-full bg-raised px-2.5 py-1 text-sm font-semibold hover:bg-sun hover:text-main disabled:cursor-wait disabled:opacity-50"
            >
              {busy === p.catalog_id ? 'Adding…' : 'Add to the room'}
            </button>
          </li>
        ))}
      </ul>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      <p className="text-xs text-faint">Generic stand-in pieces at real size, from Poly Haven (public domain). Not products to buy.</p>
    </div>
  );
}
