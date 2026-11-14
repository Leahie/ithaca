import { describe, expect, it } from 'vitest';
import { rotationToward } from './rooms';

describe('rotationToward', () => {
  const eye = { x: 0, y: 1.5, z: 0 };

  it('follows the Showcase camera convention: yaw 0 looks along -Z, positive yaw turns left', () => {
    expect(rotationToward(eye, { x: 0, y: 1.5, z: -3 }).y).toBeCloseTo(0);
    expect(rotationToward(eye, { x: -3, y: 1.5, z: 0 }).y).toBeCloseTo(90);
    expect(rotationToward(eye, { x: 3, y: 1.5, z: 0 }).y).toBeCloseTo(-90);
    expect(Math.abs(rotationToward(eye, { x: 0, y: 1.5, z: 3 }).y)).toBeCloseTo(180);
  });

  it('looks slightly down at a point below eye height, and never steeply', () => {
    expect(rotationToward(eye, { x: 0, y: 1.25, z: -3 }).x).toBeLessThan(0);
    expect(rotationToward(eye, { x: 0, y: -50, z: -1 }).x).toBe(-35);
  });
});
