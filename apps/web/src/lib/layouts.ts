import type { LayoutVariant } from '@ithaca/schema';

export interface VisibleLayouts {
  variants: LayoutVariant[];
  /** Why these are shown: the agent picked them, they belong to the room in view, or nothing narrower matched. */
  scope: 'agent' | 'room' | 'all';
}

/**
 * Which layout variants to show. Desktop and phone use the same rule, so they always agree:
 * what the agent chose to show, else the room in view, else every layout for the home.
 */
export function visibleLayouts(all: LayoutVariant[], shownIds: string[], roomId: string | null): VisibleLayouts {
  const fromAgent = all.filter((l) => shownIds.includes(l.layout_id));
  if (fromAgent.length) return { variants: fromAgent, scope: 'agent' };
  const inRoom = roomId ? all.filter((l) => l.room_id === roomId) : [];
  if (inRoom.length) return { variants: inRoom, scope: 'room' };
  return { variants: all, scope: 'all' };
}
