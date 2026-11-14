'use client';

import type { InventoryItem } from '@ithaca/schema';
import { useMemo, useState } from 'react';
import { PlanPreview } from '@/components/design/PlanPreview';
import { cn } from '@/components/ui';
import { api } from '@/lib/api';
import { useInventory, useTwin } from '@/lib/data/hooks';
import { useResource } from '@/lib/data/resource';
import { useSession } from '@/lib/store/session';

/** What sets an option apart, from the rationale the server wrote: where the pieces stand. */
function where(rationale: string): string {
  const afterName = rationale.replace(/^.*?\(option \d+\)\.\s*/, '');
  return afterName.split(/\.\s+(Meets|Could not)/)[0] ?? afterName;
}

/**
 * The layout options the agent made, as small top-down plans in the chat. A click puts that
 * option in the room; the one in the room now is outlined.
 */
export function LayoutSuggestion({ layoutIds }: { layoutIds: string[] }) {
  // Keyed under "layout:" so a hand edit in the Arrange panel refreshes these plans too.
  const loaded = useResource(`layout:set:${layoutIds.join(',')}`, () => Promise.all(layoutIds.map((id) => api().getLayout(id))));
  const twin = useTwin(loaded.data?.[0]?.property_id ?? null);
  const inventory = useInventory();
  const selectedId = useSession((s) => s.viewer.selected_layout_id);
  const dispatch = useSession((s) => s.dispatch);
  const [kept, setKept] = useState<string | null>(null);
  const items = useMemo(() => new Map<string, InventoryItem>((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);

  if (loaded.error) return <p className="text-sm text-danger">The layouts could not be loaded: {loaded.error.message}</p>;
  if (!loaded.data || !twin.data) return <p className="text-sm text-muted">Loading the layouts…</p>;
  const t = twin.data;

  const inRoom = loaded.data.find((l) => l.layout_id === selectedId);

  return (
    <div data-testid="layout-suggestion" className="space-y-2">
    <ul className="grid grid-cols-2 gap-2">
      {loaded.data.map((layout, i) => {
        const room = t.rooms.find((r) => r.room_id === layout.room_id);
        if (!room) return null;
        const openings = t.openings.filter((o) => o.room_id === room.room_id || o.other_room_id === room.room_id);
        const current = layout.layout_id === selectedId;
        return (
          <li key={layout.layout_id}>
            <button
              type="button"
              aria-pressed={current}
              onClick={() => dispatch('layout.select', { layout_id: layout.layout_id })}
              className={cn('flex w-full flex-col rounded-xl border bg-raised p-2 text-left transition-colors hover:border-text', current ? 'border-2 border-text' : 'border-line')}
            >
              <PlanPreview room={room} openings={openings} placements={layout.placements} items={items} labels={false} className="aspect-square w-full rounded-lg bg-ink" />
              <span className="mt-1 text-sm font-semibold">
                Option {i + 1}
                {layout.layout_id === kept ? ' · kept' : current ? ' · in the room' : ''}
              </span>
              <span className="line-clamp-3 text-xs leading-snug text-muted">{where(layout.rationale)}</span>
            </button>
          </li>
        );
      })}
    </ul>
    {/* Clicking an option only tries it in the room. Keeping one is a separate, deliberate step:
        that is what the guide is told about and what goes into memory. */}
    {kept ? (
      <p className="text-sm font-semibold text-muted">Kept option {loaded.data.findIndex((l) => l.layout_id === kept) + 1} ✓</p>
    ) : (
      <div className="flex items-center gap-2">
        <button
          type="button"
          disabled={!inRoom}
          onClick={() => {
            if (!inRoom) return;
            setKept(inRoom.layout_id);
            dispatch('layout.confirm', { layout_id: inRoom.layout_id });
          }}
          className="rounded-full bg-text px-3.5 py-1.5 text-sm font-semibold text-surface disabled:opacity-40"
        >
          {inRoom ? `Keep option ${loaded.data.indexOf(inRoom) + 1}` : 'Keep this layout'}
        </button>
        <span className="text-xs text-muted">Click an option to try it. Nothing is saved until you keep one.</span>
      </div>
    )}
    </div>
  );
}
