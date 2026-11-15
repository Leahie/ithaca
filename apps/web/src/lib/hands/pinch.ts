/**
 * Thumb + index pinch, read the way HoloTouch reads it (`core/poses.py`): distances in palm
 * lengths so they do not change with how far the hand is from the camera, separate thresholds
 * to enter and to leave, and a short time a reading must last before it counts.
 */

import type { P2 } from './filters';

/** One MediaPipe landmark: image ones are 0..1 of the frame (z relative), world ones are metres. */
export interface Pt {
  x: number;
  y: number;
  z: number;
}

export const WRIST = 0;
export const THUMB_TIP = 4;
export const INDEX_MCP = 5;
export const INDEX_TIP = 8;
export const MIDDLE_MCP = 9;
export const PINKY_MCP = 17;
/** mcp, pip, dip, tip of index..pinky. */
const FINGERS = [
  [5, 6, 7, 8],
  [9, 10, 11, 12],
  [13, 14, 15, 16],
  [17, 18, 19, 20],
] as const;

export const PINCH = {
  /**
   * Thumb tip to index tip, in palm lengths: under this a pinch starts, over `exit` it ends. The
   * landmarks sit at the very ends of the fingers, so tips touching pad to pad are still a fifth
   * to a third of a palm apart: at 0.25 many real pinches were never seen.
   */
  enter: 0.33,
  exit: 0.5,
  onMs: 30,
  offMs: 60,
  /**
   * An index fingertip this near its knuckle (palm lengths) is tucked into the palm: a fist, not a
   * pinch. Only the index is asked: a pinch reaches it out to the thumb, while the other fingers
   * often curl in. (Asking every finger made a natural pinch, with the others curled, a "fist".)
   */
  fist: 0.42,
  /** A pinch that starts while the hand moves faster than this (frame heights / s) is ignored. */
  maxOnsetSpeed: 4,
  /** A hand must be in view, not pinching, this long before it can pinch. */
  armMs: 100,
  /** The same, after letting go: dragging the view round is a run of pinches. */
  regrabMs: 60,
  /** Thumb and index this far apart in depth (palm lengths) are not touching, whatever the picture shows. */
  maxDepthGap: 0.45,
} as const;

const dist = (a: Pt, b: Pt) => Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);

export interface HandFeatures {
  /** Palm centre in the (mirrored) picture, in frame heights: x in 0..aspect, y in 0..1. */
  palm: P2;
  /** Thumb tip to index tip, palm lengths: the smaller of the metric and the picture measure. */
  pinch: number;
  /** Fingertip-to-knuckle reach over palm length, index..pinky. */
  curl: number[];
}

/** Thumb tip to index tip as the picture shows them, in palm lengths; Infinity when far apart in depth. */
function picturePinch(image: Pt[], aspect: number): number {
  const p = (i: number): P2 => [image[i]!.x * aspect, image[i]!.y];
  const d = (a: P2, b: P2) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  // The palm's length or, scaled to match, its width: whichever a turned hand has not foreshortened.
  const palm = Math.max(d(p(MIDDLE_MCP), p(WRIST)), 1.25 * d(p(INDEX_MCP), p(PINKY_MCP)), 1e-6);
  const depth = (Math.abs(image[THUMB_TIP]!.z - image[INDEX_TIP]!.z) * aspect) / palm;
  if (depth > PINCH.maxDepthGap) return Infinity;
  return d(p(THUMB_TIP), p(INDEX_TIP)) / palm;
}

export function extractFeatures(image: Pt[], world: Pt[], aspect: number): HandFeatures {
  const palmLen = Math.max(dist(world[MIDDLE_MCP]!, world[WRIST]!), 1e-4);
  const curl = FINGERS.map(([mcp, , , tip]) => dist(world[tip]!, world[mcp]!) / palmLen);
  const palm: P2 = [
    ((image[WRIST]!.x + image[INDEX_MCP]!.x + image[PINKY_MCP]!.x) / 3) * aspect,
    (image[WRIST]!.y + image[INDEX_MCP]!.y + image[PINKY_MCP]!.y) / 3,
  ];
  const pinch = Math.min(dist(world[THUMB_TIP]!, world[INDEX_TIP]!) / palmLen, picturePinch(image, aspect));
  return { palm, pinch, curl };
}

/** Pinch state of one hand, with hysteresis, debounce and the onset guards. */
export class PinchTracker {
  pinching = false;
  /** When the current state became stable, seconds. */
  since: number;
  private candidate = false;
  private candidateSince = 0;
  private hasPinched = false;

  constructor(t: number) {
    this.since = t;
  }

  /** `speed` is the hand's speed in frame heights per second. */
  update(f: HandFeatures, t: number, speed: number): boolean {
    const fist = (f.curl[0] ?? 1) < PINCH.fist;
    const reading = !fist && f.pinch < (this.pinching ? PINCH.exit : PINCH.enter);
    if (reading === this.pinching) {
      this.candidate = reading;
      return this.pinching;
    }
    if (reading !== this.candidate) {
      this.candidate = reading;
      this.candidateSince = t;
    }
    if ((t - this.candidateSince) * 1000 < (reading ? PINCH.onMs : PINCH.offMs)) return this.pinching;
    if (reading) {
      // A pinch only counts from a hand that has been in view and open for a moment, and is not
      // swinging past: a waving hand is often misread as pinching for a frame or two. A hand that
      // has just let go may take hold again sooner.
      const armMs = this.hasPinched ? PINCH.regrabMs : PINCH.armMs;
      if ((this.candidateSince - this.since) * 1000 < armMs || speed > PINCH.maxOnsetSpeed) return this.pinching;
      this.hasPinched = true;
    }
    this.pinching = reading;
    this.since = t;
    return this.pinching;
  }

  /** 0 with the fingers well apart, 1 fully closed: how tight to draw the cursor ring. */
  static strength(f: HandFeatures): number {
    const closed = PINCH.enter * 0.5;
    const open = PINCH.exit * 1.6;
    return Math.min(Math.max((open - f.pinch) / (open - closed), 0), 1);
  }
}
