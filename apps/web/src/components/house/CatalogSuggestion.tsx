'use client';

import type { CatalogPiece } from '@ithaca/schema';
import { useState } from 'react';
import { api } from '@/lib/api';
import { useHouseFurniture, useTwin } from '@/lib/data/hooks';
import { invalidate } from '@/lib/data/resource';
import { bounds } from '@/lib/geometry/polygon';
import { roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';

interface Props {
  propertyId: string;
  /** The room the agent meant; null = the room the user is in. */
  roomId: string | null;
  /** One of the user's pieces to put the new one next to. */
  nearItemId: string | null;
  pieces: CatalogPiece[];
}

/**
 * Catalog pieces the agent suggested, as picture cards in the chat. A click adds the piece to the
 * user's furniture and puts it in the room (next to `nearItemId` when that stands there); the
 * server picks the nearest spot that works. The agent is told with a `catalog.picked` event.
 */
export function CatalogSuggestion({ propertyId, roomId, nearItemId, pieces }: Props) {
  const twin = useTwin(propertyId);
  const house = useHouseFurniture(propertyId);
  const viewRoomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  const [busy, setBusy] = useState<string | null>(null);
  const [added, setAdded] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const targetId = roomId ?? viewRoomId;
  const room = twin.data?.rooms.find((r) => r.room_id === targetId);

  const add = async (piece: CatalogPiece) => {
    if (!room) {
      setError('Walk into a room first, then add the piece.');
      return;
    }
    setBusy(piece.catalog_id);
    setError(null);
    try {
      const item = await api().addFromCatalog(piece.catalog_id);
      const beside = house.data?.find((p) => p.item_id === nearItemId && p.room_id === room.room_id);
      const b = bounds(room.polygon);
      await api().placePiece(propertyId, item.item_id, {
        x: beside?.position.x ?? (b.minX + b.maxX) / 2,
        z: beside?.position.z ?? (b.minZ + b.maxZ) / 2,
        room_id: room.room_id,
        floor_id: room.floor_id,
        snap: true,
      });
      invalidate('inventory');
      invalidate(`furniture:${propertyId}`);
      dispatch('layout.changed', { layout_id: null });
      dispatch('catalog.picked', { catalog_id: piece.catalog_id, item_id: item.item_id, room_id: room.room_id });
      setAdded((ids) => [...ids, piece.catalog_id]);
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err);
      setError(/"violations":\s*\["([^"]+)"/.exec(text)?.[1] ?? text);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div data-testid="catalog-suggestion" className="space-y-1.5">
      <ul className="grid grid-cols-2 gap-2">
        {pieces.map((p) => {
          const done = added.includes(p.catalog_id);
          return (
            <li key={p.catalog_id}>
              <button
                type="button"
                disabled={busy !== null || done}
                onClick={() => void add(p)}
                aria-label={`Add ${p.label}${room && twin.data ? ` to ${roomName(twin.data, room.room_id)}` : ''}`}
                className="group flex w-full flex-col rounded-xl border border-line bg-raised p-2 text-left transition-colors hover:border-text disabled:cursor-default disabled:opacity-70"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={p.thumbnail_url} alt="" loading="lazy" className="aspect-square w-full rounded-lg bg-surface object-contain" />
                <span className="mt-1 text-sm font-semibold leading-tight">{p.label}</span>
                <span className="text-xs tabular-nums text-muted">
                  {p.dims_m.w.toFixed(2)} × {p.dims_m.d.toFixed(2)} × {p.dims_m.h.toFixed(2)} m
                </span>
                <span className="mt-1 text-xs font-semibold text-muted group-hover:text-text">{done ? 'Added ✓' : busy === p.catalog_id ? 'Adding…' : 'Click to add'}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    </div>
  );
}
