/**
 * Hands to view gestures. Follows up to two hands across camera frames, smooths them, reads
 * their pinch, and once per display frame turns what they do into navigation:
 *
 * - one hand pinches and moves: drag the view, as a mouse drag would (the scene follows the hand)
 * - both hands pinch: pull them apart to zoom in, together to zoom out
 * - one open hand sweeps sideways: turn on the spot, the way the hand goes
 * - one open hand pushes toward the camera: one step forward; pulls away: one step back
 *
 * Views listen with `onHandNav`. The cursor overlay reads `HandEngine.hands`.
 */

import { MotionTrack, type P2 } from './filters';
import { PinchTracker, extractFeatures, type HandFeatures } from './pinch';
import type { FrameSample, HandSample, Side } from './tracker';

export type HandNav =
  /** Viewport fractions the pinching hand moved: du right, dv down. */
  | { type: 'drag'; du: number; dv: number }
  /** Ratio of the hands' spread now to a frame ago: over 1 is apart (zoom in). */
  | { type: 'zoom'; factor: number }
  /** An open hand swept sideways: viewport fractions it moved, positive to the right. */
  | { type: 'turn'; du: number }
  /** An open hand pushed toward the camera (forward) or pulled away from it. */
  | { type: 'step'; forward: boolean };

const navListeners = new Set<(nav: HandNav) => void>();

/** Hand gestures meant for the active view. Returns an unsubscribe function. */
export function onHandNav(cb: (nav: HandNav) => void): () => void {
  navListeners.add(cb);
  return () => navListeners.delete(cb);
}

const emit = (nav: HandNav) => navListeners.forEach((cb) => cb(nav));

/**
 * The part of the camera picture that maps to the whole viewport, as fractions of the frame
 * (HoloTouch's defaults): the hand never has to reach the edge of the picture.
 */
const BOX = { x: 0.15, y: 0.2, w: 0.7, h: 0.6 };
/** Max palm travel between frames for a detection to be the same hand, in frame heights. */
const MATCH_GATE = 0.5;
/** A hand not seen for this long is forgotten. */
const LOST_GRACE_S = 0.25;
/** A hand not seen for this long stops dragging, though it is not yet forgotten. */
const STALE_S = 0.1;
/** The hand settles as the fingers close: motion in the first moments of a pinch is not a drag. */
const GRAB_SETTLE_S = 0.08;

/** An open hand turning the view: only a quick, mostly sideways move counts, so bringing the hand
 * back slowly (or just holding it up) does nothing. Speeds in viewport widths per second. */
export const SWEEP = { minSpeed: 0.9, sideways: 2, armS: 0.2 } as const;
/** Walking: the hand's size in the picture against where it has been resting. Pushing it toward
 * the camera makes it bigger. One step per push, then a pause in which the hand comes back. */
export const PUSH = { forward: 1.2, back: 0.84, restS: 0.9, cooldownS: 0.9, maxSpeed: 1.2 } as const;

/** The palm's size in the picture, in frame heights (as in pinch.ts: length, or width scaled to match). */
function palmSize(image: HandSample['image'], aspect: number): number {
  const d = (a: number, b: number) => Math.hypot((image[a]!.x - image[b]!.x) * aspect, image[a]!.y - image[b]!.y);
  return Math.max(d(9, 0), 1.25 * d(5, 17));
}

interface Track {
  id: number;
  side: Side;
  motion: MotionTrack;
  pinch: PinchTracker;
  features: HandFeatures;
  sample: HandSample;
  lastSeen: number;
  /** Shown palm position, frame heights, at the last tick. */
  pos: P2 | null;
  /** Position at the last tick it was dragging from, or null. */
  dragFrom: P2 | null;
  /** Open-hand gestures: position and time at the last tick, palm size now and at rest, and
   * until when a step just taken keeps the next one from firing. */
  last: { pos: P2; t: number } | null;
  size: number;
  rest: number;
  quietUntil: number;
}

/** What the overlay draws for one hand. */
export interface HandView {
  id: number;
  side: Side;
  /** Viewport fractions; may run a little past 0..1. */
  u: number;
  v: number;
  /** 0 open .. 1 fully pinched. */
  strength: number;
  pinching: boolean;
  /** Not seen in the latest frame (coasting). */
  stale: boolean;
  /** Mirrored image landmarks, 0..1 of the frame. */
  landmarks: HandSample['image'];
}

export type Gesture = 'none' | 'drag' | 'zoom' | 'turn';

export class HandEngine {
  private tracks: Track[] = [];
  private nextId = 1;
  private aspect = 16 / 9;
  private spread: number | null = null;
  gesture: Gesture = 'none';
  hands: HandView[] = [];

  ingest(frame: FrameSample): void {
    this.aspect = frame.aspect;
    const t = frame.t;
    const features = frame.hands.map((h) => extractFeatures(h.image, h.world, frame.aspect));
    // Nearest first: each detection goes to the closest unclaimed hand within the gate. A
    // matching side is preferred, since MediaPipe's left/right is right far more often than not.
    const pairs: { d: number; ti: number; di: number }[] = [];
    this.tracks.forEach((tr, ti) =>
      features.forEach((f, di) => {
        const ref = tr.pos ?? tr.features.palm;
        const d = Math.hypot(f.palm[0] - ref[0], f.palm[1] - ref[1]) + (frame.hands[di]!.side === tr.side ? 0 : 0.15);
        if (d < MATCH_GATE) pairs.push({ d, ti, di });
      }),
    );
    pairs.sort((a, b) => a.d - b.d);
    const usedT = new Set<number>();
    const usedD = new Set<number>();
    for (const { ti, di } of pairs) {
      if (usedT.has(ti) || usedD.has(di)) continue;
      usedT.add(ti);
      usedD.add(di);
      this.feed(this.tracks[ti]!, frame.hands[di]!, features[di]!, t);
    }
    features.forEach((f, di) => {
      if (usedD.has(di) || this.tracks.length >= 2) return;
      const hand = frame.hands[di]!;
      const track: Track = {
        id: this.nextId++,
        side: hand.side,
        motion: new MotionTrack(),
        pinch: new PinchTracker(t),
        features: f,
        sample: hand,
        lastSeen: t,
        pos: null,
        dragFrom: null,
        last: null,
        size: 0,
        rest: 0,
        quietUntil: 0,
      };
      this.feed(track, hand, f, t);
      this.tracks.push(track);
    });
    this.tracks = this.tracks.filter((tr) => t - tr.lastSeen <= LOST_GRACE_S);
  }

