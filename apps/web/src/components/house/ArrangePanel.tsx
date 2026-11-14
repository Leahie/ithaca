'use client';

import type { InventoryItem, LayoutVariant, PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState } from 'react';
import { CatalogBrowser } from '@/components/design/CatalogBrowser';
import { PlanEditor } from '@/components/design/PlanEditor';
import { api } from '@/lib/api';
import { useInventory, useLayouts } from '@/lib/data/hooks';
import { invalidate, useResource } from '@/lib/data/resource';
import { bounds } from '@/lib/geometry/polygon';
import { layoutForRoom } from '@/lib/layouts';
import { roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';

/**
 * Floating panel over the 3D views: the furniture of the layout on screen, as a plan the user
 * can rearrange by hand. The Tour and the Sun Lab redraw from the stored layout after each change.
 */
export function ArrangePanel({ twin, onClose }: { twin: PropertyTwin; onClose(): void }) {
  const layoutId = useSession((s) => s.viewer.selected_layout_id);
  const layouts = useLayouts(twin.property_id);
  const inventory = useInventory();
  const listed = layouts.data?.find((l) => l.layout_id === layoutId);
  // A layout the agent just made may not be in the list yet: ask for it by id.
  const single = useResource(layoutId && layouts.data && !listed ? `layout:${layoutId}` : null, () => api().getLayout(layoutId!));
  const viewRoomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  // The panel works on the room the user is standing in. When they walk into another room, its own
  // layout becomes the one on screen (if it has one); a room with none starts empty.
  const selected = listed ?? single.data;
  const here = viewRoomId ?? selected?.room_id ?? null;
  const fresh = selected?.room_id === here ? selected : undefined;
  const roomLayout = layoutForRoom(layouts.data, here);
  useEffect(() => {
    if (here && roomLayout && roomLayout.layout_id !== layoutId && selected?.room_id !== here) {
      dispatch('layout.select', { layout_id: roomLayout.layout_id });
    }
  }, [here, roomLayout, layoutId, selected?.room_id, dispatch]);
  // Every change reloads the layout list, and for a moment there is no data. Keep showing the last
  // layout meanwhile, so the editor stays mounted and the piece being turned stays selected.
  const last = useRef<LayoutVariant | undefined>(undefined);
  if (fresh) last.current = fresh;
  const layout = fresh ?? (last.current?.room_id === here && last.current?.layout_id === layoutId ? last.current : undefined);
  const items = useMemo(() => new Map<string, InventoryItem>((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const room = layout ? twin.rooms.find((r) => r.room_id === layout.room_id) : undefined;
  const openings = useMemo(() => (room ? twin.openings.filter((o) => o.room_id === room.room_id || o.other_room_id === room.room_id) : []), [twin.openings, room]);

  const [browsing, setBrowsing] = useState(false);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  /** Delete a piece from the user's furniture for good (it also leaves any room it stood in). */
  const discard = async (item: InventoryItem) => {
    setDeleting(null);
    try {
      await api().deleteInventoryItem(item.item_id);
      invalidate('inventory');
      invalidate('layouts:');
      invalidate('layout:');
    } catch (err) {
      setPlaceError(err instanceof Error ? err.message : String(err));
    }
  };
  // Where a new piece goes: the room of the layout on screen, else the room the user is in.
  const targetRoom = room ?? twin.rooms.find((r) => r.room_id === here);
  const placed = new Set((layout?.placements ?? []).map((p) => p.item_id));
  const unplaced = (inventory.data ?? []).filter((i) => !placed.has(i.item_id));

  /** Put an owned piece into the room: the server finds the nearest spot that works. */
  const place = async (item: InventoryItem) => {
    if (!targetRoom) {
      setPlaceError('Walk into a room first, then add the piece.');
      return;
    }
    setPlaceError(null);
    const b = bounds(targetRoom.polygon);
    try {
      const result = await api().dropItem({
        property_id: twin.property_id,
        item_id: item.item_id,
        x: (b.minX + b.maxX) / 2,
        z: (b.minZ + b.maxZ) / 2,
        layout_id: layout?.layout_id ?? null,
        room_id: layout ? null : targetRoom.room_id,
        floor_id: null,
      });
      last.current = result;
      invalidate('layouts:');
      invalidate('layout:');
      if (result.layout_id !== layoutId) dispatch('layout.select', { layout_id: result.layout_id }); // a new layout: show it
      setBrowsing(false);
    } catch (err) {
      const text = err instanceof Error ? err.message : String(err);
      setPlaceError(/"violations":\s*\["([^"]+)"/.exec(text)?.[1] ?? text);
    }
  };

  return (
    <section aria-label="Arrange furniture" className="absolute bottom-4 left-4 z-20 w-[min(26rem,calc(100%-2rem))] rounded-2xl border border-line bg-surface p-3 shadow-lg">
      <header className="mb-2 flex items-center gap-2">
        <h2 className="font-display text-base font-semibold">
          {browsing ? 'Add furniture' : 'Arrange'}
          {targetRoom ? `: ${roomName(twin, targetRoom.room_id)}` : ''}
        </h2>
        <button type="button" onClick={() => setBrowsing(!browsing)} aria-pressed={browsing} className="ml-auto rounded-full bg-ink px-2.5 py-1 text-sm font-semibold hover:bg-raised">
          {browsing ? 'Back to the plan' : 'Add furniture'}
        </button>
        <button type="button" onClick={onClose} className="rounded-full px-2.5 py-1 text-sm text-muted hover:bg-ink hover:text-text" aria-label="Close the arrange panel">
          Close
        </button>
      </header>
      {browsing ? (
        <CatalogBrowser onAdded={place} />
      ) : layout && room ? (
        <PlanEditor room={room} openings={openings} layout={layout} items={items} />
      ) : (
        <p className="text-sm text-muted">{layouts.data ? 'Nothing is placed in this room yet. Add furniture, or ask the guide to set it up.' : 'Loading…'}</p>
      )}
      {!browsing && unplaced.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-line pt-2 text-sm">
          <span className="text-muted">Your furniture:</span>
          {unplaced.slice(0, 8).map((i) =>
            deleting === i.item_id ? (
              // Deleting can't be undone, so it takes a second, explicit click.
              <span key={i.item_id} className="inline-flex items-center gap-1 rounded-full bg-ink py-1 pl-2.5 pr-1">
                Delete {i.label} for good?
                <button type="button" onClick={() => void discard(i)} className="rounded-full bg-danger px-2 py-0.5 font-semibold text-surface">
                  Delete
                </button>
                <button type="button" onClick={() => setDeleting(null)} className="rounded-full px-2 py-0.5 hover:bg-raised">
                  Keep
                </button>
              </span>
            ) : (
              <span key={i.item_id} className="inline-flex items-center rounded-full bg-ink">
                <button type="button" onClick={() => void place(i)} className="rounded-l-full py-1 pl-2.5 pr-1.5 hover:bg-raised" title={`Place ${i.label} in this room`}>
                  + {i.label}
                </button>
                <button type="button" onClick={() => setDeleting(i.item_id)} className="rounded-r-full py-1 pl-1 pr-2 text-muted hover:bg-raised hover:text-danger" aria-label={`Delete ${i.label} from your furniture`} title="Delete from your furniture">
                  ×
                </button>
              </span>
            ),
          )}
        </div>
      )}
      {placeError && <p role="alert" className="mt-1 text-sm text-danger">{placeError}</p>}
    </section>
  );
}
