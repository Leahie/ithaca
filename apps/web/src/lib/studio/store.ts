'use client';

import { create } from 'zustand';

/**
 * The room studio (experimental): one room on its own, seen from a corner above, turned and zoomed
 * by hand. Two open hands held apart mark a spot on its floor and how wide the piece for it should
 * be; a spoken request ("put a sofa here") then finds a piece of that size and stands it there.
 * A fist held over a piece takes it out of the room.
 *
 * `useStudio` is what React draws from. `studioLive` is what changes every frame (what the hands
 * frame right now): the scene writes it, the camera panel reads it, nothing re-renders for it.
 */

/** A spot marked on the room's floor by hand. Interior frame, metres. */
export interface StudioMark {
  /** Counts marks, so the same spot marked again is still a new mark. */
  id: number;
  x: number;
  z: number;
  /** Along the line between the two hands. Null: a pinned spot with no size (one finger pointing). */
  width: number | null;
  /** Across that line. Null: whatever the piece's own depth is. */
  depth: number | null;
  /** The turn of the footprint about +Y, degrees, as furniture is turned (I-G12). */
  yaw_deg: number;
  kind: 'size' | 'pin';
}

/** What the hands frame right now, before it is held long enough to become a mark. */
export interface StudioFraming extends Omit<StudioMark, 'id'> {
  /** 0 the moment the hands come to rest, 1 when the mark locks. */
  progress: number;
}

/** The piece chosen for the mark. `url` is its model once it has been fetched (null while only its size is known). */
export interface StudioPiece {
  label: string;
  dims: { w: number; d: number; h: number };
  url: string | null;
  thumbnail: string | null;
}

/**
 * Where a spoken request is: `listening` the microphone is open; `waiting` a piece was asked for
 * and the studio waits for a mark; `searching` the library is searched; `fetching` the piece's
 * model is on its way; `placing` it is being stood in the room; `placed` it stands; `error`.
 */
export type StudioPhase = 'idle' | 'listening' | 'waiting' | 'searching' | 'fetching' | 'placing' | 'placed' | 'error';

export type StudioGesture = 'idle' | 'spin' | 'zoom' | 'frame' | 'pin' | 'remove';

interface StudioStore {
  open: boolean;
  /** The room on show; null while the studio asks which. */
  roomId: string | null;
  /** The locked mark, until a piece takes it or it is cleared. */
  mark: StudioMark | null;
  phase: StudioPhase;
  /** What the user is saying (live) or last said. */
  heard: string;
  /** The studio's (or the guide's) line back. */
  reply: string | null;
  piece: StudioPiece | null;
  /** The piece that was just stood in the room: it is the one that drops in. */
  placedId: string | null;
  enter(roomId?: string | null): void;
  pickRoom(roomId: string | null): void;
  exit(): void;
  setMark(mark: Omit<StudioMark, 'id'> | null): void;
  patch(part: Partial<Pick<StudioStore, 'phase' | 'heard' | 'reply' | 'piece' | 'placedId'>>): void;
}

let markSeq = 0;
const FRESH = { mark: null, phase: 'idle' as StudioPhase, heard: '', reply: null, piece: null, placedId: null };

export const useStudio = create<StudioStore>((set) => ({
  open: false,
  roomId: null,
  ...FRESH,
  enter: (roomId = null) => set({ open: true, roomId, ...FRESH }),
  pickRoom: (roomId) => set({ roomId, ...FRESH }),
  exit: () => {
    studioLive.framing = null;
    studioLive.spots = [];
    studioLive.gesture = 'idle';
    studioLive.aim = null;
    studioLive.gone.clear();
    set({ open: false, ...FRESH });
  },
  setMark: (mark) => set({ mark: mark ? { ...mark, id: ++markSeq } : null }),
  patch: (part) => set(part),
}));

/** Per frame, not React state. */
export const studioLive: {
  framing: StudioFraming | null;
  /** Where each hand in view lands on the room's floor. */
  spots: { side: 'L' | 'R'; x: number; z: number }[];
  gesture: StudioGesture;
  /** The piece a fist is held over, and how far the hold has come (0 to 1): at 1 it is taken out. */
  aim: { id: string; progress: number } | null;
  /** Pieces taken out by hand that the house has not yet stopped listing: they are not drawn. */
  gone: Set<string>;
} = { framing: null, spots: [], gesture: 'idle', aim: null, gone: new Set() };
