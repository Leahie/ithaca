'use client';

import { useEffect, useMemo, useState } from 'react';
import { CatalogSuggestion } from '@/components/house/CatalogSuggestion';
import { ChoiceCards } from '@/components/house/ChoiceCards';
import { LayoutSuggestion } from '@/components/house/LayoutSuggestion';
import { MODAL_BACKDROP, useScrollLock } from '@/components/ui';
import { IconClose } from '@/components/ui/icons';
import type { ChatItem } from '@/lib/store/reducer';
import { useSession } from '@/lib/store/session';

type Shown = Extract<ChatItem, { kind: 'choice' | 'catalog' | 'layouts' }>;

/**
 * What the guide has put up for this person and they have not answered yet: the newest of its
 * picture cards, catalog pieces or layout options. Anything they said afterwards (typed, spoken
 * or tapped) is their answer, so nothing older than that is still open.
 */
export function openForMe(chat: ChatItem[], memberId: string | null): Shown | null {
  for (let i = chat.length - 1; i >= 0; i--) {
    const c = chat[i]!;
    // In a family the phone shows its own guide only, as the Guide screen does.
    if (memberId && 'member_id' in c && c.member_id !== memberId) continue;
    if (c.kind === 'user') return null;
    if (c.kind === 'choice') return c.picked === null ? c : null;
    if (c.kind === 'catalog' || c.kind === 'layouts') return c;
  }
  return null;
}

/**
 * The phone's pop-up for the guide's questions. Whatever screen the phone is on, when the guide
 * asks the person to pick something (a room, a look, a piece, a layout), the options come up
 * over it as pictures to tap, so nobody has to open the chat to answer. Tapping an answer, or
 * the cross, closes it; the cards stay in the Guide screen's conversation.
 */
export function GuidePopup({ propertyId, memberId, arranging }: { propertyId: string; memberId: string | null; /** The phone is on the furniture screen, which shows layout options itself. */ arranging: boolean }) {
  const chat = useSession((s) => s.chat);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const open = useMemo(() => openForMe(chat, memberId), [chat, memberId]);
  const shown = open && open.id !== dismissed && !(open.kind === 'layouts' && arranging) ? open : null;
  useScrollLock(shown !== null);

  // Escape closes it, as it closes every modal.
  useEffect(() => {
    if (!shown) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDismissed(shown.id);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [shown]);

  if (!shown) return null;
  const close = () => setDismissed(shown.id);
  const title =
    shown.kind === 'choice'
      ? shown.question
      : shown.kind === 'catalog'
        ? 'Tap a piece to add it to the room'
        : `${shown.layout_ids.length} way${shown.layout_ids.length === 1 ? '' : 's'} to lay out the room`;

  return (
    <div className={MODAL_BACKDROP} data-testid="guide-popup" data-kind={shown.kind} onClick={close}>
      <section
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="guide-popup flex w-full max-w-md flex-col overflow-hidden rounded-3xl border border-line bg-surface shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-start gap-3 px-4 pt-4 pb-2">
          <h2 className="min-w-0 flex-1 font-display text-lg leading-snug font-semibold">{title}</h2>
          <button type="button" aria-label="Close" onClick={close} className="-mt-1 -mr-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-line bg-raised text-text active:scale-95">
            <IconClose width={16} height={16} />
          </button>
        </header>
        {/* The max-height is on the scroller itself: mobile Safari won't shrink a flex child to fit one. */}
        <div className="max-h-[68dvh] touch-pan-y overflow-y-auto overscroll-contain px-4 pt-1 pb-4">
          {shown.kind === 'choice' ? (
            <ChoiceCards item={shown} propertyId={propertyId} question={false} />
          ) : shown.kind === 'catalog' ? (
            <CatalogSuggestion propertyId={shown.property_id} roomId={shown.room_id} nearItemId={shown.near_item_id} pieces={shown.pieces} onAdded={close} />
          ) : (
            <LayoutSuggestion layoutIds={shown.layout_ids} onKept={close} />
          )}
        </div>
      </section>
    </div>
  );
}
