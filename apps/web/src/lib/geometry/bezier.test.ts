import { expect, test } from 'vitest';
import { cubicBezier } from './bezier';

test('an ease-out bezier starts fast, ends at 1 and never goes backwards', () => {
  const ease = cubicBezier(0.22, 0.8, 0.3, 1);
  expect(ease(0)).toBe(0);
  expect(ease(1)).toBe(1);
  expect(ease(0.25)).toBeGreaterThan(0.5);
  let last = 0;
  for (let t = 0; t <= 1; t += 0.05) {
    expect(ease(t)).toBeGreaterThanOrEqual(last);
    last = ease(t);
  }
  expect(cubicBezier(0, 0, 1, 1)(0.4)).toBeCloseTo(0.4, 3); // the straight line
});
