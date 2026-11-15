/**
 * Hands to view gestures. Follows up to two hands across camera frames, smooths them, reads
 * their pinch, and once per display frame turns what they do into navigation:
 *
 * - one hand pinches and moves: drag the view, as a mouse drag would (the scene follows the hand)
 * - both hands pinch: pull them apart to zoom in, together to zoom out
 * - one open hand sweeps sideways: turn on the spot, the way the hand goes
 * - one hand points (index finger up, the others curled): walk forward, a step at a time while held
 * - one hand makes a fist: walk backward the same way
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
  /** Pointing (forward) or a fist (back), once when the pose settles and again while it is held. */
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
export const BOX = { x: 0.15, y: 0.2, w: 0.7, h: 0.6 };
/** Max palm travel between frames for a detection to be the same hand, in frame heights. */
const MATCH_GATE = 0.5;
/**
 * How long a hand may go unseen. Both are counted from when the tracker's results ARRIVE, on the
 * clock `tick` runs on, and both stretch with the time between results: a result reaches here
 * only after the tracker has worked on its frame, and on a slow machine that is 100 ms or more,
 * with results as far apart. Measured against the frame's own (capture) time and a fixed limit,
 * every hand was "not seen lately" the moment it arrived: its ring went dim, its pinch was
 * ignored, and past a quarter second it was forgotten and found again on every result.
 *
 * `stale`: it stops dragging, though it is not yet forgotten. `lost`: it is forgotten. Each is
 * the fixed time, or that many results' time when results come slower. At 30 results a second
 * these are 0.1 s and 0.25 s, as before; at 5 a second, 0.5 s and 0.8 s.
 */
const UNSEEN = { staleS: 0.1, staleResults: 2.5, lostS: 0.25, lostResults: 4 } as const;
/**
 * A forgotten hand that turns up again near where it was, within this long (s), is the same
 * hand: same id, still pinching if it was. The tracker often loses a pinching hand (its fingertips
 * hide each other) and needs a few frames to find it again; found as a new hand, it could not
 * pinch until it had been seen open, so a piece being carried fell.
 */
const REVIVE_S = 0.7;
/** Left or right is taken from several frames, not one: the tracker's own guess flips now and then. */
const SIDE_VOTES = { max: 5, flip: 3 } as const;
/** The hand settles as the fingers close: motion in the first moments of a pinch is not a drag. */
const GRAB_SETTLE_S = 0.08;

/** An open hand turning the view: only a quick, mostly sideways move counts, so bringing the hand
 * back slowly (or just holding it up) does nothing. Speeds in viewport widths per second. */
export const SWEEP = { minSpeed: 0.9, sideways: 2, armS: 0.2 } as const;
/** Walking by pose. A pose must hold `holdS` before the first step, then steps repeat. Curl is
 * fingertip-to-knuckle over palm length: a straight finger is over `straight`, a curled one under `curled`. */
export const WALK = { holdS: 0.3, repeatS: 0.95, straight: 0.85, curled: 0.62 } as const;

export type Pose = 'open' | 'point' | 'fist';

/** What the hand is doing, from how far each fingertip (index..pinky) is from its knuckle. */
export function poseOf(curl: number[]): Pose {
  const [index = 1, ...rest] = curl;
  if (rest.every((c) => c < WALK.curled)) return index > WALK.straight ? 'point' : index < WALK.curled ? 'fist' : 'open';
  return 'open';
}

interface Track {
  id: number;
  side: Side;
  /** Recent frames' votes for the side: positive is right, negative left (see SIDE_VOTES). */
  sideVotes: number;
  motion: MotionTrack;
  pinch: PinchTracker;
  features: HandFeatures;
  sample: HandSample;
  /** When the last result that held this hand arrived (the tick clock, not the frame's time). */
  lastSeen: number;
  /** Shown palm position, frame heights, at the last tick. */
  pos: P2 | null;
  /** Position at the last tick it was dragging from, or null. */
  dragFrom: P2 | null;
  /** Position and time at the last tick, for the sweep. */
  last: { pos: P2; t: number } | null;
  /** The pose, since when, and when it next steps. */
  pose: Pose;
  poseSince: number;
  nextStep: number;
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
  /** Open, pointing (index finger out, the others curled) or a fist. */
  pose: Pose;
}

export type Gesture = 'none' | 'drag' | 'zoom' | 'turn' | 'forward' | 'back';

/**
 * How the tracking is keeping up, for the readout in the camera box (running averages).
 * `rate`: results a second. `modelMs`: the hand model's time per frame (0 until known).
 * `delayMs`: from the picture to its result reaching this page. Where the worker reads the camera
 * itself, the picture's time is when the worker took it, so the camera's own buffering (about one
 * frame) is not in it. The cursor's smoothing comes on top of this. `gpuMs`: how long a tiny
 * WebGL read waits on the graphics card's queue (the worker measures it now and then).
 */
