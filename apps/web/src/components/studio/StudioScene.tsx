'use client';

import type { PropertyTwin, Room } from '@ithaca/schema';
import { Edges, Html, Line, useGLTF } from '@react-three/drei';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { Suspense, useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { BufferAttribute, BufferGeometry, DoubleSide, FrontSide, PMREMGenerator, Plane, Raycaster, Vector2, Vector3, type DirectionalLight, type Group, type Mesh, type MeshBasicMaterial, type OrthographicCamera } from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { furnitureChanged } from '@/components/house/ArrangePanel';
import { AssetBoundary } from '@/components/house3d/House3D';
import { api } from '@/lib/api';
import { useHouseFurniture, useInventory } from '@/lib/data/hooks';
import { bounds, pointInPolygon } from '@/lib/geometry/polygon';
import { BOX, onHandNav, type HandView } from '@/lib/hands/engine';
import { handEngine, useHands } from '@/lib/hands/store';
import { studioLive, useStudio, type StudioFraming } from '@/lib/studio/store';
import { floorGeometry, wallGeometry } from '@/lib/three/roomShell';

/**
 * The room studio's picture: one room on its own, like a model on a table, seen from a corner
 * above with no perspective. A pinch-drag turns and tilts it (it keeps turning a little when let
 * go), two pinching hands zoom, and slide it when they move together. Two open hands held apart frame a footprint on the floor, and one
 * pointing finger pins a spot: held still, either becomes the mark the next piece is stood on.
 * A fist held over a piece takes it out of the room (it stays in storage).
 * The mouse does the same: drag to turn, wheel to zoom, Shift-drag on the floor to mark.
 */

const DEG = Math.PI / 180;
/** Where the camera settles (degrees round and up), its limits, and how far it stands (m; with no perspective this only has to clear the room). */
const VIEW = { yaw: 38, pitch: 33, minPitch: 14, maxPitch: 80, minZoom: 0.45, maxZoom: 3.4, distance: 60 } as const;
/** Hands on the view: degrees per viewport dragged, how strongly the spread zooms, the time constant (s) the view follows with, and how long a flick keeps turning (s). */
const HAND = { yawDegPerView: 220, pitchDegPerView: 80, zoomGain: 1.6, easeS: 0.085, coastS: 0.75, maxSpin: 420 } as const;
/**
 * Marking. `holdS` / `pinHoldS`: how long the hands must rest before the mark locks. `stillMps`:
 * moving slower than this (metres of floor a second) is resting. `settleS`: after a pinch, this
 * long before open hands frame anything (letting go of a zoom is not a mark). `snapDeg`: a
 * footprint this near square to the nearest wall is made square to it.
 *
 * How wide: the hands hold a model of the piece at `scale` to one. The gap between them is
 * measured in the real world (in palms, `palmM` each, so it reads the same however near the camera
 * they are and however the room is zoomed) and the piece is `scale` times that: hands 40 cm
 * apart are a two metre sofa. At 1, the piece would be as wide as the gap itself.
 */
const MARK = { holdS: 0.9, pinHoldS: 0.8, stillMps: 0.5, minW: 0.3, maxW: 4.5, settleS: 0.6, snapDeg: 14, easeS: 0.07, scale: 5, palmM: 0.1, palmEaseS: 0.25 } as const;
/** The room on screen: its diagonal over the shorter side of the view (smaller is a bigger room), and how far above the floor the camera looks (m), so floor and walls sit in the middle together. */
const FIT = { span: 2.5, lookUp: 1.25 } as const;
const COLOR = { frame: '#f1c84b', lock: '#2bdc86', L: '#4aa8ff', R: '#ff6b5c' } as const;
/**
 * Taking a piece out by hand: a fist held over it. `holdS`: for how long. `dwellS`: how long a
 * hand rests on a piece before the piece is pointed out (a hand on its way across the room passes
 * several). `steal`: how much nearer another piece must be to take the hand from the one it is on.
 * `reach`: how near the ring must be to the piece's middle, as a share of the piece's size on
 * screen, and at least `minReachPx`.
 */
const REMOVE = { holdS: 1, dwellS: 0.2, steal: 0.6, reach: 0.55, minReachPx: 70 } as const;

/** The wall nearest a spot: how far, its unit normal into the room, and its direction. */
function nearestWall(room: Room, x: number, z: number) {
  let best = { d: Infinity, nx: 0, nz: 1, tx: 1, tz: 0 };
  const poly = room.polygon;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i]!;
    const b = poly[(i + 1) % poly.length]!;
    const ex = b[0] - a[0];
    const ez = b[1] - a[1];
    const len = Math.hypot(ex, ez);
    if (len < 1e-6) continue;
    const t = Math.min(1, Math.max(0, ((x - a[0]) * ex + (z - a[1]) * ez) / (len * len)));
    const d = Math.hypot(x - (a[0] + ex * t), z - (a[1] + ez * t));
    if (d >= best.d) continue;
    let nx = -ez / len;
    let nz = ex / len;
    if (!pointInPolygon([(a[0] + b[0]) / 2 + nx * 0.05, (a[1] + b[1]) / 2 + nz * 0.05], poly)) {
      nx = -nx;
      nz = -nz;
    }
    best = { d, nx, nz, tx: ex / len, tz: ez / len };
  }
  return best;
}

/**
 * The footprint two floor spots frame: its middle, its width (the gap), and its turn. The width
 * runs along the line between the spots, squared to the nearest wall when nearly so, and the
 * piece's front (+Z of its model) looks away from that wall, into the room.
 */
function frameBetween(room: Room, ax: number, az: number, bx: number, bz: number): { x: number; z: number; width: number; yaw_deg: number } {
  const x = (ax + bx) / 2;
  const z = (az + bz) / 2;
  const width = Math.min(Math.max(Math.hypot(bx - ax, bz - az), MARK.minW), MARK.maxW);
  const wall = nearestWall(room, x, z);
  let yaw = Math.atan2(-(bz - az), bx - ax) / DEG;
  const wallYaw = Math.atan2(-wall.tz, wall.tx) / DEG;
  const off = ((((yaw - wallYaw + 45) % 90) + 90) % 90) - 45;
  if (Math.abs(off) < MARK.snapDeg) yaw -= off;
  if (Math.sin(yaw * DEG) * wall.nx + Math.cos(yaw * DEG) * wall.nz < 0) yaw += 180;
  return { x, z, width, yaw_deg: ((yaw % 360) + 360) % 360 };
}

