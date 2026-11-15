'use client';

import { useFrame, useThree } from '@react-three/fiber';
import { useEffect, useMemo, useRef, type MutableRefObject } from 'react';
import { Euler, MathUtils, Quaternion, Ray, Raycaster, Vector2, Vector3, type Object3D, type PerspectiveCamera } from 'three';
import type { House } from '@/lib/house/house';
import { EYE_M, RUN_FACTOR, WALK_SPEED, floorUnder, followFloor, walkBy } from '@/lib/house/walk';

/**
 * The tour's camera on the rebuilt house. Three ways to stand:
 *
 *   inside   a person at eye height: drag looks around, the wheel zooms, W A S D (or the arrows)
 *            walk, stopping at walls and climbing stairs; a click on a spot asks to go there.
 *   outside  around the house, without its lid: drag turns it, the wheel comes nearer.
 *   above    looking down into a room (arranging furniture): like outside, around a spot.
 *
 * Between them the camera flies. Angles are degrees in the Showcase convention the rest of the app
 * uses: yaw 0 looks along -Z and positive turns left; pitch is up positive.
 */

export type TourCameraPlace = 'inside' | 'outside' | 'above';

export interface TourPose {
  x: number;
  y: number;
  z: number;
  yaw_deg: number;
  pitch_deg: number;
  place: TourCameraPlace;
}

export type P3 = { x: number; y: number; z: number };

export interface TourRig {
  /** Stand at a scan position (its eye), facing `facing` (or as the camera faces now). `ms` 0 is at once. */
  standAt(at: P3, facing: { x: number; y: number } | null, ms: number): Promise<void>;
  /** Turn where the camera stands, at `degPerS`. */
  turnTo(facing: { x: number; y: number }, degPerS: number): Promise<void>;
  /** Around the house from outside. */
  outside(ms: number): Promise<void>;
  /** Looking down: from `position` with `rotation`, the storeys above `storey` hidden. */
  above(view: { position: P3; rotation: { x: number; y: number } }, storey: number | null, ms: number): Promise<void>;
  /** Turn by (phone convention: positive yaw looks right, positive pitch looks up). */
  turn(yawDeg: number, pitchDeg: number): void;
  /** Zoom in (positive) or out: inside the view narrows, outside the camera comes nearer. */
  zoom(delta: number): void;
  readonly moving: boolean;
  readonly place: TourCameraPlace;
  pose(): TourPose;
  /** Where a point shows on screen, in CSS pixels of the view. */
  project(x: number, y: number, z: number): [number, number];
  size(): { w: number; h: number };
  /** Every frame the camera moved: its pose, and whether the user walked it (keys) rather than a flight. */
  onPose(fn: (pose: TourPose, byUser: boolean) => void): () => void;
}

const DEG = Math.PI / 180;
const WALK_FOV = 72;
const MAX_ZOOM = 3;
const PITCH_LIMIT = 85 * DEG;
/** Outside: how steeply the camera may look down, and how flat. */
const ORBIT_PITCH = { min: -89 * DEG, max: -4 * DEG };
const ORBIT_DISTANCE = { min: 2.5, max: 160 };
/** Drag: radians per pixel. */
const LOOK_PER_PX = 0.0045;
const ORBIT_PER_PX = 0.006;
/** Movement keys (by position on the keyboard): [forward, right]. */
const WALK_KEYS: Record<string, [number, number]> = {
  KeyW: [1, 0],
  ArrowUp: [1, 0],
  KeyS: [-1, 0],
  ArrowDown: [-1, 0],
  KeyD: [0, 1],
  ArrowRight: [0, 1],
  KeyA: [0, -1],
  ArrowLeft: [0, -1],
};

interface View {
  place: TourCameraPlace;
  /** Inside: the eye. */
  eye: Vector3;
  /** Outside and above: what the camera turns around, and how far from it it stands. */
  center: Vector3;
  distance: number;
  /** Radians, three.js: yaw 0 looks along -Z, positive turns left. */
  yaw: number;
  pitch: number;
  zoom: number;
}

const forwardOf = (yaw: number, pitch: number) => new Vector3(-Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), -Math.cos(yaw) * Math.cos(pitch));

function positionOf(v: View): Vector3 {
  return v.place === 'inside' ? v.eye.clone() : v.center.clone().addScaledVector(forwardOf(v.yaw, v.pitch), -v.distance);
}

function quaternionOf(v: View): Quaternion {
  return new Quaternion().setFromEuler(new Euler(v.pitch, v.yaw, 0, 'YXZ'));
}

const smoothstep = (t: number) => t * t * (3 - 2 * t);
const wrap = (a: number) => Math.atan2(Math.sin(a), Math.cos(a));

