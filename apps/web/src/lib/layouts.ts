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


/**
 * The layouts whose furniture is drawn in the house: the selected one, plus the chosen layout of
 * every other room. So furniture stays where the user put it when they walk on to the next room.
 */
export function shownLayouts(layouts: readonly LayoutVariant[] | undefined, selectedId: string | null): LayoutVariant[] {
  const all = layouts ?? [];
  const selected = all.find((l) => l.layout_id === selectedId);
  const rest = all.filter((l) => l.status === 'chosen' && l.layout_id !== selectedId && l.room_id !== selected?.room_id);
  return selected ? [selected, ...rest] : rest;
}

/** The layout to work on in a room: the chosen one, else the most recent. Lists come newest first. */
export function layoutForRoom(layouts: readonly LayoutVariant[] | undefined, roomId: string | null): LayoutVariant | undefined {
  const inRoom = (layouts ?? []).filter((l) => l.room_id === roomId);
  return inRoom.find((l) => l.status === 'chosen') ?? inRoom[0];
}
