import type { HousePiece, LayoutVariant, Vec3 } from '@ithaca/schema';

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

/** The proposal being tried in its room: the selected layout while it is still only proposed. */
export function previewLayout(layouts: readonly LayoutVariant[] | undefined, selectedId: string | null): LayoutVariant | undefined {
  const selected = (layouts ?? []).find((l) => l.layout_id === selectedId);
  return selected?.status === 'proposed' ? selected : undefined;
}

export interface OnScreenPiece {
  item_id: string;
  room_id: string;
  position: Vec3;
  yaw_deg: number;
}

/**
 * The furniture every screen draws: what stands in the house, except that the room of a proposal
 * being tried shows the proposal instead (docs/specs/furniture.md, "What a screen draws").
 */
export function furnitureOnScreen(house: readonly HousePiece[] | undefined, preview: LayoutVariant | undefined): OnScreenPiece[] {
  const standing = (house ?? []).filter((p) => p.room_id !== preview?.room_id);
  const tried = (preview?.placements ?? []).map((p) => ({ ...p, room_id: preview!.room_id }));
  const out = new Map<string, OnScreenPiece>();
  for (const p of [...standing, ...tried]) out.set(p.item_id, { item_id: p.item_id, room_id: p.room_id, position: p.position, yaw_deg: p.yaw_deg });
  return [...out.values()];
}
