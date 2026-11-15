'use client';

import type { InventoryItem, LayoutVariant, PropertyTwin } from '@ithaca/schema';
import { useMemo, useState } from 'react';
import { PlanPreview } from '@/components/design/PlanPreview';
import { Badge, Button, Notice, Spinner, ViewState, cn } from '@/components/ui';
import { IconCheck } from '@/components/ui/icons';
import { api } from '@/lib/api';
import { useInventory, useLayouts } from '@/lib/data/hooks';
import { invalidate } from '@/lib/data/resource';
import { visibleLayouts } from '@/lib/layouts';
import { roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';

function VariantCard({ layout, index, twin, items, selected }: { layout: LayoutVariant; index: number; twin: PropertyTwin; items: Map<string, InventoryItem>; selected: boolean }) {
  const dispatch = useSession((s) => s.dispatch);
  const [feedback, setFeedback] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const room = twin.rooms.find((r) => r.room_id === layout.room_id);
  const openings = twin.openings.filter((o) => o.room_id === layout.room_id);
  const missing = layout.placements.filter((p) => !items.has(p.item_id)).length;
  // A layout is a proposal until it is kept (docs/specs/furniture.md): trying one only draws it in
  // its room; keeping it makes its pieces the room's furniture.
  const kept = layout.status === 'chosen';
  const trying = selected && layout.status === 'proposed';
  const see = (mode: 'tour' | 'sun') => {
    dispatch('viewer.goto', { room_id: layout.room_id });
    dispatch('viewer.setMode', { mode });
  };

  const sendFeedback = async () => {
    const text = feedback.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    try {
      await api().layoutFeedback(layout.layout_id, text);
      invalidate('layouts:');
      // The same words go to the agent, so it can propose again (and remember the preference).
      dispatch('chat.user', { text: `About layout ${index + 1} for ${roomName(twin, room?.room_id)}: ${text}` });
      setFeedback('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <article
      data-testid="variant-card"
      data-layout-id={layout.layout_id}
      data-selected={selected}
      data-status={layout.status}
      className={cn('flex flex-col overflow-hidden rounded-xl border bg-surface', trying || kept ? 'border-sun shadow-[0_0_0_1px_var(--color-sun)]' : 'border-line')}
    >
      <div className="relative aspect-[4/3] bg-ink">
        {layout.thumbnail_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={layout.thumbnail_url} alt={`Rendering of layout ${index + 1}`} className="h-full w-full object-cover" />
        ) : room ? (
          <PlanPreview room={room} openings={openings} placements={layout.placements} items={items} className="h-full w-full p-3" />
        ) : null}
        <div className="absolute top-2 left-2 flex gap-1.5">
          <Badge>Layout {index + 1}</Badge>
          {kept && (
            <Badge tone="sun">
              <IconCheck width={11} height={11} /> In the house
            </Badge>
          )}
          {trying && <Badge tone="sun">Trying it</Badge>}
          {layout.status === 'rejected' && (
            <Badge tone="danger">
              Rejected
            </Badge>
          )}
        </div>
        {!layout.thumbnail_url && <span className="absolute right-2 bottom-2 text-[10px] text-faint">plan view · rendering pending</span>}
      </div>
      <div className="flex flex-1 flex-col gap-3 p-4">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm font-medium">{roomName(twin, layout.room_id)}</span>
          <span className="text-xs text-muted" title="Solver score">
            fit {(layout.score * 100).toFixed(0)}%
          </span>
        </div>
        <p className="text-[13px] leading-5 text-muted">{layout.rationale}</p>
        {layout.feedback && <p className="rounded-md border border-line px-2.5 py-1.5 text-xs text-muted">Your note: “{layout.feedback}”</p>}
        {missing > 0 && <p className="text-xs text-sun-deep">{missing} item(s) in this layout are no longer in your inventory.</p>}
        <div className="mt-auto flex flex-wrap gap-2">
          {kept ? (
            <Button variant="subtle" className="flex-1" disabled>
              Kept
            </Button>
          ) : trying ? (
            <Button variant="primary" className="flex-1" onClick={() => dispatch('layout.confirm', { layout_id: layout.layout_id })}>
              Keep it
            </Button>
          ) : (
            <Button variant="primary" className="flex-1" onClick={() => dispatch('layout.select', { layout_id: layout.layout_id })}>
              Try it in the room
            </Button>
          )}
          {(trying || kept) && (
            <>
              <Button onClick={() => see('tour')}>Tour</Button>
              <Button onClick={() => see('sun')}>Sun Lab</Button>
            </>
          )}
          {trying && (
            <Button variant="ghost" onClick={() => dispatch('layout.select', { layout_id: null })} title="Go back to what stands in the room now">
              Undo
            </Button>
          )}
        </div>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void sendFeedback();
          }}
        >
          <input
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="What would you change?"
            aria-label={`Feedback on layout ${index + 1}`}
            className="h-8 min-w-0 flex-1 rounded-md border border-line bg-ink px-2.5 text-xs placeholder:text-faint focus:border-line-strong focus:outline-none"
          />
          <Button size="sm" type="submit" disabled={busy || !feedback.trim()} className="h-8">
            Send
          </Button>
        </form>
        {error && <p className="text-xs text-danger">{error}</p>}
      </div>
    </article>
  );
}

