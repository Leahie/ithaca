'use client';

import { create } from 'zustand';

/** Where the tour camera is, as the Showcase reports it. */
export type TourPlace = 'inside' | 'outside' | 'floorplan' | 'moving' | null;

interface TourStore {
  place: TourPlace;
  /** Counts requests for the outside view from the room list; a repeat counts again. */
  outsideSeq: number;
  setPlace(place: TourPlace): void;
  goOutside(): void;
}

/**
 * Tour state that is not in schema v0: the outside view. `viewer.goto` names a room or a
 * sweep only, so "go outside" stays local to this tab until the schema has an event for it.
 */
export const useTour = create<TourStore>((set, get) => ({
  place: null,
  outsideSeq: 0,
  setPlace: (place) => set({ place }),
  goOutside: () => set({ outsideSeq: get().outsideSeq + 1 }),
}));