  private feed(tr: Track, sample: HandSample, f: HandFeatures, t: number): void {
    tr.sample = sample;
    tr.features = f;
    tr.lastSeen = t;
    tr.side = sample.side;
    const size = palmSize(sample.image, this.aspect);
    tr.size = tr.size ? tr.size + (size - tr.size) * 0.5 : size;
    tr.motion.update(f.palm, t);
    tr.pinch.update(f, t, tr.motion.speed);
  }

  /** Once per display frame: move the cursors and emit navigation. `now` in seconds. */
  tick(now: number): void {
    this.tracks = this.tracks.filter((tr) => now - tr.lastSeen <= LOST_GRACE_S);
    for (const tr of this.tracks) tr.pos = tr.motion.step(now);

    const pinching = this.tracks.filter((tr) => tr.pinch.pinching && now - tr.lastSeen <= STALE_S);
    for (const tr of this.tracks) if (!pinching.includes(tr)) tr.dragFrom = null;

    if (pinching.length === 2) {
      const [a, b] = pinching as [Track, Track];
      const spread = Math.hypot(a.pos![0] - b.pos![0], a.pos![1] - b.pos![1]);
      if (this.spread !== null && this.spread > 1e-3) {
        const factor = spread / this.spread;
        if (Math.abs(factor - 1) > 1e-4) emit({ type: 'zoom', factor });
      }
      this.spread = spread;
      // Letting one hand go mid-zoom must not jump: it drags from where it is then.
      a.dragFrom = null;
      b.dragFrom = null;
      this.gesture = 'zoom';
    } else {
      this.spread = null;
      const tr = pinching[0];
      if (tr) {
        const pos = tr.pos!;
        const settled = now - tr.pinch.since >= GRAB_SETTLE_S;
        if (tr.dragFrom && settled) {
          const du = (pos[0] - tr.dragFrom[0]) / (BOX.w * this.aspect);
          const dv = (pos[1] - tr.dragFrom[1]) / BOX.h;
          if (du !== 0 || dv !== 0) emit({ type: 'drag', du, dv });
        }
        tr.dragFrom = pos;
        this.gesture = 'drag';
      } else {
        this.gesture = this.openHand(now) ? 'turn' : 'none';
      }
    }
    for (const t of this.tracks) t.last = t.pos ? { pos: t.pos, t: now } : null;

    this.hands = this.tracks.map((tr) => {
      const p = tr.pos ?? tr.features.palm;
      return {
        id: tr.id,
        side: tr.side,
        u: (p[0] / this.aspect - BOX.x) / BOX.w,
        v: (p[1] - BOX.y) / BOX.h,
        strength: PinchTracker.strength(tr.features),
        pinching: tr.pinch.pinching,
        stale: now - tr.lastSeen > STALE_S,
        landmarks: tr.sample.image,
      };
    });
  }

  /** One open hand in view: a sideways sweep turns, a push or pull steps. True while turning. */
  private openHand(now: number): boolean {
    const fresh = this.tracks.filter((t) => now - t.lastSeen <= STALE_S);
    const tr = fresh[0];
    // Two hands up are getting ready to zoom; a hand that just let go of a pinch is still settling.
    if (fresh.length !== 1 || !tr || !tr.pos || !tr.last || tr.pinch.pinching || now - tr.pinch.since < SWEEP.armS) {
      for (const t of fresh) t.rest = t.size;
      return false;
    }
    const dt = now - tr.last.t;
    if (dt <= 0 || dt > 0.1) return false;
    const du = (tr.pos[0] - tr.last.pos[0]) / (BOX.w * this.aspect);
    const dv = (tr.pos[1] - tr.last.pos[1]) / BOX.h;
    const speed = Math.abs(du) / dt;
    if (speed >= SWEEP.minSpeed && Math.abs(du) >= SWEEP.sideways * Math.abs(dv)) {
      emit({ type: 'turn', du });
      tr.rest = tr.size; // a sweep also moves the hand nearer or farther: that is not a push
      return true;
    }
    if (!tr.rest || now < tr.quietUntil || speed > PUSH.maxSpeed) {
      tr.rest = tr.size;
      return false;
    }
    const ratio = tr.size / tr.rest;
    if (ratio >= PUSH.forward || ratio <= PUSH.back) {
      emit({ type: 'step', forward: ratio > 1 });
      tr.quietUntil = now + PUSH.cooldownS; // the hand comes back during the pause
      tr.rest = tr.size;
    } else {
      tr.rest += (tr.size - tr.rest) * Math.min(1, dt / PUSH.restS); // drift with a hand held still
    }
    return false;
  }

  reset(): void {
    this.tracks = [];
    this.hands = [];
    this.spread = null;
    this.gesture = 'none';
  }
}