/** A hand's palm in the camera picture, in picture heights: its length or, scaled to match, its width, whichever a turned hand has not foreshortened. */
function palmSize(hand: HandView, aspect: number): number {
  const p = hand.landmarks;
  const wrist = p[0];
  const middle = p[9];
  const index = p[5];
  const pinky = p[17];
  if (!wrist || !middle || !index || !pinky) return 0;
  return Math.max(Math.hypot((middle.x - wrist.x) * aspect, middle.y - wrist.y), 1.25 * Math.hypot((index.x - pinky.x) * aspect, index.y - pinky.y));
}

/** The gap between two hands in the camera picture, in picture heights (from where the rings are drawn, which is smoothed). */
function handGap(a: HandView, b: HandView, aspect: number): number {
  return Math.hypot((a.u - b.u) * BOX.w * aspect, (a.v - b.v) * BOX.h);
}

/** A pinned spot: no size, its front looking away from the nearest wall. */
function pinAt(room: Room, x: number, z: number): { x: number; z: number; yaw_deg: number } {
  const wall = nearestWall(room, x, z);
  return { x, z, yaw_deg: ((Math.atan2(wall.nx, wall.nz) / DEG) % 360 + 360) % 360 };
}

// ---------------------------------------------------------------------------------------------
// The camera, and the hands (or the mouse) on it
// ---------------------------------------------------------------------------------------------

