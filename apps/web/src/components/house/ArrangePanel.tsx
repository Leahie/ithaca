'use client';

import type { InventoryItem, LayoutVariant, PropertyTwin } from '@ithaca/schema';
import { useMemo, useRef } from 'react';
import { PlanEditor } from '@/components/design/PlanEditor';
import { api } from '@/lib/api';
import { useInventory, useLayouts } from '@/lib/data/hooks';
import { useResource } from '@/lib/data/resource';
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
  // Every change reloads the layout list, and for a moment there is no data. Keep showing the last
  // layout meanwhile, so the editor stays mounted and the piece being turned stays selected.
  const last = useRef<LayoutVariant | undefined>(undefined);
  const fresh = listed ?? single.data;
  if (fresh) last.current = fresh;
  const layout = fresh ?? (last.current?.layout_id === layoutId ? last.current : undefined);
  const items = useMemo(() => new Map<string, InventoryItem>((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const room = layout ? twin.rooms.find((r) => r.room_id === layout.room_id) : undefined;
  const openings = useMemo(() => (room ? twin.openings.filter((o) => o.room_id === room.room_id || o.other_room_id === room.room_id) : []), [twin.openings, room]);

  return (
    <section aria-label="Arrange furniture" className="absolute bottom-4 left-4 z-20 w-[min(26rem,calc(100%-2rem))] rounded-2xl border border-line bg-surface p-3 shadow-lg">
      <header className="mb-2 flex items-center gap-2">
        <h2 className="font-display text-base font-semibold">Arrange{room ? `: ${roomName(twin, room.room_id)}` : ''}</h2>
        <button type="button" onClick={onClose} className="ml-auto rounded-full px-2.5 py-1 text-sm text-muted hover:bg-ink hover:text-text" aria-label="Close the arrange panel">
          Close
        </button>
      </header>
      {layout && room ? (
        <PlanEditor room={room} openings={openings} layout={layout} items={items} />
      ) : (
        <p className="text-sm text-muted">{layoutId ? 'Loading the layout…' : 'No furniture is placed yet. Ask the guide to set up a room, then arrange it here.'}</p>
      )}
    </section>
  );
}
