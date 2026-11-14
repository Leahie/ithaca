import { describe, expect, it } from 'vitest';
import { HandEngine, onHandNav, type HandNav } from './engine';
import type { FrameSample } from './tracker';

/** An open hand: a flat palm of the given size at (x, y) of the picture, fingers straight up. */
function frame(t: number, x: number, y: number, pose: 'open' | 'point' | 'fist' = 'open'): FrameSample {
  const size = 0.2;
  const pts = Array.from({ length: 21 }, () => ({ x, y, z: 0 }));
  const set = (i: number, dx: number, dy: number) => (pts[i] = { x: x + (dx * size * 9) / 16, y: y + dy * size, z: 0 });
  set(0, 0, 0.5); // wrist
  [5, 9, 13, 17].forEach((mcp, f) => {
    const dx = (f - 1.5) * 0.25;
    set(mcp, dx, -0.5);
    const straight = pose === 'open' || (pose === 'point' && f === 0);
    set(mcp + 3, dx, straight ? -1.4 : -0.7); // fingertip: far from its knuckle, or curled in to it
  });
  set(4, -0.9, -0.2); // thumb tip, far from the index tip
  const world = pts.map((p) => ({ x: ((p.x - x) * 16) / 9 / size * 0.09, y: ((p.y - y) / size) * 0.09, z: 0 }));
  return { t, aspect: 16 / 9, hands: [{ side: 'R', image: pts, world }] } as FrameSample;
}

function run(path: (t: number) => [number, number, ('open' | 'point' | 'fist')?]): HandNav[] {
  const engine = new HandEngine();
  const seen: HandNav[] = [];
  const off = onHandNav((n) => seen.push(n));
  for (let i = 0; i <= 90; i++) {
    const t = i / 60;
    const [x, y, pose] = path(t);
    engine.ingest(frame(t, x, y, pose));
    engine.tick(t);
  }
  off();
  return seen;
}

describe('open-hand gestures', () => {
  it('a quick sweep to the right turns right, and a hand held still does nothing', () => {
    expect(run(() => [0.5, 0.5])).toEqual([]);
    const sweep = run((t) => [t < 0.5 ? 0.3 : Math.min(0.7, 0.3 + (t - 0.5) * 1.6), 0.5]);
    expect(sweep.length).toBeGreaterThan(0);
    expect(sweep.every((n) => n.type === 'turn' && n.du > 0)).toBe(true);
  });

  it('pointing walks forward step after step, and a fist walks back', () => {
    expect(run(() => [0.5, 0.5, 'point'])).toEqual([{ type: 'step', forward: true }, { type: 'step', forward: true }]);
    expect(run((t) => [0.5, 0.5, t < 1 ? 'open' : 'fist'])).toEqual([{ type: 'step', forward: false }]);
  });
});