function Rig({ room, center, radius }: { room: Room; center: Vector3; radius: number }) {
  const camera = useThree((s) => s.camera) as OrthographicCamera;
  const size = useThree((s) => s.size);
  const gl = useThree((s) => s.gl);
  // It opens from higher up and further round, small, and swings in.
  const view = useRef({ yaw: VIEW.yaw - 80, pitch: 66, zoom: 0.5, tYaw: VIEW.yaw as number, tPitch: VIEW.pitch as number, tZoom: 1, spin: 0, dragAt: 0, age: 0, busyAt: performance.now() / 1000, mouse: false, panX: 0, panZ: 0, tPanX: 0, tPanZ: 0, midU: 0, midV: 0, mid: false });
  const mark = useRef({ x: 0, z: 0, w: 1, yaw: 0, hold: 0, has: false, lastX: 0, lastZ: 0, lastW: 0, motion: 0, pinchAt: 0, locked: false, apartAt: 0, mouse: false, palm: 0 });
  const tools = useMemo(() => ({ ray: new Raycaster(), ndc: new Vector2(), hit: new Vector3(), look: new Vector3(), plane: new Plane(new Vector3(0, 1, 0), -room.floor_y) }), [room.floor_y]);

  /** Where a spot of the view (fractions of it, from the top left) lands on the room's floor. */
  const floorHit = (u: number, v: number): [number, number] | null => {
    tools.ndc.set(u * 2 - 1, 1 - v * 2);
    tools.ray.setFromCamera(tools.ndc, camera);
    return tools.ray.ray.intersectPlane(tools.plane, tools.hit) ? [tools.hit.x, tools.hit.z] : null;
  };
  const hitRef = useRef(floorHit);
  hitRef.current = floorHit;

  // Hands turn and zoom the room.
  useEffect(
    () =>
      onHandNav((nav) => {
        const v = view.current;
        const now = performance.now() / 1000;
        v.busyAt = now;
        if (nav.type === 'drag') {
          const d = -nav.du * HAND.yawDegPerView;
          v.tYaw += d;
          v.tPitch = Math.min(VIEW.maxPitch, Math.max(VIEW.minPitch, v.tPitch + nav.dv * HAND.pitchDegPerView));
          const gap = now - v.dragAt;
          const speed = d / Math.max(gap, 1 / 120);
          v.spin = gap > 0.2 ? 0 : Math.max(-HAND.maxSpin, Math.min(HAND.maxSpin, v.spin * 0.6 + speed * 0.4));
          v.dragAt = now;
        } else if (nav.type === 'zoom') {
          v.tZoom = Math.min(VIEW.maxZoom, Math.max(VIEW.minZoom, v.tZoom * Math.pow(nav.factor, HAND.zoomGain)));
          v.spin = 0;
        }
      }),
    [],
  );

  // The mouse does the same; Shift-drag (or the right button) on the floor draws a mark.
  useEffect(() => {
    const el = gl.domElement;
    let drag: { x: number; y: number; from: [number, number] | null } | null = null;
    const at = (e: PointerEvent): [number, number] => {
      const r = el.getBoundingClientRect();
      return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
    };
    const down = (e: PointerEvent) => {
      const marking = e.shiftKey || e.button === 2;
      const [u, v] = at(e);
      drag = { x: e.clientX, y: e.clientY, from: marking ? hitRef.current(u, v) : null };
      // A Shift-click with no drag pins the spot.
      if (drag.from) studioLive.framing = { ...pinAt(room, drag.from[0], drag.from[1]), width: null, depth: null, kind: 'pin', progress: 1 };
      view.current.mouse = true;
      view.current.spin = 0;
      mark.current.mouse = marking;
      el.setPointerCapture(e.pointerId);
    };
    const move = (e: PointerEvent) => {
      if (!drag) return;
      const v = view.current;
      v.busyAt = performance.now() / 1000;
      if (drag.from) {
        const to = hitRef.current(...at(e));
        if (!to) return;
        const far = Math.hypot(to[0] - drag.from[0], to[1] - drag.from[1]);
        studioLive.framing =
          far > 0.25
            ? { ...frameBetween(room, drag.from[0], drag.from[1], to[0], to[1]), depth: null, kind: 'size', progress: 1 }
            : { ...pinAt(room, drag.from[0], drag.from[1]), width: null, depth: null, kind: 'pin', progress: 1 };
        return;
      }
      const r = el.getBoundingClientRect();
      v.tYaw -= ((e.clientX - drag.x) / r.width) * HAND.yawDegPerView;
      v.tPitch = Math.min(VIEW.maxPitch, Math.max(VIEW.minPitch, v.tPitch + ((e.clientY - drag.y) / r.height) * HAND.pitchDegPerView));
      drag.x = e.clientX;
      drag.y = e.clientY;
    };
    const up = () => {
      if (drag?.from) {
        const f = studioLive.framing;
        if (f && pointInPolygon([f.x, f.z], room.polygon)) useStudio.getState().setMark({ x: f.x, z: f.z, width: f.width, depth: f.depth, yaw_deg: f.yaw_deg, kind: f.kind });
        studioLive.framing = null;
      }
      drag = null;
      view.current.mouse = false;
      mark.current.mouse = false;
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const v = view.current;
      v.busyAt = performance.now() / 1000;
      v.tZoom = Math.min(VIEW.maxZoom, Math.max(VIEW.minZoom, v.tZoom * Math.exp(-e.deltaY * 0.0014)));
    };
    const menu = (e: Event) => e.preventDefault();
    el.addEventListener('pointerdown', down);
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
    el.addEventListener('wheel', wheel, { passive: false });
    el.addEventListener('contextmenu', menu);
    return () => {
      el.removeEventListener('pointerdown', down);
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', up);
      el.removeEventListener('wheel', wheel);
      el.removeEventListener('contextmenu', menu);
    };
  }, [gl, room]);

  useEffect(
    () => () => {
      studioLive.framing = null;
      studioLive.spots = [];
      studioLive.gesture = 'idle';
      studioLive.aim = null;
    },
    [],
  );

  // Before the scene is drawn: the camera first, then what the hands frame through it.
  useFrame((_, rawDt) => {
    const dt = Math.min(rawDt, 0.1);
    const now = performance.now() / 1000;
    const v = view.current;
    const m = mark.current;
    const gesture = handEngine.gesture;
    const hands = handEngine.hands.filter((h) => !h.stale);
    if (hands.length) v.busyAt = now;

    // A flick keeps the room turning for a moment; a pinch held still stops it.
    if (gesture === 'drag' || v.mouse) {
      if (now - v.dragAt > 0.09) v.spin = 0;
    } else if (Math.abs(v.spin) > 1.5) {
      v.tYaw += v.spin * dt;
      v.spin *= Math.exp(-dt / HAND.coastS);
    }
    // Left alone, it turns slowly, so the screen is never a still picture.
    if (now - v.busyAt > 9 && !useStudio.getState().mark) v.tYaw += 3 * dt;

    // Two pinching hands that move together slide the room under them, as a hand on a table would.
    const grip = hands.filter((h) => h.pinching);
    if (grip.length === 2 && camera.zoom > 0) {
      const midU = (grip[0]!.u + grip[1]!.u) / 2;
      const midV = (grip[0]!.v + grip[1]!.v) / 2;
      if (v.mid) {
        const right = ((midU - v.midU) * size.width) / camera.zoom;
        const away = ((midV - v.midV) * size.height) / camera.zoom / Math.max(Math.sin(v.pitch * DEG), 0.25);
        const a = v.yaw * DEG;
        v.tPanX += -Math.cos(a) * right - Math.sin(a) * away;
        v.tPanZ += Math.sin(a) * right - Math.cos(a) * away;
        const far = Math.hypot(v.tPanX, v.tPanZ);
        if (far > radius * 1.4) {
          v.tPanX *= (radius * 1.4) / far;
          v.tPanZ *= (radius * 1.4) / far;
        }
      }
      v.mid = true;
      v.midU = midU;
      v.midV = midV;
    } else {
      v.mid = false;
    }

    v.age += dt;
    const k = 1 - Math.exp(-dt / (v.age < 1.8 ? 0.42 : HAND.easeS));
    v.yaw += (v.tYaw - v.yaw) * k;
    v.pitch += (v.tPitch - v.pitch) * k;
    v.zoom += (v.tZoom - v.zoom) * k;
    v.panX += (v.tPanX - v.panX) * k;
    v.panZ += (v.tPanZ - v.panZ) * k;
    const yaw = v.yaw * DEG;
    const pitch = v.pitch * DEG;
    const look = tools.look.set(center.x + v.panX, center.y, center.z + v.panZ);
    camera.position.set(look.x + VIEW.distance * Math.cos(pitch) * Math.sin(yaw), look.y + VIEW.distance * Math.sin(pitch), look.z + VIEW.distance * Math.cos(pitch) * Math.cos(yaw));
    camera.lookAt(look);
    camera.zoom = (Math.min(size.width, size.height * 1.25) / (radius * FIT.span)) * v.zoom;
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld();

    // Where each hand lands on the floor.
    const spots: typeof studioLive.spots = [];
    for (const h of hands) {
      const p = floorHit(h.u, h.v);
      if (p) spots.push({ side: h.side, x: p[0], z: p[1] });
    }
    studioLive.spots = spots;

    if (hands.some((h) => h.pinching)) m.pinchAt = now;
    if (m.mouse) {
      studioLive.gesture = 'frame';
      return;
    }
    const phase = useStudio.getState().phase;
    const free = phase !== 'searching' && phase !== 'fetching' && phase !== 'placing' && now - m.pinchAt > MARK.settleS;
    const two = hands.length === 2 && hands.every((h) => !h.pinching && h.pose !== 'fist');
    const one = hands.length === 1 && !hands[0]!.pinching && hands[0]!.pose === 'point';

    let raw: { x: number; z: number; width: number | null; yaw_deg: number; kind: 'size' | 'pin' } | null = null;
    if (free && two) {
      const [l, r] = [...hands].sort((a, b) => a.u - b.u) as [(typeof hands)[number], (typeof hands)[number]];
      const a = floorHit(l.u, l.v);
      const b = floorHit(r.u, r.v);
      if (a && b) {
        // Where and which way round: from the floor under the hands. How wide: from the hands themselves.
        const video = useHands.getState().video;
        const aspect = video && video.videoHeight ? video.videoWidth / video.videoHeight : 16 / 9;
        const palm = (palmSize(l, aspect) + palmSize(r, aspect)) / 2;
        if (palm > 1e-3) m.palm = m.palm > 0 ? m.palm + (palm - m.palm) * (1 - Math.exp(-dt / MARK.palmEaseS)) : palm;
        if (m.palm > 0) {
          const width = Math.min(Math.max((handGap(l, r, aspect) / m.palm) * MARK.palmM * MARK.scale, MARK.minW), MARK.maxW);
          raw = { ...frameBetween(room, a[0], a[1], b[0], b[1]), width, kind: 'size' };
        }
      }
    } else if (free && one) {
      // The fingertip, not the palm: that is what points.
      const tip = hands[0]!.landmarks[8];
      const p = tip ? floorHit((tip.x - BOX.x) / BOX.w, (tip.y - BOX.y) / BOX.h) : null;
      if (p) raw = { ...pinAt(room, p[0], p[1]), width: null, kind: 'pin' };
    }

    if (!raw) {
      // Still held up, only not free to frame (a piece is on its way, or a pinch just ended): that
      // is not the hands going away, or the same hands would lock a second mark when the piece lands.
      const up = two || one;
      if (up) {
        m.apartAt = now;
        if (!m.locked) m.hold = 0;
      }
      // The hands went apart (or away): the next time they frame something, it can lock again.
      if (m.has && now - m.apartAt > 0.35) {
        m.has = false;
        m.locked = false;
        m.hold = 0;
        m.palm = 0;
      }
      if (!m.has || up) studioLive.framing = null;
      studioLive.gesture = gesture === 'drag' ? 'spin' : gesture === 'zoom' ? 'zoom' : 'idle';
      return;
    }
    m.apartAt = now;
    v.spin = 0; // a flick must not keep turning the floor under hands that are marking it
    const w = raw.width ?? 0;
    if (!m.has) {
      m.has = true;
      m.x = raw.x;
      m.z = raw.z;
      m.w = w;
      m.hold = 0;
      m.motion = 2;
    } else {
      // A fingertip is read straight from the tracker and trembles more than a palm: it is smoothed
      // harder. Rest is judged on the smoothed spot, so that trembling alone is not movement.
      const e = 1 - Math.exp(-dt / (raw.kind === 'pin' ? MARK.easeS * 2 : MARK.easeS));
      m.x += (raw.x - m.x) * e;
      m.z += (raw.z - m.z) * e;
      m.w += (w - m.w) * e;
      const speed = (Math.hypot(m.x - m.lastX, m.z - m.lastZ) + Math.abs(m.w - m.lastW)) / Math.max(dt, 1e-3);
      m.motion += (speed - m.motion) * Math.min(1, dt / 0.12);
    }
    m.lastX = m.x;
    m.lastZ = m.z;
    m.lastW = m.w;
    m.yaw = raw.yaw_deg;
    studioLive.gesture = raw.kind === 'size' ? 'frame' : 'pin';
    if (m.locked) {
      studioLive.framing = null; // the locked mark is what shows, until the hands let go
      return;
    }
    const inside = pointInPolygon([m.x, m.z], room.polygon);
    m.hold = inside && m.motion < MARK.stillMps ? m.hold + dt : Math.max(0, m.hold - dt * 2.5);
    const need = raw.kind === 'size' ? MARK.holdS : MARK.pinHoldS;
    const framing: StudioFraming = { x: m.x, z: m.z, width: raw.kind === 'size' ? m.w : null, depth: null, yaw_deg: m.yaw, kind: raw.kind, progress: Math.min(1, m.hold / need) };
    if (framing.progress >= 1) {
      m.locked = true;
      studioLive.framing = null;
      useStudio.getState().setMark({ x: framing.x, z: framing.z, width: framing.width, depth: null, yaw_deg: framing.yaw_deg, kind: framing.kind });
      return;
    }
    studioLive.framing = framing;
  }, -1);

  return null;
}

