/**
 * Jitter filtering and between-frame prediction for hand positions. Ported from HoloTouch
 * (`core/filters.py`): a 1€ filter on each sample, then a velocity estimate that carries the
 * position forward between camera frames so the cursor moves at display rate, not camera rate.
 */

export type P2 = [number, number];

/** Smoothing factor of a low-pass with this cutoff (Hz) at this sample interval (s). */
function alpha(dt: number, cutoff: number): number {
  const tau = 1 / (2 * Math.PI * cutoff);
  return 1 / (1 + tau / dt);
}

/**
 * 1€ filter (Casiez, Roussel and Vogel, CHI 2012): a low-pass whose cutoff rises with speed.
 * A slow hand is smoothed down to `minCutoff` Hz (no jitter); a fast one raises the cutoff by
 * `beta` per unit of speed (no lag).
 */
export class OneEuro {
  private x: P2 | null = null;
  private dx: P2 = [0, 0];
  private t = 0;

  constructor(
    private readonly minCutoff: number,
    private readonly beta: number,
    private readonly dCutoff = 1,
  ) {}

  reset(): void {
    this.x = null;
  }

  filter(x: P2, t: number): P2 {
    if (!this.x) {
      this.x = [...x];
      this.dx = [0, 0];
      this.t = t;
      return [...x];
    }
    const dt = t - this.t;
    if (dt <= 0) return [...this.x];
    const ad = alpha(dt, this.dCutoff);
    this.dx = [this.dx[0] + ad * ((x[0] - this.x[0]) / dt - this.dx[0]), this.dx[1] + ad * ((x[1] - this.x[1]) / dt - this.dx[1])];
    const speed = Math.hypot(this.dx[0], this.dx[1]);
    const a = alpha(dt, this.minCutoff + this.beta * speed);
    this.x = [this.x[0] + a * (x[0] - this.x[0]), this.x[1] + a * (x[1] - this.x[1])];
    this.t = t;
    return [...this.x];
  }
}

export interface MotionConfig {
  minCutoff: number;
  beta: number;
  dCutoff: number;
  /** Cutoff (Hz) of the velocity estimate used for prediction. */
  velocityCutoff: number;
  /** The furthest ahead of the last sample the position is carried, in ms. */
  predictMaxMs: number;
  /** Time constant of the follow that hides the step when a new sample lands, in ms: the least it is. */
  followTauMs: number;
  /**
   * The follow's time constant as a share of the time between samples, when that is longer than
   * `followTauMs`. Samples come at the tracker's pace, not the camera's: on a slow machine that
   * is 5 to 15 a second, and a 12 ms follow then shows every sample as a jump. At 0.15 the shown
   * position has covered about half a step a tenth of the way to the next sample, and nearly all
   * of it by halfway, so it still glides. (It was 0.4: smoother, but at 10 samples a second it
   * kept the cursor about 30 ms further behind the hand, measured in a simulation of this filter.)
   */
  followShare: number;
}

/**
 * HoloTouch's defaults, for positions in camera-frame heights, made quicker where the tracker is
 * slow: `followShare` 0.15 (was 0.4) and `dCutoff` 2 (was 1: the speed the 1€ filter reads, to
 * open up for a moving hand, was itself smoothed so much that it opened late).
 */
export const MOTION_DEFAULTS: MotionConfig = { minCutoff: 1.5, beta: 12, dCutoff: 2, velocityCutoff: 5, predictMaxMs: 50, followTauMs: 12, followShare: 0.15 };

/** The follow never takes longer than this to settle, however slow the samples (ms). */
const FOLLOW_TAU_MAX_MS = 140;

/** Turns position samples (about 30 a second, fewer on a slow machine) into a smooth position at any tick time (seconds). */
export class MotionTrack {
  private readonly oneEuro: OneEuro;
  private pos: P2 | null = null;
  private vel: P2 = [0, 0];
  private tSample = 0;
  private shown: P2 | null = null;
  private tShown = 0;
  /** The usual time between samples, seconds (a guess until two have come): what the follow is paced by. */
  private interval = 1 / 30;
  private paced = false;

  constructor(private readonly cfg: MotionConfig = MOTION_DEFAULTS) {
    this.oneEuro = new OneEuro(cfg.minCutoff, cfg.beta, cfg.dCutoff);
  }

  /** Velocity, in units per second. */
  get velocity(): P2 {
    return this.vel;
  }

  get speed(): number {
    return Math.hypot(this.vel[0], this.vel[1]);
  }

  update(p: P2, t: number): void {
    const prev = this.pos;
    const prevT = this.tSample;
    const filtered = this.oneEuro.filter(p, t);
    if (prev) {
      const dt = Math.max(t - prevT, 1e-3);
      // A gap of a second or more is a pause (the hand left, the tab was hidden), not the pace.
      if (dt < 1) {
        this.interval = this.paced ? this.interval + 0.3 * (dt - this.interval) : dt;
        this.paced = true;
      }
      const a = alpha(dt, this.cfg.velocityCutoff);
      this.vel = [this.vel[0] + a * ((filtered[0] - prev[0]) / dt - this.vel[0]), this.vel[1] + a * ((filtered[1] - prev[1]) / dt - this.vel[1])];
    }
    this.pos = filtered;
    this.tSample = t;
  }

  private predict(now: number): P2 {
    const p = this.pos ?? [0, 0];
    const lead = Math.min(Math.max(now - this.tSample, 0), this.cfg.predictMaxMs / 1000);
    return [p[0] + this.vel[0] * lead, p[1] + this.vel[1] * lead];
  }

  /** The position to show at `now`. Call once per display frame. */
  step(now: number): P2 {
    const target = this.predict(now);
    if (!this.shown) {
      this.shown = [...target];
    } else {
      const dt = Math.max(now - this.tShown, 0);
      const tau = Math.min(Math.max(this.cfg.followTauMs / 1000, this.cfg.followShare * this.interval), FOLLOW_TAU_MAX_MS / 1000);
      const k = tau <= 0 ? 1 : 1 - Math.exp(-dt / tau);
      this.shown = [this.shown[0] + k * (target[0] - this.shown[0]), this.shown[1] + k * (target[1] - this.shown[1])];
    }
    this.tShown = now;
    return [...this.shown];
  }
}
