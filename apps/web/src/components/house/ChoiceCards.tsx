'use client';

import type { ChoiceOption, InventoryItem, PropertyTwin } from '@ithaca/schema';
import { useMemo } from 'react';
import { CARD, CARD_NAME, CARD_SUB } from '@/components/design/CatalogBrowser';
import { PlanPreview } from '@/components/design/PlanPreview';
import { cn } from '@/components/ui';
import { IconCheck } from '@/components/ui/icons';
import { useHouseFurniture, useInventory, useTwin } from '@/lib/data/hooks';
import type { ChatItem } from '@/lib/store/reducer';
import { useSession } from '@/lib/store/session';

type Choice = Extract<ChatItem, { kind: 'choice' }>;

const PICTURE = 'aspect-square w-full overflow-hidden rounded-lg bg-surface';

/** What a card shows: the room's plan with what stands in it, one picture, or up to four as a collage (a furniture set). */
function Picture({ option, twin, pieces, items }: { option: ChoiceOption; twin: PropertyTwin | undefined; pieces: { item_id: string; room_id: string; position: { x: number; y: number; z: number }; yaw_deg: number }[]; items: Map<string, InventoryItem> }) {
  const room = option.room_id ? twin?.rooms.find((r) => r.room_id === option.room_id) : undefined;
  if (room && twin) {
    const openings = twin.openings.filter((o) => o.room_id === room.room_id || o.other_room_id === room.room_id);
    return <PlanPreview room={room} openings={openings} placements={pieces.filter((p) => p.room_id === room.room_id)} items={items} labels={false} className={cn(PICTURE, 'bg-ink p-1')} />;
  }
  const urls = option.image_urls ?? [];
  if (urls.length === 1) {
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={urls[0]} alt="" loading="lazy" className={cn(PICTURE, 'object-contain')} />;
  }
  if (urls.length > 1) {
    return (
      <span className={cn(PICTURE, 'grid grid-cols-2 grid-rows-2 gap-px bg-line')}>
        {urls.slice(0, 4).map((url) => (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={url} src={url} alt="" loading="lazy" className="h-full w-full bg-surface object-contain" />
        ))}
      </span>
    );
  }
  return null;
}

/**
 * A question from the guide with a few definite answers, as cards to tap. A room is drawn as its
 * plan, a furniture set as its first pieces, a piece as its picture; an answer in words is a
 * plain button. The tap is the answer: it goes to the guide and reads as the user's line in the
 * chat. Once answered, the cards stay, with the choice marked.
 */
export function ChoiceCards({ item, propertyId, question = true }: { item: Choice; propertyId: string | null; question?: boolean }) {
  const dispatch = useSession((s) => s.dispatch);
  const needsPlan = item.options.some((o) => o.room_id);
  const twin = useTwin(needsPlan ? propertyId : null);
  const house = useHouseFurniture(needsPlan ? propertyId : null);
  const inventory = useInventory();
  const items = useMemo(() => new Map<string, InventoryItem>((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const pictured = item.options.some((o) => o.room_id || (o.image_urls ?? []).length > 0);
  const answered = item.picked !== null;

  const pick = (o: ChoiceOption) => {
    if (!answered) dispatch('choice.picked', { choice_id: item.id, option_id: o.option_id, label: o.label });
  };

  return (
    <div data-testid="choice-cards" className="space-y-2">
      {question && <p className="font-display text-[15px] leading-snug font-semibold">{item.question}</p>}
      <ul className={pictured ? 'grid grid-cols-2 gap-2' : 'flex flex-col gap-2'}>
        {item.options.map((o) => {
          const chosen = item.picked === o.option_id;
          return (
            <li key={o.option_id}>
              <button
                type="button"
                disabled={answered}
                aria-pressed={chosen}
                onClick={() => pick(o)}
                className={cn(
                  pictured ? CARD : 'flex min-h-12 w-full items-center justify-between gap-3 rounded-2xl border border-line bg-raised px-4 py-2.5 text-left transition-[border-color,transform] hover:border-text active:scale-[0.98] disabled:active:scale-100',
                  chosen && 'border-2 border-text',
                  answered && !chosen && 'opacity-45',
                )}
              >
                {pictured ? (
                  <>
                    <span className="relative block">
                      <Picture option={o} twin={twin.data} pieces={house.data ?? []} items={items} />
                      {chosen && (
                        <span aria-hidden className="absolute top-1.5 right-1.5 grid h-6 w-6 place-items-center rounded-full bg-leaf text-main">
                          <IconCheck width={13} height={13} />
                        </span>
                      )}
                    </span>
                    <span className={CARD_NAME}>{o.label}</span>
                    {o.detail && <span className={CARD_SUB}>{o.detail}</span>}
                  </>
                ) : (
                  <>
                    <span className="min-w-0">
                      <span className="block text-[15px] leading-tight font-semibold">{o.label}</span>
                      {o.detail && <span className="mt-0.5 block text-xs text-muted">{o.detail}</span>}
                    </span>
                    {chosen && (
                      <span aria-hidden className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-leaf text-main">
                        <IconCheck width={13} height={13} />
                      </span>
                    )}
                  </>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