// ---------------------------------------------------------------------------------------------
// The room
// ---------------------------------------------------------------------------------------------

/** The edge of the slab the room stands on: one quad per wall, going down. */
function skirtGeometry(room: Room, depth: number): BufferGeometry {
  const positions: number[] = [];
  const poly = room.polygon;
  const y0 = room.floor_y;
  const y1 = room.floor_y - depth;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i]!;
    const b = poly[(i + 1) % poly.length]!;
    positions.push(a[0], y0, a[1], b[0], y0, b[1], b[0], y1, b[1], a[0], y0, a[1], b[0], y1, b[1], a[0], y1, a[1]);
  }
  const geo = new BufferGeometry();
  geo.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
  geo.computeVertexNormals();
  return geo;
}

type P3 = [number, number, number];

function RoomShell({ room, twin }: { room: Room; twin: PropertyTwin }) {
  const height = Math.min(Math.max(room.height_m || 2.6, 2.2), 2.9);
  const geo = useMemo(
    () => ({
      floor: floorGeometry(room),
      // Walls from 0 up, in a group that stands on the floor: they grow out of it when the room opens.
      walls: wallGeometry({ ...room, floor_y: 0 }, height),
      skirt: skirtGeometry(room, 0.24),
    }),
    [room, height],
  );
  useEffect(
    () => () => {
      geo.floor.dispose();
      geo.walls.dispose();
      geo.skirt.dispose();
    },
    [geo],
  );
  const ring = useMemo(() => [...room.polygon, room.polygon[0]!], [room.polygon]);
  const floorLine = useMemo(() => ring.map(([x, z]): P3 => [x, room.floor_y + 0.012, z]), [ring, room.floor_y]);
  const topLine = useMemo(() => ring.map(([x, z]): P3 => [x, height, z]), [ring, height]);
  const panes = useMemo(
    () =>
      twin.openings
        .filter((o) => (o.room_id === room.room_id || o.other_room_id === room.room_id) && o.polygon.length >= 3)
        .map((o) => {
          const g = new BufferGeometry();
          g.setAttribute('position', new BufferAttribute(new Float32Array(o.polygon.flatMap((p) => [p.x, p.y, p.z])), 3));
          const index: number[] = [];
          for (let i = 1; i < o.polygon.length - 1; i++) index.push(0, i, i + 1);
          g.setIndex(index);
          return { id: o.opening_id, window: o.kind !== 'door', geometry: g };
        }),
    [twin.openings, room.room_id],
  );
  useEffect(() => () => panes.forEach((p) => p.geometry.dispose()), [panes]);

  const walls = useRef<Group>(null);
  const grown = useRef(0);
  useFrame((_, dt) => {
    if (grown.current >= 1 || !walls.current) return;
    grown.current = Math.min(1, grown.current + dt / 1.1);
    walls.current.scale.y = Math.max(0.001, 1 - Math.pow(1 - grown.current, 3));
  });

  return (
    <>
      <mesh geometry={geo.floor} receiveShadow>
        <meshStandardMaterial color="#e2d3b8" roughness={0.85} metalness={0} side={DoubleSide} />
      </mesh>
      <mesh geometry={geo.skirt}>
        <meshStandardMaterial color="#123f33" roughness={0.9} side={DoubleSide} />
      </mesh>
      <Line points={floorLine} color={COLOR.lock} lineWidth={2.2} toneMapped={false} />
      <group ref={walls} position={[0, room.floor_y, 0]} scale={[1, 0.001, 1]}>
        <mesh geometry={geo.walls} receiveShadow>
          <meshStandardMaterial color="#f5f2ea" roughness={1} side={FrontSide} />
        </mesh>
        <Line points={topLine} color="#bfeedd" lineWidth={1.2} transparent opacity={0.55} toneMapped={false} />
      </group>
      {panes.map((p) => (
        <mesh key={p.id} geometry={p.geometry} renderOrder={2}>
          <meshBasicMaterial color={p.window ? '#8fd4ff' : '#ffdca6'} transparent opacity={p.window ? 0.5 : 0.32} side={DoubleSide} depthWrite={false} polygonOffset polygonOffsetFactor={-4} toneMapped={false} />
        </mesh>
      ))}
    </>
  );
}