export interface HandStats {
  rate: number;
  modelMs: number;
  delayMs: number;
  gpuMs: number;
  /** What runs the hand model ('GPU', 'CPU'), or '' while not known. */
  on: string;
}
/** Weight of each new result in the running averages. */
const STATS_WEIGHT = 0.1;

export class HandEngine {
  private tracks: Track[] = [];
  /** Hands forgotten lately, that may come back (see REVIVE_S). */
  private gone: Track[] = [];
  private nextId = 1;
  private aspect = 16 / 9;
  private spread: number | null = null;
  /** The usual time between the tracker's results, seconds (a guess until two have come), and when the last one arrived. */
  private interval = 1 / 30;
  private paced = false;
  private lastArrival: number | null = null;
  gesture: Gesture = 'none';
  hands: HandView[] = [];
  /** Counts the results taken in: the hands' landmarks change only when this does. */
  results = 0;
  stats: HandStats = { rate: 0, modelMs: 0, delayMs: 0, gpuMs: 0, on: '' };

  /** A hand unseen for longer than this stops dragging (see UNSEEN). */
  private get staleAfter(): number {
    return Math.max(UNSEEN.staleS, UNSEEN.staleResults * this.interval);
  }

  /** A hand unseen for longer than this is forgotten. */
  private get lostAfter(): number {
    return Math.max(UNSEEN.lostS, UNSEEN.lostResults * this.interval);
  }

  /**
   * One result from the tracker. `frame.t` is when its picture was taken: the motion filter and
   * the pinch timing run on that. `arrived` is when the result got here, on the clock `tick` is
   * called with: whether a hand is still in view is judged from that.
   */
  ingest(frame: FrameSample, arrived: number = frame.t): void {
    this.aspect = frame.aspect;
    const t = frame.t;
    if (this.lastArrival !== null) {
      const dt = arrived - this.lastArrival;
      // A gap of a second or more is a pause (a hidden tab, the camera stalled), not the tracker's pace.
      if (dt > 0 && dt < 1) {
        // The first gap is taken as it is: a slow tracker must not look like a hand going away
        // while the average is still on its way up from the guess.
        this.interval = this.paced ? this.interval + 0.25 * (dt - this.interval) : Math.max(dt, this.interval);
        this.paced = true;
      }
    }
    this.lastArrival = arrived;
    this.results++;
    const avg = (was: number, now: number) => (this.results === 1 || was === 0 ? now : was + STATS_WEIGHT * (now - was));
    this.stats = {
      rate: this.paced ? 1 / this.interval : 0,
      modelMs: frame.ms === undefined ? this.stats.modelMs : avg(this.stats.modelMs, frame.ms),
      delayMs: avg(this.stats.delayMs, Math.max(0, (arrived - t) * 1000)),
      // Few samples (two a second): weighted more, so it still follows a change within seconds.
      gpuMs: frame.gpuMs === undefined ? this.stats.gpuMs : this.stats.gpuMs === 0 ? frame.gpuMs : this.stats.gpuMs + 0.4 * (frame.gpuMs - this.stats.gpuMs),
      on: frame.on ?? this.stats.on,
    };
    // Between results a hand travels further when they are far apart: the gate widens with them.
    const gate = MATCH_GATE * Math.min(3, Math.max(1, this.interval * 15));
    const features = frame.hands.map((h) => extractFeatures(h.image, h.world, frame.aspect));
    // Nearest first: each detection goes to the closest unclaimed hand within the gate. A
    // matching side is preferred, since MediaPipe's left/right is right far more often than not.
    const pairs: { d: number; ti: number; di: number }[] = [];
    this.tracks.forEach((tr, ti) =>
      features.forEach((f, di) => {
        const ref = tr.pos ?? tr.features.palm;
        const d = Math.hypot(f.palm[0] - ref[0], f.palm[1] - ref[1]) + (frame.hands[di]!.side === tr.side ? 0 : 0.15);
        if (d < gate) pairs.push({ d, ti, di });
      }),
    );
    pairs.sort((a, b) => a.d - b.d);
    const usedT = new Set<number>();
    const usedD = new Set<number>();
    for (const { ti, di } of pairs) {
      if (usedT.has(ti) || usedD.has(di)) continue;
      usedT.add(ti);
      usedD.add(di);
      this.feed(this.tracks[ti]!, frame.hands[di]!, features[di]!, t, arrived);
    }
    features.forEach((f, di) => {
      if (usedD.has(di) || this.tracks.length >= 2) return;
      const hand = frame.hands[di]!;
      const back = this.revive(f, arrived, gate);
      if (back) {
        back.dragFrom = null; // where it went while unseen is not a drag
        this.feed(back, hand, f, t, arrived);
        this.tracks.push(back);
        return;
      }
      const track: Track = {
        id: this.nextId++,
        side: hand.side,
        sideVotes: 0,
        motion: new MotionTrack(),
        pinch: new PinchTracker(t),
        features: f,
        sample: hand,
        lastSeen: arrived,
        pos: null,
        dragFrom: null,
        last: null,
        pose: 'open',
        poseSince: t,
        nextStep: 0,
      };
      this.feed(track, hand, f, t, arrived);
      this.tracks.push(track);
    });
    this.forget(arrived);
  }

