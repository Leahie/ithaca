/**
 * Walking through the rebuilt house (ported from 3d_rendering_matterport/src/model3d.ts): the
 * eye stays at eye height above whatever floor is under it, stairs included, and stops 30 cm
 * before walls, sliding along them. Everything asks the house's BVH.
 */
import { DoubleSide, Ray, Vector3 } from 'three';
import type { MeshBVH } from 'three-mesh-bvh';

export const EYE_M = 1.6; // eye height of a standing adult
export const WALK_SPEED = 2.2; // meters per second
export const RUN_FACTOR = 2.2;
const BODY_RADIUS_M = 0.3; // how close the eye gets to a wall
// Above the feet: knee, waist, head. The lowest is above anything that can be stepped onto, and
// high enough that on a steep staircase it does not meet the steps ahead within a body's radius.
const PROBE_HEIGHTS_M = [0.45, 1.0, 1.5];
const STEP_UP_M = 0.42; // a floor more than this above the feet is not stepped onto
const DROP_M = 1.5; // how far below the feet a floor is still followed down

/** First surface along a ray: its distance and its normal. */
export function cast(bvh: MeshBVH, origin: Vector3, direction: Vector3, far: number): { distance: number; normal: Vector3 } | null {
  const hit = bvh.raycastFirst(new Ray(origin, direction), DoubleSide, 0, far);
  return hit ? { distance: hit.distance, normal: hit.face ? hit.face.normal.clone() : direction.clone().negate() } : null;
}

/** Height of the floor under a point at about eye height, or null if there is none. */
export function floorUnder(bvh: MeshBVH, eye: Vector3): number | null {
  const hit = cast(bvh, eye, new Vector3(0, -1, 0), EYE_M + DROP_M);
  return hit ? eye.y - hit.distance : null;
}

/** Move the eye by a horizontal step, stopping at walls and sliding along them. Returns whether it moved. */
export function walkBy(bvh: MeshBVH, eye: Vector3, step: Vector3): boolean {
  const feet = eye.y - EYE_M;
  for (let pass = 0; pass < 2; pass++) {
    const length = step.length();
    if (length < 1e-6) return false;
    const direction = step.clone().divideScalar(length);
    let wall: Vector3 | null = null;
    let nearest = Infinity;
    for (const height of PROBE_HEIGHTS_M) {
      const hit = cast(bvh, new Vector3(eye.x, feet + height, eye.z), direction, length + BODY_RADIUS_M);
      if (hit && hit.distance < nearest) {
        nearest = hit.distance;
        wall = hit.normal;
      }
    }
    if (!wall) {
      eye.add(step);
      return true;
    }
    // Blocked: keep only the part of the step that runs along the wall, and try that once.
    wall.y = 0;
    if (wall.lengthSq() < 1e-6) return false;
    wall.normalize();
    if (wall.dot(direction) > 0) wall.negate();
    step = step.clone().addScaledVector(wall, -step.dot(wall));
  }
  return false;
}

/** Keep the eye at eye height above the floor: up and down stairs, smoothly. */
export function followFloor(bvh: MeshBVH, eye: Vector3, dt: number): void {
  const floor = floorUnder(bvh, eye);
  if (floor === null || floor - (eye.y - EYE_M) > STEP_UP_M) return;
  const target = floor + EYE_M;
  if (Math.abs(target - eye.y) < 0.002) return;
  eye.y += (target - eye.y) * (1 - Math.exp(-dt * 12));
}