/** Soft light from the room's own surroundings, made here: nothing is downloaded for it. */
function Surroundings() {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  useEffect(() => {
    const pmrem = new PMREMGenerator(gl);
    const around = new RoomEnvironment();
    const env = pmrem.fromScene(around, 0.04).texture;
    around.dispose();
    scene.environment = env;
    scene.environmentIntensity = 0.55;
    return () => {
      scene.environment = null;
      env.dispose();
      pmrem.dispose();
    };
  }, [gl, scene]);
  return null;
}

function Sun({ center, radius }: { center: Vector3; radius: number }) {
  const light = useRef<DirectionalLight>(null);
  useLayoutEffect(() => {
    const l = light.current;
    if (!l) return;
    l.target.position.copy(center);
    l.target.updateMatrixWorld();
    const cam = l.shadow.camera;
    cam.left = cam.bottom = -radius * 1.3;
    cam.right = cam.top = radius * 1.3;
    cam.near = 0.5;
    cam.far = radius * 8;
    cam.updateProjectionMatrix();
  }, [center, radius]);
  return <directionalLight ref={light} position={[center.x + radius * 0.9, center.y + radius * 2.4, center.z + radius * 0.7]} intensity={2.3} castShadow shadow-mapSize={[2048, 2048]} shadow-bias={-0.0005} shadow-normalBias={0.02} />;
}

// ---------------------------------------------------------------------------------------------
// Furniture
// ---------------------------------------------------------------------------------------------

/** A piece at its stored pose. One that was just asked for drops in from above and lands with a bounce. */
function PieceModel({ id, url, x, y, z, yaw, fresh }: { id: string; url: string; x: number; y: number; z: number; yaw: number; fresh: boolean }) {
  const gltf = useGLTF(url);
  const scene = useMemo(() => {
    const root = gltf.scene.clone(true);
    root.traverse((node) => {
      const mesh = node as Mesh;
      if (!mesh.isMesh) return;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
    });
    return root;
  }, [gltf.scene]);
  const group = useRef<Group>(null);
  const ring = useRef<Mesh>(null);
  const t = useRef(fresh ? 0 : 1);
  /** 0 standing, 1 gone: it shrinks away once a fist has taken it out. */
  const out = useRef(0);
  useFrame(({ clock }, dt) => {
    const g = group.current;
    if (!g) return;
    let scale = 1;
    if (t.current < 1.6) {
      t.current += dt / 0.85;
      const e = Math.min(1, t.current);
      scale = 1 + 2.70158 * Math.pow(e - 1, 3) + 1.70158 * Math.pow(e - 1, 2);
      g.position.y = y + Math.pow(1 - e, 3) * 1.6;
      // The ring that runs out over the floor as it lands.
      const r = ring.current;
      if (r) {
        const p = Math.min(1, Math.max(0, (t.current - 0.55) / 1.05));
        r.visible = p > 0 && p < 1;
        r.scale.setScalar(0.4 + p * 2.6);
        (r.material as MeshBasicMaterial).opacity = 0.75 * (1 - p);
      }
    }
    // Under a closing fist it is squeezed and trembles; taken out, it shrinks to nothing. (If the
    // house refuses, it is no longer listed as gone and stands again.)
    const aim = studioLive.aim;
    const squeeze = aim && aim.id === id ? aim.progress : 0;
    out.current = studioLive.gone.has(id) ? Math.min(1, out.current + dt / 0.28) : 0;
    g.visible = out.current < 1;
    g.scale.setScalar(Math.max(0.001, scale * (1 - 0.22 * squeeze) * (1 - out.current * out.current)));
    g.rotation.y = yaw * DEG + (squeeze > 0 ? Math.sin(clock.elapsedTime * 38) * 0.04 * squeeze : 0);
  });
  return (
    <>
      <group ref={group} position={[x, y, z]} rotation={[0, yaw * DEG, 0]} scale={fresh ? 0.001 : 1}>
        <primitive object={scene} />
      </group>
      {fresh && (
        <mesh ref={ring} position={[x, y + 0.025, z]} rotation={[-Math.PI / 2, 0, 0]} visible={false} renderOrder={5}>
          <ringGeometry args={[0.92, 1, 64]} />
          <meshBasicMaterial color={COLOR.lock} transparent opacity={0} depthWrite={false} toneMapped={false} />
        </mesh>
      )}
    </>
  );
}

interface RoomPiece {
  id: string;
  url: string;
  label: string;
  position: { x: number; y: number; z: number };
  yaw: number;
  dims: { w: number; d: number; h: number };
}