  /** Hands unseen for too long are forgotten, and kept a moment in case they come back. */
  private forget(now: number): void {
    const lost = this.lostAfter;
    for (const tr of this.tracks) if (now - tr.lastSeen > lost) this.gone.push(tr);
    this.tracks = this.tracks.filter((tr) => now - tr.lastSeen <= lost);
    this.gone = this.gone.filter((tr) => now - tr.lastSeen <= REVIVE_S);
  }

  /** The hand forgotten lately nearest this new one, if it is near enough to be the same; taken out of `gone`. */
  private revive(f: HandFeatures, now: number, gate: number): Track | null {
    let best: Track | null = null;
    let bestD = gate;
    for (const tr of this.gone) {
      if (now - tr.lastSeen > REVIVE_S) continue;
      const ref = tr.pos ?? tr.features.palm;
      const d = Math.hypot(f.palm[0] - ref[0], f.palm[1] - ref[1]);
      if (d < bestD) {
        bestD = d;
        best = tr;
      }
    }
    if (best) this.gone = this.gone.filter((tr) => tr !== best);
    return best;
  }

  private feed(tr: Track, sample: HandSample, f: HandFeatures, t: number, arrived: number): void {
    tr.sample = sample;
    tr.features = f;
    tr.lastSeen = arrived;
    // The side changes only when the last few frames agree on the other one: one odd frame must
    // not turn the ring from blue to coral and back.
    tr.sideVotes = Math.min(Math.max(tr.sideVotes + (sample.side === 'R' ? 1 : -1), -SIDE_VOTES.max), SIDE_VOTES.max);
    if (tr.sideVotes >= SIDE_VOTES.flip) tr.side = 'R';
    else if (tr.sideVotes <= -SIDE_VOTES.flip) tr.side = 'L';
    const pose = poseOf(f.curl);
    if (pose !== tr.pose) {
      tr.pose = pose;
      tr.poseSince = t;
      tr.nextStep = t + WALK.holdS;
    }
    tr.motion.update(f.palm, t);
    tr.pinch.update(f, t, tr.motion.speed);
  }

  /** Once per display frame: move the cursors and emit navigation. `now` in seconds. */
  tick(now: number): void {
    const { staleAfter } = this;
    this.forget(now);
    for (const tr of this.tracks) tr.pos = tr.motion.step(now);

    const pinching = this.tracks.filter((tr) => tr.pinch.pinching && now - tr.lastSeen <= staleAfter);
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
        this.gesture = this.freeHand(now);
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
        stale: now - tr.lastSeen > staleAfter,
        landmarks: tr.sample.image,
        pose: tr.pose,
      };
    });
  }

  /** One hand in view, not pinching: a sideways sweep turns, pointing or a fist walks. */
  private freeHand(now: number): Gesture {
    const fresh = this.tracks.filter((t) => now - t.lastSeen <= this.staleAfter);
    const tr = fresh[0];
    // Two hands up are getting ready to zoom; a hand that just let go of a pinch is still settling.
    if (fresh.length !== 1 || !tr || !tr.pos || !tr.last || tr.pinch.pinching || now - tr.pinch.since < SWEEP.armS) return 'none';
    const dt = now - tr.last.t;
    if (dt > 0 && dt <= 0.1) {
      const du = (tr.pos[0] - tr.last.pos[0]) / (BOX.w * this.aspect);
      const dv = (tr.pos[1] - tr.last.pos[1]) / BOX.h;
      if (Math.abs(du) / dt >= SWEEP.minSpeed && Math.abs(du) >= SWEEP.sideways * Math.abs(dv)) {
        emit({ type: 'turn', du });
        tr.nextStep = Math.max(tr.nextStep, now + WALK.holdS); // turn first, then walk on
        return 'turn';
      }
    }
    if (tr.pose === 'open') return 'none';
    if (now >= tr.nextStep) {
      emit({ type: 'step', forward: tr.pose === 'point' });
      tr.nextStep = now + WALK.repeatS;
    }
    return tr.pose === 'point' ? 'forward' : 'back';
  }

  reset(): void {
    this.tracks = [];
    this.gone = [];
    this.hands = [];
    this.spread = null;
    this.gesture = 'none';
    this.interval = 1 / 30;
    this.paced = false;
    this.lastArrival = null;
    this.results = 0;
    this.stats = { rate: 0, modelMs: 0, delayMs: 0, gpuMs: 0, on: '' };
  }
}
