'use client';

import type { LayoutVariant } from '@ithaca/schema';
import Link from 'next/link';
import { useMemo } from 'react';
import { PlanPreview } from '@/components/design/PlanPreview';
import { Markdown } from '@/components/Markdown';
import { Badge, Notice, Spinner } from '@/components/ui';
import { config } from '@/lib/config';
import { useInventory, useLayouts, useMemoryFile, useMemoryList, useTwin } from '@/lib/data/hooks';
import { roomName } from '@/lib/rooms';

const STATUS: Record<LayoutVariant['status'], { label: string; tone: 'leaf' | 'danger' | 'neutral' }> = {
  chosen: { label: 'Chosen', tone: 'leaf' },
  rejected: { label: 'Rejected', tone: 'danger' },
  proposed: { label: 'Proposed', tone: 'neutral' },
};

function PropertyHistory({ propertyId, hasNotes }: { propertyId: string; hasNotes: boolean }) {
  const twin = useTwin(propertyId);
  const layouts = useLayouts(propertyId);
  const notes = useMemoryFile(hasNotes ? `properties/${propertyId}.md` : null);
  const inventory = useInventory();
  const items = useMemo(() => new Map((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const decided = [...(layouts.data ?? [])].sort((a, b) => Number(b.status === 'chosen') - Number(a.status === 'chosen') || b.score - a.score);

  return (
    <section className="rounded-xl border border-line bg-surface p-5" data-testid="property-history" data-property-id={propertyId}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-base font-semibold">{twin.data?.address ?? propertyId}</h2>
        <Link href={`/house/${encodeURIComponent(propertyId)}`} className="text-sm text-sky hover:underline">
          Open this home
        </Link>
      </div>
      {twin.error && <p className="mt-1 text-xs text-muted">The twin for this home is not available ({twin.error.message}).</p>}

      {hasNotes && (
        <div className="mt-4 rounded-lg border border-line bg-ink/40 p-4">
          <div className="mb-2 text-[11px] font-semibold tracking-wide text-faint uppercase">The agent&apos;s notes</div>
          {notes.data !== undefined ? <Markdown text={notes.data} /> : notes.error ? <p className="text-xs text-danger">{notes.error.message}</p> : <Spinner />}
        </div>
      )}

      <div className="mt-4">
        <div className="mb-2 text-[11px] font-semibold tracking-wide text-faint uppercase">Layout decisions</div>
        {!layouts.data ? (
          layouts.error ? (
            <p className="text-xs text-danger">{layouts.error.message}</p>
          ) : (
            <Spinner />
          )
        ) : decided.length === 0 ? (
          <p className="text-sm text-muted">No layouts were proposed for this home.</p>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {decided.map((l) => {
              const room = twin.data?.rooms.find((r) => r.room_id === l.room_id);
              return (
                <li key={l.layout_id} className="flex gap-3 rounded-lg border border-line p-3" data-status={l.status}>
                  {room && twin.data && (
                    <PlanPreview room={room} openings={twin.data.openings.filter((o) => o.room_id === room.room_id)} placements={l.placements} items={items} labels={false} className="h-20 w-24 shrink-0" />
                  )}
                  <div className="min-w-0 text-sm">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{twin.data ? roomName(twin.data, l.room_id) : l.room_id}</span>
                      <Badge tone={STATUS[l.status].tone}>{STATUS[l.status].label}</Badge>
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs leading-4 text-muted">{l.rationale}</p>
                    {l.feedback && <p className="mt-1 text-xs leading-4">You said: “{l.feedback}”</p>}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

/** Per-property decision history: the agent's notes and what happened to each proposed layout. */
export function HistorySection() {
  const memory = useMemoryList();
  if (memory.error) {
    return (
      <Notice tone="error" title="Could not load history">
        {memory.error.message}
      </Notice>
    );
  }
  if (!memory.data) return <Spinner label="Loading history…" />;

  // Homes the agent has notes on, plus the configured home.
  const noted = memory.data.flatMap((p) => /^properties\/(.+)\.md$/.exec(p)?.[1] ?? []);
  const ids = [...new Set([...noted, ...(config.defaultPropertyId ? [config.defaultPropertyId] : [])])];
  if (!ids.length) return <Notice title="No homes yet">Homes you view appear here, with the agent&apos;s notes and your layout decisions.</Notice>;

  return (
    <div className="space-y-5" data-testid="history">
      {ids.map((id) => (
        <PropertyHistory key={id} propertyId={id} hasNotes={noted.includes(id)} />
      ))}
    </div>
  );
}