/** The pieces standing in the room, each with its model, its name and its size. */
function useRoomPieces(twin: PropertyTwin, roomId: string): RoomPiece[] {
  const furniture = useHouseFurniture(twin.property_id);
  const inventory = useInventory();
  return useMemo(() => {
    const byId = new Map((inventory.data ?? []).map((i) => [i.item_id, i]));
    return (furniture.data ?? [])
      .filter((p) => p.room_id === roomId)
      .flatMap((p): RoomPiece[] => {
        const item = byId.get(p.item_id);
        return item ? [{ id: p.item_id, url: api().assetUrl(item.glb_url), label: item.label, position: p.position, yaw: p.yaw_deg, dims: { w: item.dims_m.w, d: item.dims_m.d, h: item.dims_m.h } }] : [];
      });
  }, [furniture.data, inventory.data, roomId]);
}

function Pieces({ twin, roomId }: { twin: PropertyTwin; roomId: string }) {
  const pieces = useRoomPieces(twin, roomId);
  const placedId = useStudio((s) => s.placedId);
  return (
    <>
      {pieces.map((p) => (
        <AssetBoundary key={p.id} label="A furniture model" onError={() => {}}>
          <Suspense fallback={null}>
            <PieceModel id={p.id} url={p.url} x={p.position.x} y={p.position.y} z={p.position.z} yaw={p.yaw} fresh={p.id === placedId} />
          </Suspense>
        </AssetBoundary>
      ))}
    </>
  );
}

/**
 * Taking a piece out by hand. One hand over a piece points it out (a white box round it); closed
 * into a fist and held there, the box turns coral and fills, the piece is squeezed, and after
 * REMOVE.holdS it leaves the room. It stays in storage, so the furniture menu can bring it back.
 * The fist must open before it can take out another: sweeping a fist across the room clears nothing.
 */
function Remover({ twin, roomId }: { twin: PropertyTwin; roomId: string }) {
  const pieces = useRoomPieces(twin, roomId);
  const camera = useThree((s) => s.camera) as OrthographicCamera;
  const size = useThree((s) => s.size);
  const group = useRef<Group>(null);
  const hover = useRef<Mesh>(null);
  const closing = useRef<Mesh>(null);
  const fill = useRef<MeshBasicMaterial>(null);
  const tag = useRef<Group>(null);
  const label = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const state = useRef({ id: null as string | null, since: 0, hold: 0, armed: true, said: '' });
  const at = useMemo(() => new Vector3(), []);
  const list = useRef(pieces);
  list.current = pieces;

  // A piece the house no longer lists needs no hiding any more.
  useEffect(() => {
    for (const id of [...studioLive.gone]) if (!pieces.some((p) => p.id === id)) studioLive.gone.delete(id);
  }, [pieces]);
  useEffect(
    () => () => {
      studioLive.aim = null;
    },
    [],
  );

  const takeOut = (piece: RoomPiece) => {
    const studio = () => useStudio.getState();
    studioLive.gone.add(piece.id);
    studio().patch({ reply: `Taking the ${piece.label} out…` });
    api()
      .removePiece(twin.property_id, piece.id)
      .then(
        () => {
          furnitureChanged(twin.property_id);
          if (studio().open) studio().patch({ reply: `${piece.label} is out of the room. It is still in your furniture.` });
        },
        (err: unknown) => {
          // It could not be taken out: it stands again.
          studioLive.gone.delete(piece.id);
          if (studio().open) studio().patch({ phase: 'error', reply: `${piece.label} could not be taken out: ${err instanceof Error ? err.message : String(err)}` });
        },
      );
  };

  useFrame((_, rawDt) => {
    const dt = Math.min(rawDt, 0.1);
    const now = performance.now() / 1000;
    const st = state.current;
    const g = group.current;
    const hands = handEngine.hands.filter((h) => !h.stale);
    const phase = useStudio.getState().phase;
    const busy = phase === 'searching' || phase === 'fetching' || phase === 'placing';
    // One hand, neither pinching nor pointing: open, it points a piece out; closed, it takes it out.
    const only = hands.length === 1 ? hands[0]! : null;
    const hand = !busy && only && !only.pinching && only.pose !== 'point' ? only : null;
    const fist = !!hand && hand.pose === 'fist';
    if (!fist) st.armed = true;

    let best: RoomPiece | null = null;
    if (hand) {
      const hx = hand.u * size.width;
      const hy = hand.v * size.height;
      let bestScore = Infinity;
      for (const p of list.current) {
        if (studioLive.gone.has(p.id)) continue;
        // Its middle, not its feet: that is where a hand reaches for a tall piece.
        at.set(p.position.x, p.position.y + p.dims.h / 2, p.position.z).project(camera);
        const far = Math.hypot(((at.x + 1) / 2) * size.width - hx, ((1 - at.y) / 2) * size.height - hy);
        const reach = Math.max(REMOVE.minReachPx, REMOVE.reach * Math.max(p.dims.w, p.dims.d, p.dims.h) * camera.zoom);
        if (far > reach) continue;
        // The piece the hand is on keeps it unless another is clearly nearer: a closing fist moves the palm a little.
        const score = (far / reach) * (p.id === st.id ? REMOVE.steal : 1);
        if (score < bestScore) {
          bestScore = score;
          best = p;
        }
      }
    }
    const target = best as RoomPiece | null;
    if ((target?.id ?? null) !== st.id) {
      st.id = target?.id ?? null;
      st.since = now;
      st.hold = 0;
    }
    const held = !!target && fist && st.armed;
    st.hold = held ? st.hold + dt : Math.max(0, st.hold - dt * 3);
    const progress = Math.min(1, st.hold / REMOVE.holdS);
    studioLive.aim = target && progress > 0 ? { id: target.id, progress } : null;
    if (held) studioLive.gesture = 'remove';

    if (target && progress >= 1) {
      st.armed = false;
      st.hold = 0;
      st.id = null;
      studioLive.aim = null;
      takeOut(target);
    }
    const shown = !!target && st.id !== null && (held || progress > 0 || now - st.since >= REMOVE.dwellS);
    if (g) g.visible = shown;
    if (label.current) label.current.style.display = shown ? '' : 'none';
    if (!target || !shown || !g) return;

    const { w, d, h } = target.dims;
    g.position.set(target.position.x, target.position.y, target.position.z);
    g.rotation.y = target.yaw * DEG;
    for (const mesh of [hover.current, closing.current]) {
      if (!mesh) continue;
      mesh.scale.set(w * 1.06, h * 1.04, d * 1.06);
      mesh.position.y = (h * 1.04) / 2;
    }
    const active = held || progress > 0;
    if (hover.current) hover.current.visible = !active;
    if (closing.current) closing.current.visible = active;
    if (fill.current) fill.current.opacity = 0.08 + 0.32 * progress;
    if (tag.current) tag.current.position.y = h + 0.35;
    const text = active ? `Removing ${target.label}` : `${target.label} · hold a fist to remove`;
    if (label.current && text !== st.said) {
      st.said = text;
      label.current.firstElementChild!.textContent = text;
    }
    if (label.current) label.current.dataset.closing = active ? '1' : '0';
    if (bar.current) bar.current.style.transform = `scaleX(${progress})`;
  });

  return (
    <group ref={group} visible={false}>
      <mesh ref={hover} renderOrder={6}>
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial color="#ffffff" transparent opacity={0.05} depthWrite={false} toneMapped={false} />
        <Edges color="#ffffff" lineWidth={2} toneMapped={false} />
      </mesh>
      <mesh ref={closing} renderOrder={6} visible={false}>
        <boxGeometry args={[1, 1, 1]} />
        <meshBasicMaterial ref={fill} color={COLOR.R} transparent opacity={0.1} depthWrite={false} toneMapped={false} />
        <Edges color={COLOR.R} lineWidth={3.5} toneMapped={false} />
      </mesh>
      <group ref={tag}>
        <Html center zIndexRange={[15, 0]} style={{ pointerEvents: 'none' }}>
          <div ref={label} data-closing="0" style={{ display: 'none' }} className="overflow-hidden rounded-full bg-white font-display text-[15px] font-semibold whitespace-nowrap text-[#06261f] shadow-lg data-[closing='1']:bg-[#ff6b5c]">
            <span className="block px-3.5 pt-1 pb-0.5" />
            <div ref={bar} className="h-1 origin-left bg-[#06261f]" style={{ transform: 'scaleX(0)' }} />
          </div>
        </Html>
      </group>
    </group>
  );
}