/** Design: layout variants as cards. Trying one draws it in the Tour and the Sun Lab; keeping it furnishes the room. */
export function DesignTab({ twin }: { twin: PropertyTwin }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const shown = useSession((s) => s.viewer.shown_layout_ids);
  const selectedId = useSession((s) => s.viewer.selected_layout_id);
  const dispatch = useSession((s) => s.dispatch);
  const layouts = useLayouts(twin.property_id);
  const inventory = useInventory();
  const items = useMemo(() => new Map((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const room = twin.rooms.find((r) => r.room_id === roomId);

  const { variants, scope } = useMemo(() => visibleLayouts(layouts.data ?? [], shown, roomId), [layouts.data, shown, roomId]);

  if (layouts.error) {
    return (
      <ViewState>
        <Notice tone="error" title="Could not load layouts">
          {layouts.error.message}
        </Notice>
      </ViewState>
    );
  }
  if (!layouts.data) {
    return (
      <ViewState>
        <Spinner label="Loading layouts…" />
      </ViewState>
    );
  }

  const askAgent = (
    <Button variant="primary" size="sm" onClick={() => dispatch('chat.user', { text: room ? `Lay out ${roomName(twin, room.room_id)} with my furniture` : 'Show layouts' })}>
      Ask the agent for layouts
    </Button>
  );

  return (
    <div className="h-full overflow-y-auto p-5" data-testid="design" data-variants={variants.length} data-scope={scope}>
      <div className="mx-auto max-w-5xl">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold">{scope === 'room' && room ? `Layouts for ${roomName(twin, room.room_id)}` : 'Layouts'}</h1>
            <p className="text-sm text-muted">Your own furniture, arranged by the designer. Try a layout to see it in the tour and under the sun; keep it to make it the room's furniture.</p>
          </div>
          {scope === 'agent' && (
            <Button size="sm" variant="ghost" onClick={() => dispatch('design.showVariants', { layout_ids: [] })}>
              Show all layouts
            </Button>
          )}
        </div>

        {scope === 'all' && room && variants.length > 0 && (
          <div className="mb-4">
            <Notice title={`No layouts for ${roomName(twin, room.room_id)} yet`} action={askAgent}>
              Showing the layouts for other rooms.
            </Notice>
          </div>
        )}

        {variants.length > 0 ? (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {variants.map((l, i) => (
              <VariantCard key={l.layout_id} layout={l} index={i} twin={twin} items={items} selected={l.layout_id === selectedId} />
            ))}
          </div>
        ) : (
          <Notice title="No layouts yet" action={askAgent}>
            The designer needs furniture to work with. Add pieces on the Me page, then ask for a layout.
          </Notice>
        )}
      </div>
    </div>
  );
}