export function TourCamera({
  rig,
  house,
  center,
  radius,
  active,
  onPick,
  onLid,
}: {
  rig: MutableRefObject<TourRig | null>;
  house: MutableRefObject<House | null>;
  /** The middle of the house and how far it reaches: where the outside view looks. */
  center: Vector3;
  radius: number;
  active: boolean;
  /** A click on a spot (not a drag): where it landed, interior frame. */
  onPick(point: Vector3): void;
  /** What of the house to draw for the way the camera stands: the lid (from inside), and from above the top storey and the spot looked down at. */
  onLid(lid: { inside: boolean; topStorey: number | null; focus: P3 | null }): void;
}) {
  const camera = useThree((s) => s.camera) as PerspectiveCamera;
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);

  const view = useRef<View>({ place: 'outside', eye: new Vector3(), center: center.clone(), distance: radius * 1.95, yaw: 0, pitch: -40 * DEG, zoom: 1 });
  const flight = useRef<{ fromPos: Vector3; fromQuat: Quaternion; fromFov: number; to: View; start: number; ms: number; done(): void } | null>(null);
  const keys = useRef(new Set<string>());
  const listeners = useRef(new Set<(pose: TourPose, byUser: boolean) => void>());
  const activeRef = useRef(active);
  activeRef.current = active;
  const pickRef = useRef(onPick);
  pickRef.current = onPick;
  const lidRef = useRef(onLid);
  lidRef.current = onLid;

  const outsideView = useMemo(() => {
    // From the south-west and above.
    const toCamera = new Vector3(-0.9, 1.25, 1.15).normalize();
    const forward = toCamera.clone().negate();
    return { center, distance: radius * 1.95, yaw: Math.atan2(-forward.x, -forward.z), pitch: Math.asin(forward.y) };
  }, [center, radius]);

  useEffect(() => {
    const fovOf = (zoom: number) => (2 * Math.atan(Math.tan((WALK_FOV / 2) * DEG) / zoom)) / DEG;
    const showFor = (v: View, storey: number | null = null) =>
      lidRef.current({
        inside: v.place === 'inside',
        topStorey: v.place === 'above' ? storey : null,
        focus: v.place === 'above' ? { x: v.center.x, y: v.center.y, z: v.center.z } : null,
      });

    /** Fly to a view (or jump, with ms 0). A newer flight takes over; the older one counts as done. */
    const fly = (to: View, ms: number, storey: number | null = null): Promise<void> =>
      new Promise((resolve) => {
        flight.current?.done();
        showFor(to, storey);
        if (ms <= 0) {
          flight.current = null;
          view.current = to;
          resolve();
          return;
        }
        flight.current = { fromPos: camera.position.clone(), fromQuat: camera.quaternion.clone(), fromFov: camera.fov, to, start: performance.now(), ms, done: resolve };
      });

    const eyeAt = (at: P3) => {
      const eye = new Vector3(at.x, at.y, at.z);
      const bvh = house.current?.bvh;
      const floor = bvh ? floorUnder(bvh, eye) : null;
      if (floor !== null) eye.y = floor + EYE_M;
      return eye;
    };
    const facingOf = (facing: { x: number; y: number } | null) => (facing ? { yaw: facing.y * DEG, pitch: MathUtils.clamp(facing.x * DEG, -PITCH_LIMIT, PITCH_LIMIT) } : null);

    rig.current = {
      standAt(at, facing, ms) {
        const f = facingOf(facing);
        const v = view.current;
        // Keep looking the way the camera looks now (from outside: level, the same heading).
        const yaw = f?.yaw ?? v.yaw;
        const pitch = f?.pitch ?? (v.place === 'inside' ? v.pitch : 0);
        return fly({ ...v, place: 'inside', eye: eyeAt(at), yaw, pitch, zoom: 1 }, ms);
      },
      turnTo(facing, degPerS) {
        const v = view.current;
        const f = facingOf(facing)!;
        const turn = Math.max(Math.abs(wrap(f.yaw - v.yaw)), Math.abs(f.pitch - v.pitch)) / DEG;
        return fly({ ...v, yaw: v.yaw + wrap(f.yaw - v.yaw), pitch: f.pitch }, (turn / degPerS) * 1000);
      },
      outside(ms) {
        const o = outsideView;
        return fly({ ...view.current, place: 'outside', center: o.center.clone(), distance: o.distance, yaw: o.yaw, pitch: o.pitch, zoom: 1 }, ms);
      },
      above({ position, rotation }, storey, ms) {
        const yaw = rotation.y * DEG;
        const pitch = rotation.x * DEG;
        // The camera turns around the spot it looks down at, where the view's line meets the floor below.
        const forward = forwardOf(yaw, pitch);
        const depth = forward.y < -0.05 ? Math.abs((position.y - floorBelow(position)) / forward.y) : 6;
        const from = new Vector3(position.x, position.y, position.z);
        return fly({ ...view.current, place: 'above', center: from.clone().addScaledVector(forward, depth), distance: depth, yaw, pitch, zoom: 1 }, ms, storey);
      },
      turn(yawDeg, pitchDeg) {
        const v = view.current;
        if (flight.current) return;
        v.yaw -= yawDeg * DEG;
        if (v.place === 'inside') v.pitch = MathUtils.clamp(v.pitch + pitchDeg * DEG, -PITCH_LIMIT, PITCH_LIMIT);
        else v.pitch = MathUtils.clamp(v.pitch + pitchDeg * DEG, ORBIT_PITCH.min, ORBIT_PITCH.max);
      },
      zoom(delta) {
        const v = view.current;
        if (v.place === 'inside') v.zoom = MathUtils.clamp(v.zoom + delta, 1, MAX_ZOOM);
        else v.distance = MathUtils.clamp(v.distance * (1 - delta), ORBIT_DISTANCE.min, ORBIT_DISTANCE.max);
      },
      get moving() {
        return !!flight.current;
      },
      get place() {
        return view.current.place;
      },
      pose: () => poseNow(),
      project(x, y, z) {
        const p = new Vector3(x, y, z).project(camera);
        const { w, h } = size();
        return [((p.x + 1) / 2) * w, ((1 - p.y) / 2) * h];
      },
      size,
      onPose(fn) {
        listeners.current.add(fn);
        return () => listeners.current.delete(fn);
      },
    };
    // The floor under a point high above it: the house's, or (without one) the bottom of the house.
    function floorBelow(p: P3): number {
      const bvh = house.current?.bvh;
      const hit = bvh?.raycastFirst(new Ray(new Vector3(p.x, p.y, p.z), new Vector3(0, -1, 0)), undefined, 0, 200);
      return hit ? p.y - hit.distance : center.y - 1.5;
    }
    function size() {
      return { w: gl.domElement.clientWidth || 1, h: gl.domElement.clientHeight || 1 };
    }
    function poseNow(): TourPose {
      const v = view.current;
      const p = camera.position;
      return { x: p.x, y: p.y, z: p.z, yaw_deg: v.yaw / DEG, pitch_deg: v.pitch / DEG, place: v.place };
    }
    view.current = { ...view.current, place: 'outside', center: outsideView.center.clone(), distance: outsideView.distance, yaw: outsideView.yaw, pitch: outsideView.pitch };
    showFor(view.current);
    camera.fov = fovOf(1);
    return () => {
      flight.current?.done();
      flight.current = null;
      rig.current = null;
    };
  }, [rig, camera, gl, house, center, outsideView]);

  // --- input ----------------------------------------------------------------------------------
  useEffect(() => {
    const el = gl.domElement;
    let press: { x: number; y: number; moved: number; id: number } | null = null;
    const down = (e: PointerEvent) => {
      if (e.button !== 0) return;
      press = { x: e.clientX, y: e.clientY, moved: 0, id: e.pointerId };
      el.setPointerCapture(e.pointerId);
    };
    const move = (e: PointerEvent) => {
      if (!press || e.pointerId !== press.id) return;
      const dx = e.clientX - press.x;
      const dy = e.clientY - press.y;
      press.x = e.clientX;
      press.y = e.clientY;
      press.moved += Math.abs(dx) + Math.abs(dy);
      if (press.moved < 4 || flight.current) return;
      const v = view.current;
      if (v.place === 'inside') {
        // Drag the world, as in a panorama.
        v.yaw += (dx * LOOK_PER_PX) / v.zoom;
        v.pitch = MathUtils.clamp(v.pitch + (dy * LOOK_PER_PX) / v.zoom, -PITCH_LIMIT, PITCH_LIMIT);
      } else {
        v.yaw -= dx * ORBIT_PER_PX;
        v.pitch = MathUtils.clamp(v.pitch - dy * ORBIT_PER_PX * 0.7, ORBIT_PITCH.min, ORBIT_PITCH.max);
      }
    };
    const up = (e: PointerEvent) => {
      if (!press || e.pointerId !== press.id) return;
      const click = press.moved < 4;
      press = null;
      if (!click) return;
      // A click: the first surface drawn under the pointer (through a wall faded out of the way).
      const r = el.getBoundingClientRect();
      const ndc = new Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
      const ray = new Raycaster();
      ray.setFromCamera(ndc, camera);
      const hit = ray
        .intersectObjects(scene.children, true)
        .find((i) => shown(i.object) && i.object.userData.kind !== 'sky' && i.object.userData.kind !== 'mirror' && !house.current?.seesThrough(i.object, i.point, i.face?.normal, camera.position));
      if (hit) pickRef.current(hit.point);
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const v = view.current;
      const step = MathUtils.clamp(e.deltaY, -200, 200);
      if (v.place === 'inside') v.zoom = MathUtils.clamp(v.zoom * Math.exp(-step * 0.002), 1, MAX_ZOOM);
      else v.distance = MathUtils.clamp(v.distance * Math.exp(step * 0.0015), ORBIT_DISTANCE.min, ORBIT_DISTANCE.max);
    };
    const keyDown = (e: KeyboardEvent) => {
      if (!activeRef.current || e.ctrlKey || e.metaKey || e.altKey) return;
      if ((e.target as HTMLElement | null)?.closest?.('input, textarea, select, [contenteditable]')) return;
      if (e.code === 'ShiftLeft' || e.code === 'ShiftRight') return void keys.current.add(e.code);
      if (!(e.code in WALK_KEYS) || view.current.place !== 'inside') return;
      e.preventDefault();
      keys.current.add(e.code);
    };
    const keyUp = (e: KeyboardEvent) => keys.current.delete(e.code);
    const blur = () => keys.current.clear();
    el.addEventListener('pointerdown', down);
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('wheel', wheel, { passive: false });
    addEventListener('keydown', keyDown);
    addEventListener('keyup', keyUp);
    addEventListener('blur', blur);
    return () => {
      el.removeEventListener('pointerdown', down);
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', up);
      el.removeEventListener('wheel', wheel);
      removeEventListener('keydown', keyDown);
      removeEventListener('keyup', keyUp);
      removeEventListener('blur', blur);
    };
  }, [gl, camera, scene, house]);
  useEffect(() => {
    if (!active) keys.current.clear();
  }, [active]);

  // --- every frame -----------------------------------------------------------------------------
  const last = useRef({ pos: new Vector3(Infinity, 0, 0), quat: new Quaternion() });
  useFrame((_, delta) => {
    const dt = Math.min(0.1, delta);
    const v = view.current;
    let byUser = false;

    // Walking by keys.
    if (v.place === 'inside' && !flight.current && keys.current.size) {
      let forward = 0;
      let right = 0;
      for (const code of keys.current) {
        const k = WALK_KEYS[code];
        if (k) {
          forward += k[0];
          right += k[1];
        }
      }
      if (forward || right) {
        const sin = Math.sin(v.yaw);
        const cos = Math.cos(v.yaw);
        const run = keys.current.has('ShiftLeft') || keys.current.has('ShiftRight');
        const step = new Vector3(-sin * forward + cos * right, 0, -cos * forward - sin * right).normalize().multiplyScalar(WALK_SPEED * (run ? RUN_FACTOR : 1) * dt);
        const bvh = house.current?.bvh;
        if (bvh) walkBy(bvh, v.eye, step);
        else v.eye.add(step);
        byUser = true;
      }
    }
    if (v.place === 'inside' && !flight.current) {
      const bvh = house.current?.bvh;
      if (bvh) followFloor(bvh, v.eye, dt);
    }

    const fov = (2 * Math.atan(Math.tan((WALK_FOV / 2) * DEG) / v.zoom)) / DEG;
    const f = flight.current;
    if (f) {
      const k = Math.min(1, (performance.now() - f.start) / f.ms);
      const e = smoothstep(k);
      camera.position.copy(f.fromPos).lerp(positionOf(f.to), e);
      camera.quaternion.copy(f.fromQuat).slerp(quaternionOf(f.to), e);
      camera.fov = f.fromFov + ((2 * Math.atan(Math.tan((WALK_FOV / 2) * DEG) / f.to.zoom)) / DEG - f.fromFov) * e;
      if (k >= 1) {
        view.current = f.to;
        flight.current = null;
        f.done();
      }
    } else {
      camera.position.copy(positionOf(v));
      camera.quaternion.copy(quaternionOf(v));
      camera.fov = fov;
    }
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld();

    const moved = !camera.position.equals(last.current.pos) || !camera.quaternion.equals(last.current.quat);
    if (!moved) return;
    last.current.pos.copy(camera.position);
    last.current.quat.copy(camera.quaternion);
    const pose = { x: camera.position.x, y: camera.position.y, z: camera.position.z, yaw_deg: view.current.yaw / DEG, pitch_deg: view.current.pitch / DEG, place: view.current.place };
    for (const fn of listeners.current) fn(pose, byUser);
  });

  return null;
}

/** Whether an object and everything above it is shown. */
function shown(o: Object3D | null): boolean {
  for (; o; o = o.parent) if (!o.visible) return false;
  return true;
}
