'use client';

import { create } from 'zustand';
import { HandEngine } from './engine';
import { startTracking, type Tracking } from './tracker';

export type HandsStatus = 'off' | 'starting' | 'on' | 'error';

interface HandsStore {
  status: HandsStatus;
  error: string | null;
  /** The camera picture while tracking, for the preview. */
  video: HTMLVideoElement | null;
  start(): Promise<void>;
  stop(): void;
  toggle(): void;
}

/** The one engine. The overlay reads it each frame; it is not React state. */
export const handEngine = new HandEngine();

const tickListeners = new Set<() => void>();
/** Called once per display frame while tracking, after the engine has moved. */
export function onHandTick(cb: () => void): () => void {
  tickListeners.add(cb);
  return () => tickListeners.delete(cb);
}

let tracking: Tracking | null = null;
let raf = 0;
/** Counts starts, so a stop during a slow start wins. */
let generation = 0;

function loop() {
  raf = requestAnimationFrame(loop);
  handEngine.tick(performance.now() / 1000);
  tickListeners.forEach((cb) => cb());
}

/** Hand tracking: on only when the user asks, since it opens the camera. */
export const useHands = create<HandsStore>((set, get) => ({
  status: 'off',
  error: null,
  video: null,

  async start() {
    if (get().status === 'starting' || get().status === 'on') return;
    const gen = ++generation;
    set({ status: 'starting', error: null });
    try {
      // The second argument is when the result arrived: the engine tells a hand that left from a slow tracker by it.
      const t = await startTracking((frame) => handEngine.ingest(frame, performance.now() / 1000));
      if (gen !== generation) {
        t.stop();
        return;
      }
      tracking = t;
      handEngine.reset();
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(loop);
      set({ status: 'on', video: t.video });
    } catch (err) {
      if (gen !== generation) return;
      console.warn('[hands] could not start', err);
      set({ status: 'error', error: err instanceof Error ? err.message : String(err), video: null });
    }
  },

  stop() {
    generation++;
    cancelAnimationFrame(raf);
    tracking?.stop();
    tracking = null;
    handEngine.reset();
    tickListeners.forEach((cb) => cb());
    set({ status: 'off', video: null });
  },

  toggle() {
    const s = get().status;
    if (s === 'on' || s === 'starting') get().stop();
    else void get().start();
  },
}));
