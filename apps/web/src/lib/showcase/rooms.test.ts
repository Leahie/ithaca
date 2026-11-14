import { describe, expect, it } from 'vitest';
import { aerialView, rotationToward } from './rooms';

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

describe('aerialView', () => {
  const sofa = { x: -2, y: 0, z: -6.5 };

  it('looks down at the target from above, facing -Z like the phone plan', () => {
    const { position, rotation } = aerialView(sofa, 10);
    expect(rotation.y).toBe(0);
    expect(rotation.x).toBeLessThan(-45);
    expect(position.x).toBeCloseTo(sofa.x);
    expect(position.y).toBeGreaterThan(sofa.y + 4);
    expect(position.z).toBeGreaterThan(sofa.z);
    // The camera's pitch points straight at the target.
    const down = (Math.atan2(position.y - sofa.y, position.z - sofa.z) * 180) / Math.PI;
    expect(-down).toBeCloseTo(rotation.x);
  });

  it('stands further back in a bigger room, within limits', () => {
    const away = (span: number) => {
      const { position } = aerialView(sofa, span);
      return Math.hypot(position.y - sofa.y, position.z - sofa.z);
    };
    expect(away(2)).toBeCloseTo(5.5);
    expect(away(12)).toBeGreaterThan(away(10));
    expect(away(40)).toBeCloseTo(9);
  });
});