// ---------------------------------------------------------------------------------------------
// The mark
// ---------------------------------------------------------------------------------------------

const SQUARE: P3[] = [
  [-0.5, 0, -0.5],
  [0.5, 0, -0.5],
  [0.5, 0, 0.5],
  [-0.5, 0, 0.5],
  [-0.5, 0, -0.5],
];
const CIRCLE: P3[] = Array.from({ length: 49 }, (_, i): P3 => [Math.cos((i / 48) * Math.PI * 2) * 0.5, 0, Math.sin((i / 48) * Math.PI * 2) * 0.5]);

/**
 * The footprint on the floor: yellow and filling up while the hands frame it, green once it is
 * locked, with its width written beside it. A pinned spot is a circle.
 */
function Footprint({ floorY }: { floorY: number }) {
  const mark = useStudio((s) => s.mark);
  const group = useRef<Group>(null);
  const live = useRef<Group>(null);
  const locked = useRef<Group>(null);
  const square = useRef<Group>(null);
  const circle = useRef<Group>(null);
  const fill = useRef<MeshBasicMaterial>(null);
  const pulse = useRef<Group>(null);
  const label = useRef<HTMLDivElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const pulseT = useRef(1);
  const said = useRef('');
  useEffect(() => {
    if (mark) pulseT.current = 0;
  }, [mark?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useFrame(({ clock }, dt) => {
    const g = group.current;
    if (!g) return;
    const s = useStudio.getState();
    const framing = studioLive.framing;
    const f = framing ?? (s.mark ? { ...s.mark, progress: 1 } : null);
    // Once a piece is on its way, the ghost of it stands on the mark instead.
    const ghost = !!s.piece && (s.phase === 'fetching' || s.phase === 'placing');
    g.visible = !!f && !ghost;
    if (label.current) label.current.style.display = g.visible ? '' : 'none';
    if (!f || ghost) return;
    const isLive = !!framing;
    const pin = f.kind === 'pin';
    const w = f.width ?? 0.7;
    const d = f.depth ?? (f.width ? Math.min(Math.max(f.width * 0.5, 0.4), 1.1) : 0.7);
    g.position.set(f.x, floorY + 0.02, f.z);
    g.rotation.y = f.yaw_deg * DEG;
    g.scale.set(w, 1, d);
    if (live.current) live.current.visible = isLive;
    if (locked.current) locked.current.visible = !isLive;
    if (square.current) square.current.visible = !pin;
    if (circle.current) circle.current.visible = pin;
    if (fill.current) {
      fill.current.color.set(isLive ? COLOR.frame : COLOR.lock);
      fill.current.opacity = isLive ? 0.1 + 0.3 * f.progress : 0.2 + 0.07 * Math.sin(clock.elapsedTime * 4);
    }
    if (pulse.current) {
      pulseT.current = Math.min(1, pulseT.current + dt / 0.6);
      const p = pulseT.current;
      pulse.current.visible = !isLive && p < 1;
      pulse.current.scale.setScalar(1 + p * 0.7);
    }
    const text = pin ? (isLive ? 'Hold still to pin' : 'Pinned · say what goes here') : isLive ? `${w.toFixed(2)} m · hold still` : `${w.toFixed(2)} m wide · say what goes here`;
    if (label.current && text !== said.current) {
      said.current = text;
      label.current.firstElementChild!.textContent = text;
      label.current.dataset.locked = isLive ? '0' : '1';
    }
    if (bar.current) bar.current.style.transform = `scaleX(${isLive ? f.progress : 0})`;
  });

  return (
    <group ref={group} visible={false}>
      <group ref={square}>
        <mesh rotation={[-Math.PI / 2, 0, 0]} renderOrder={3}>
          <planeGeometry args={[1, 1]} />
          <meshBasicMaterial ref={fill} color={COLOR.frame} transparent opacity={0.2} depthWrite={false} side={DoubleSide} toneMapped={false} />
        </mesh>
        <group ref={live}>
          <Line points={SQUARE} color={COLOR.frame} lineWidth={3} dashed dashSize={0.08} gapSize={0.05} toneMapped={false} />
        </group>
        <group ref={locked}>
          <Line points={SQUARE} color={COLOR.lock} lineWidth={3.5} toneMapped={false} />
          {/* The side the piece will face: a short bar at its front. */}
          <Line points={[[-0.18, 0, 0.5], [0.18, 0, 0.5]]} color="#ffffff" lineWidth={6} toneMapped={false} />
        </group>
        <group ref={pulse} visible={false}>
          <Line points={SQUARE} color={COLOR.lock} lineWidth={2} transparent opacity={0.5} toneMapped={false} />
        </group>
      </group>
      <group ref={circle} visible={false}>
        <Line points={CIRCLE} color={COLOR.lock} lineWidth={3.5} toneMapped={false} />
        <mesh rotation={[-Math.PI / 2, 0, 0]}>
          <circleGeometry args={[0.12, 24]} />
          <meshBasicMaterial color={COLOR.lock} toneMapped={false} />
        </mesh>
      </group>
      <Html position={[0, 0, 0.5]} center zIndexRange={[15, 0]} style={{ pointerEvents: 'none' }}>
        <div ref={label} data-locked="0" className="mt-12 overflow-hidden rounded-full bg-[#f1c84b] font-display text-[15px] font-semibold whitespace-nowrap text-[#06261f] shadow-lg tabular-nums data-[locked='1']:bg-[#2bdc86]">
          <span className="block px-3.5 pt-1 pb-0.5" />
          <div ref={bar} className="h-1 origin-left bg-[#06261f]" style={{ transform: 'scaleX(0)' }} />
        </div>
      </Html>
    </group>
  );
}

/** The piece on its way: a box of its real size on the mark, scanned from floor to top until the model arrives. */
function Ghost({ floorY }: { floorY: number }) {
  const mark = useStudio((s) => s.mark);
  const piece = useStudio((s) => s.piece);
  const phase = useStudio((s) => s.phase);
  const scan = useRef<Mesh>(null);
  const box = useRef<MeshBasicMaterial>(null);
  const show = !!mark && !!piece && (phase === 'fetching' || phase === 'placing');
  useFrame(({ clock }) => {
    if (!show || !piece) return;
    const t = clock.elapsedTime;
    if (scan.current) scan.current.position.y = (0.5 + 0.5 * Math.sin(t * 3.2)) * piece.dims.h;
    if (box.current) box.current.opacity = 0.1 + 0.06 * Math.sin(t * 6);
  });
  if (!show || !mark || !piece) return null;
  const { w, d, h } = piece.dims;
  return (
    <group position={[mark.x, floorY + 0.01, mark.z]} rotation={[0, mark.yaw_deg * DEG, 0]}>
      <mesh position={[0, h / 2, 0]} renderOrder={4}>
        <boxGeometry args={[w, h, d]} />
        <meshBasicMaterial ref={box} color={COLOR.lock} transparent opacity={0.12} depthWrite={false} toneMapped={false} />
        <Edges color={COLOR.lock} lineWidth={2.5} toneMapped={false} />
      </mesh>
      <mesh ref={scan} rotation={[-Math.PI / 2, 0, 0]} renderOrder={5}>
        <planeGeometry args={[w * 1.04, d * 1.04]} />
        <meshBasicMaterial color="#ffffff" transparent opacity={0.35} depthWrite={false} side={DoubleSide} toneMapped={false} />
      </mesh>
      <Html position={[0, h + 0.25, 0]} center zIndexRange={[15, 0]} style={{ pointerEvents: 'none' }}>
        <div className="rounded-full bg-[#2bdc86] px-3.5 py-1 font-display text-[15px] font-semibold whitespace-nowrap text-[#06261f] shadow-lg tabular-nums">
          {piece.label} · {w.toFixed(2)} × {d.toFixed(2)} m
        </div>
      </Html>
    </group>
  );
}

/** Where each hand lands on the floor: a small ring in the hand's colour, and the line between two that frame. */
function HandSpots({ floorY }: { floorY: number }) {
  const a = useRef<Mesh>(null);
  const b = useRef<Mesh>(null);
  useFrame(() => {
    const spots = studioLive.spots;
    [a, b].forEach((ref, i) => {
      const mesh = ref.current;
      if (!mesh) return;
      const s = spots[i];
      mesh.visible = !!s;
      if (!s) return;
      mesh.position.set(s.x, floorY + 0.03, s.z);
      (mesh.material as MeshBasicMaterial).color.set(COLOR[s.side]);
    });
  });
  return (
    <>
      {[a, b].map((ref, i) => (
        <mesh key={i} ref={ref} rotation={[-Math.PI / 2, 0, 0]} visible={false} renderOrder={8}>
          <ringGeometry args={[0.075, 0.12, 40]} />
          <meshBasicMaterial transparent opacity={0.95} depthTest={false} depthWrite={false} toneMapped={false} />
        </mesh>
      ))}
    </>
  );
}

export function StudioScene({ twin, room }: { twin: PropertyTwin; room: Room }) {
  const { center, radius } = useMemo(() => {
    const b = bounds(room.polygon);
    return { center: new Vector3((b.minX + b.maxX) / 2, room.floor_y + FIT.lookUp, (b.minZ + b.maxZ) / 2), radius: Math.max(1.6, Math.hypot(b.maxX - b.minX, b.maxZ - b.minZ) / 2) };
  }, [room]);
  const grid = Math.ceil(radius * 5) * 2;
  return (
    <Canvas orthographic shadows dpr={[1, 1.5]} gl={{ alpha: true, antialias: true }} camera={{ position: [center.x + 30, center.y + 30, center.z + 30], zoom: 50, near: 0.1, far: 500 }} className="touch-none">
      <Surroundings />
      <hemisphereLight args={['#ffffff', '#9db4aa', 1.2]} />
      <Sun center={center} radius={radius} />
      <Rig room={room} center={center} radius={radius} />
      <gridHelper args={[grid, grid * 2, '#2bdc86', '#1f6a55']} position={[center.x, room.floor_y - 0.25, center.z]} material-transparent material-opacity={0.32} />
      <RoomShell room={room} twin={twin} />
      <Pieces twin={twin} roomId={room.room_id} />
      <Remover twin={twin} roomId={room.room_id} />
      <Footprint floorY={room.floor_y} />
      <Ghost floorY={room.floor_y} />
      <HandSpots floorY={room.floor_y} />
    </Canvas>
  );
}
