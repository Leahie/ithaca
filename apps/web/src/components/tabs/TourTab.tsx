'use client';

import type { InventoryItem, PropertyTwin } from '@ithaca/schema';
import { PerformanceMonitor } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Object3D, Vector3 } from 'three';
import { AssetBoundary, Furniture, Ground, HouseLight, HouseModel, RoomShells, houseBounds, poseObject, useHouseLighting, useRebuiltHouse, useSun } from '@/components/house3d/House3D';
import { HouseAO } from '@/components/house3d/HouseAO';
import { LiveArrangeMinimap } from '@/components/house/ArrangeMinimap';
import { HouseLoader } from '@/components/house/HouseLoader';
import { HousePlan } from '@/components/house/HousePlan';
import { SoundTimelapse } from '@/components/sound/SoundTimelapse';
import { TourCamera, type P3, type TourRig } from '@/components/tour/TourCamera';
import { GlassBadge, Notice } from '@/components/ui';
import { api } from '@/lib/api';
import { config } from '@/lib/config';
import { useHouseFurniture, useInventory, useLayouts } from '@/lib/data/hooks';
import { cubicBezier } from '@/lib/geometry/bezier';
import { bounds, centroid, pointInPolygon } from '@/lib/geometry/polygon';
import { handEngine, onHandTick, useHands } from '@/lib/hands/store';
import type { House } from '@/lib/house/house';
import { EYE_M } from '@/lib/house/walk';
import { furnitureOnScreen, previewLayout } from '@/lib/layouts';
import { aerialView, defaultStartSweep, floorSweeps, rotationToward, sweepForRoom, viewForRoom } from '@/lib/showcase/rooms';
import { onSessionEvent, useSession } from '@/lib/store/session';
import { useTour } from '@/lib/store/tour';

declare global {
  interface Window {
    /** Test hooks. E2E reads these; nothing in the app does. */
    __ITHACA__?: Record<string, unknown>;
  }
}

/** Picking a piece by hand: how near the pinch must be to it, as a fraction of the view's height. */
const HAND_GRAB_RADIUS = 0.16;
/** Arranging by hand looks down from this near (m), above where the camera stood. */
const HAND_AERIAL_DISTANCE = 4.2;
/** Holding a piece by hand: how long a lost hand or an opened pinch is forgiven (the tracker
 * flickers), how much nearer another piece must be to take the pointer, the time constant (ms) of
 * the piece's move toward the hand (in time, not per frame: when the house draws slowly the piece
 * must not follow slowly too), and the hand movement (px) that is only trembling.
 * `dwellMs`: how long a hand must rest nearest a piece before the piece is pointed out; a hand on
 * its way across the room passes several, and each pointing-out re-renders the tour and goes to
 * every phone. `shadowMs`: the least time between two redraws of the shadow map while a piece is
 * carried (it covers the whole house, so not once per frame). */
const HAND_HOLD = { graceMs: 450, steal: 0.6, followMs: 67, still: 1.5, dwellMs: 120, shadowMs: 33 } as const;

// Phone look and zoom are eased: quick off the mark, soft landing (an ease-out bezier).
const LOOK_EASE = cubicBezier(0.22, 0.8, 0.3, 1);
const LOOK_EASE_MS = 170;
/** Facing a spot on the phone's plan from the scan point the camera already stands on. */
const TURN_IN_PLACE_DEG_PER_S = 140;
const ZOOM_EASE_MS = 280;
/** How long camera moves take (ms). */
const FLY_MS = { goto: 1400, step: 650, outside: 1300, above: 1200, back: 900, pick: 900 } as const;

/** After a drag ends, the saved pose arrives with the furniture reload; a refused move must still go back. */
const DRAG_SETTLE_MS = 2500;

const exposeForTests = (key: string, value: unknown) => {
  window.__ITHACA__ = { ...window.__ITHACA__, [key]: value };
};

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** Inside the canvas: hands the tour what it needs from the light (furniture it moves itself must re-cast its shadow). */
function ShadowHandle({ invalidate }: { invalidate: { current: (() => void) | null } }) {
  const lighting = useHouseLighting();
  useEffect(() => {
    invalidate.current = lighting.invalidate;
    return () => {
      invalidate.current = null;
    };
  }, [invalidate, lighting]);
  return null;
}

/**
 * The tour: the house rebuilt from the floor plan (components/house3d), walked through at eye
 * height under the sun of the session's moment. `viewer.goto` stands the camera on the room's
 * best scan position; the room list can also ask for the outside view. The chosen layout's
 * furniture stands at its stored poses, as they are (A5).
 */
export function TourTab({ twin, active, soundBar = true }: { twin: PropertyTwin; active: boolean; /** Show the sound and time bar. Off, it still plays: the phone has its controls. */ soundBar?: boolean }) {
  const [error, setError] = useState<string | null>(null);
  const { house: rebuilt, known } = useRebuiltHouse(twin, setError);
  const datetime = useSession((s) => s.viewer.datetime);
  const sun = useSun(twin, rebuilt, datetime);
  const bounds3 = useMemo(() => houseBounds(twin), [twin]);

  const rigRef = useRef<TourRig | null>(null);
  const houseRef = useRef<House | null>(null);
  const shadows = useRef<(() => void) | null>(null);
  /** The house is on screen and the camera can move: what the Showcase's "playing" was. */
  const [ready, setReady] = useState(false);
  const [lid, setLid] = useState<{ inside: boolean; topStorey: number | null }>({ inside: false, topStorey: null });
  /** The first view is in place: the loading screen fades out, then goes. */
  const [revealed, setRevealed] = useState(false);
  const [loaderGone, setLoaderGone] = useState(false);
  const firstMove = useRef(true);
  /** Until when (performance.now) the guide is putting a layout on show: the room is looked at
   * from above and turned once round, so the goto that comes with it must not take the camera inside. */
  const layoutShow = useRef(0);
  /** The scan point of the room a layout is on show in: where the camera goes when it comes down
   * from the view above (not back to where it stood before the show). */
  const showSweep = useRef<string | null>(null);
  /** The sweep the camera stands on (or last stood on). */
  const here = useRef<string | null>(null);
  /** The view from above while arranging furniture: what it shows (see aerialKey), or null. */
  const aerial = useRef<string | null>(null);
  /** A view from above that a goto, walk, step or "Outside" took over from: not shown again. */
  const aerialDismissed = useRef<string | null>(null);
  /** Bumped by every new walk and every goto: a walk in progress stops at its next step. */
  const walkToken = useRef(0);
  /** A move the tour itself is making (a walk, an aerial view) that a step must wait for. */
  const busy = useRef(false);

  const roomId = useSession((s) => s.viewer.room_id);
  const sweepId = useSession((s) => s.viewer.sweep_id);
  const gotoSeq = useSession((s) => s.viewer.goto_seq);
  const selectedLayoutId = useSession((s) => s.viewer.selected_layout_id);
  const layouts = useLayouts(twin.property_id);
  const furniture = useHouseFurniture(twin.property_id);
  const inventory = useInventory();
  const outsideSeq = useTour((s) => s.outsideSeq);

  const sweepsById = useMemo(() => new Map(twin.sweeps.map((s) => [s.sweep_id, s])), [twin.sweeps]);
  const sweepPosition = useCallback((id: string) => sweepsById.get(id)?.position, [sweepsById]);
  const moving = () => busy.current || !!rigRef.current?.moving;
  /** Something else takes the camera: a walk in progress stops at its next step and lets go. */
  const takeOver = () => {
    ++walkToken.current;
    busy.current = false;
  };

  // Where the camera is: inside, outside. The room list shows it. The view from above while
  // arranging is still the room.
  useEffect(() => {
    useTour.getState().setPlace(ready ? (lid.inside || lid.topStorey !== null ? 'inside' : 'outside') : null);
  }, [ready, lid]);
  useEffect(() => () => useTour.getState().setPlace(null), []);

  /** Stand on a sweep (instant, or flying), facing a direction if given. */
  const moveTo = useCallback(
    (target: string, instant: boolean, facing?: { x: number; y: number }) => {
      const rig = rigRef.current;
      const at = sweepPosition(target);
      if (!rig || !at) return Promise.resolve();
      // Already standing there: just turn.
      if (facing && target === here.current && rig.place === 'inside' && !rig.moving) return rig.turnTo(facing, TURN_IN_PLACE_DEG_PER_S);
      here.current = target;
      return rig.standAt(at, facing ?? null, instant ? 0 : FLY_MS.goto);
    },
    [sweepPosition],
  );

  /** The room a point stands in (its storey's floor near the feet), if any. */
  const roomAt = useCallback(
    (x: number, feet: number, z: number) => twin.rooms.find((r) => Math.abs(r.floor_y - feet) < 0.8 && pointInPolygon([x, z], r.polygon)),
    [twin.rooms],
  );
  /** The scan position nearest a point in a room. */
  const nearestSweep = useCallback(
    (roomIdOf: string, x: number, z: number) => floorSweeps(twin, roomIdOf).reduce<(typeof twin.sweeps)[number] | undefined>((best, s) => (!best || Math.hypot(s.position.x - x, s.position.z - z) < Math.hypot(best.position.x - x, best.position.z - z) ? s : best), undefined),
    [twin],
  );

  // The user walking: when they come into another room, the session follows (same event as a click
  // on the room list, A1), naming the scan position nearest them there.
  const follow = useCallback(
    (x: number, eyeY: number, z: number) => {
      const room = roomAt(x, eyeY - EYE_M, z);
      if (!room) return;
      const s = useSession.getState();
      if (room.room_id === s.viewer.room_id) return;
      const sweep = nearestSweep(room.room_id, x, z);
      if (!sweep) return;
      here.current = sweep.sweep_id;
      s.dispatch('viewer.goto', { sweep_id: sweep.sweep_id });
    },
    [roomAt, nearestSweep],
  );
  useEffect(() => {
    if (!ready) return;
    let last = 0;
    return rigRef.current?.onPose((pose, byUser) => {
      if (!byUser || pose.place !== 'inside') return;
      const now = performance.now();
      if (now - last < 250) return;
      last = now;
      follow(pose.x, pose.y, pose.z);
    });
  }, [ready, follow]);

  // A click on a spot: inside, go to the scan position nearest it (as in a Matterport tour);
  // from outside, go in there.
  const onPick = useCallback(
    (point: Vector3) => {
      const rig = rigRef.current;
      if (!rig || rig.moving) return;
      const room = roomAt(point.x, point.y, point.z) ?? twin.rooms.filter((r) => pointInPolygon([point.x, point.z], r.polygon) && r.floor_y <= point.y + 0.3).sort((a, b) => b.floor_y - a.floor_y)[0];
      if (!room) return;
      const sweep = nearestSweep(room.room_id, point.x, point.z);
      if (!sweep) return;
      takeOver();
      aerialDismissed.current = aerial.current;
      aerial.current = null;
      here.current = sweep.sweep_id;
      void rig.standAt(sweep.position, null, FLY_MS.pick).then(() => follow(sweep.position.x, sweep.position.y, sweep.position.z));
    },
    [roomAt, nearestSweep, follow, twin.rooms],
  );

  // "Outside" from the room list: around the building.
  useEffect(() => {
    if (!ready || outsideSeq === 0) return;
    aerialDismissed.current = aerial.current;
    aerial.current = null;
    takeOver();
    void rigRef.current?.outside(FLY_MS.outside);
  }, [ready, outsideSeq]);

  // viewer.goto: stand on the room's best scan position. Also runs once when the tour is ready:
  // it goes to the room the session already names, or into the largest room downstairs.
  useEffect(() => {
    if (!ready) return;
    const first = firstMove.current;
    firstMove.current = false;
    takeOver(); // a goto replaces a walk in progress
    // Going to a room (not to one sweep): stand where the room shows best and face into it. The
    // agent sends that view with the goto; for a click on the room list it is worked out here.
    const asked = useSession.getState().viewer.view;
    const best =
      !sweepId && roomId
        ? asked
          ? { sweep_id: asked.at_sweep_id, lookAt: asked.look_at }
          : (() => {
              const v = viewForRoom(twin, roomId);
              return v ? { sweep_id: v.sweep.sweep_id, lookAt: v.lookAt } : undefined;
            })()
        : undefined;
    const target = sweepId ?? best?.sweep_id ?? (roomId ? sweepForRoom(twin, roomId)?.sweep_id : undefined) ?? (first ? defaultStartSweep(twin)?.sweep_id : undefined);
    if (!target) {
      if (first) setRevealed(true);
      return;
    }
    // The user walked here themselves: the session is following the camera, not moving it.
    if (!first && sweepId && target === here.current) return;
    // A layout going on show is looked at from above (see aerialTarget), not from inside.
    if (!first && performance.now() < layoutShow.current) {
      showSweep.current = target;
      return;
    }
    showSweep.current = null;
    if (aerial.current) {
      aerialDismissed.current = aerial.current;
      aerial.current = null;
    }
    const from = sweepPosition(target);
    void moveTo(target, first, best && from ? rotationToward(from, best.lookAt) : undefined);
    if (first) setTimeout(() => setRevealed(true), 150);
  }, [ready, gotoSeq]); // eslint-disable-line react-hooks/exhaustive-deps

  // The loading screen never stays for good.
  useEffect(() => {
    if (!ready || revealed) return;
    const t = setTimeout(() => setRevealed(true), 4000);
    return () => clearTimeout(t);
  }, [ready, revealed]);

  // viewer.walk: step the camera from sweep to sweep along the route, facing the way it is going,
  // and face the room on arrival. A new walk, a goto or a click on the room list stops it.
  useEffect(() => {
    if (!ready) return;
    return onSessionEvent((event) => {
      if (event.type !== 'viewer.walk') return;
      const token = ++walkToken.current;
      showSweep.current = null;
      const { sweep_ids, look_at, duration_s } = event.payload;
      const steps = sweep_ids.filter((id) => sweepsById.has(id));
      if (!steps.length) return;
      const stepMs = Math.max(700, Math.min(1600, (duration_s * 1000) / steps.length));
      void (async () => {
        busy.current = true;
        try {
          for (let i = 0; i < steps.length; i++) {
            const rig = rigRef.current;
            if (walkToken.current !== token || !rig) return;
            const id = steps[i]!;
            const at = sweepPosition(id)!;
            const nextId = steps[i + 1];
            const ahead = nextId ? sweepPosition(nextId) : look_at;
            here.current = id;
            await rig.standAt(at, ahead ? rotationToward(at, ahead) : null, i === 0 ? 1200 : stepMs);
          }
        } finally {
          if (walkToken.current === token) busy.current = false;
        }
      })();
    });
  }, [ready, sweepsById, sweepPosition]);

  // viewer.look and viewer.zoom (the phone's drag and pinch), smoothed. Each message is not applied
  // at once: it is played out over a short time along a bezier ease-out curve, so the uneven
  // arrival of messages over the network becomes one even motion.
  useEffect(() => {
    if (!ready) return;
    type Ease = { start: number; ms: number; yaw: number; pitch: number; zoom: number; done: number; slow?: boolean };
    let eases: Ease[] = [];
    let live = true;
    let frame: number | null = null;
    const tick = (now: number) => {
      frame = null;
      let yaw = 0;
      let pitch = 0;
      let zoom = 0;
      for (const e of eases) {
        if (e.slow && moving()) {
          e.start = now - e.done * e.ms; // the camera is flying to the room: the turn waits, and goes on from where it was
          continue;
        }
        if (now < e.start) continue; // a slow turn that waits for the walk to the room
        const at = e.slow ? Math.min(1, (now - e.start) / e.ms) : LOOK_EASE((now - e.start) / e.ms);
        const step = at - e.done;
        e.done = at;
        yaw += e.yaw * step;
        pitch += e.pitch * step;
        zoom += e.zoom * step;
      }
      eases = eases.filter((e) => e.done < 1);
      if (yaw || pitch) rigRef.current?.turn(yaw, pitch);
      if (zoom) rigRef.current?.zoom(zoom);
      if (eases.length && live) frame = requestAnimationFrame(tick);
    };
    const off = onSessionEvent((event) => {
      if (useSession.getState().viewer.mode !== 'tour') return;
      const start = performance.now();
      if (event.type === 'viewer.look') {
        const { ms, delay_ms } = event.payload;
        // The guide's slow turn around a room: even speed, and a hand on the view takes over.
        if (ms || event.source !== 'agent') eases = eases.filter((e) => !e.slow);
        eases.push({ start: start + (delay_ms ?? 0), ms: ms ?? LOOK_EASE_MS, yaw: event.payload.yaw_delta_deg, pitch: event.payload.pitch_delta_deg, zoom: 0, done: 0, slow: !!ms });
      } else if (event.type === 'viewer.zoom') {
        eases.push({ start, ms: ZOOM_EASE_MS, yaw: 0, pitch: 0, zoom: event.payload.delta, done: 0 });
      } else if (event.type === 'viewer.walk' || event.type === 'viewer.step' || (event.type === 'viewer.goto' && performance.now() >= layoutShow.current)) {
        // Going somewhere ends the guide's slow turn.
        eases = eases.filter((e) => !e.slow);
        return;
      } else return;
      frame ??= requestAnimationFrame(tick);
    });
    return () => {
      live = false;
      if (frame) cancelAnimationFrame(frame);
      off();
    };
  }, [ready]); // eslint-disable-line react-hooks/exhaustive-deps

  // viewer.step (the phone's joystick): hop to the neighbouring scan position that lies most nearly
  // ahead of (or behind) the way the camera faces, keeping the camera's direction.
  useEffect(() => {
    if (!ready) return;
    const hop = (angleDeg: number, left: number) => {
      const rig = rigRef.current;
      if (!rig) return;
      if (showSweep.current && !moving()) {
        // Looking down on a layout: the first step comes down into that room, not back to where
        // the camera stood before the show.
        const into = showSweep.current;
        showSweep.current = null;
        takeOver();
        aerialDismissed.current = aerial.current;
        aerial.current = null;
        here.current = into;
        void rig.standAt(sweepPosition(into)!, null, FLY_MS.back);
        return;
      }
      const from = here.current ? sweepPosition(here.current) : undefined;
      if (!from || moving() || rig.place !== 'inside') return;
      const neighbors = sweepsById.get(here.current!)?.neighbors ?? [];
      if (!neighbors.length) return;
      // Showcase yaw: 0 looks along -Z, positive turns left.
      const pose = rig.pose();
      const yaw = (pose.yaw_deg * Math.PI) / 180;
      const angle = (angleDeg * Math.PI) / 180;
      const fx = -Math.sin(yaw) * Math.cos(angle) + Math.cos(yaw) * Math.sin(angle);
      const fz = -Math.cos(yaw) * Math.cos(angle) - Math.sin(yaw) * Math.sin(angle);
      let best: string | null = null;
      let bestScore = 0.35; // within about 70 degrees of that way
      for (const id of neighbors) {
        const to = sweepPosition(id);
        if (!to) continue;
        const dx = to.x - from.x;
        const dz = to.z - from.z;
        const dist = Math.hypot(dx, dz);
        if (dist < 0.2) continue;
        // Straight that way first, then the nearer one.
        const score = (dx * fx + dz * fz) / dist - dist * 0.03;
        if (score > bestScore) {
          bestScore = score;
          best = id;
        }
      }
      if (!best) return;
      const target = best;
      takeOver();
      here.current = target;
      void rig.standAt(sweepPosition(target)!, { x: pose.pitch_deg, y: pose.yaw_deg }, FLY_MS.step).then(() => {
        if (left > 1) hop(angleDeg, left - 1);
      });
    };
    return onSessionEvent((event) => {
      if (event.type !== 'viewer.step' || useSession.getState().viewer.mode !== 'tour') return;
      if (aerial.current) {
        aerialDismissed.current = aerial.current;
        aerial.current = null;
      }
      hop(event.payload.angle_deg ?? (event.payload.direction === 'back' ? 180 : 0), event.payload.steps ?? 1);
    });
  }, [ready, sweepsById, sweepPosition]); // eslint-disable-line react-hooks/exhaustive-deps

  // The house's furniture (or the layout being tried in its room).
  const itemsById = useMemo(() => new Map<string, InventoryItem>((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const placements = useMemo(
    () =>
      furnitureOnScreen(furniture.data, previewLayout(layouts.data, selectedLayoutId)).flatMap((p) => {
        const item = itemsById.get(p.item_id);
        return item ? [{ ...p, url: api().assetUrl(item.glb_url) }] : [];
      }),
    [furniture.data, layouts.data, itemsById, selectedLayoutId],
  );
  const roomPieces = useMemo(() => placements.filter((p) => p.room_id === roomId), [placements, roomId]);
  const arrangingFromPhone = useSession((s) => s.peers.phone && s.remoteView === 'furniture');

  // Each piece's root, as it stands in the scene: drags and hands move it there before the move is saved.
  const pieces = useRef(new Map<string, Object3D>());
  const [loadedCount, setLoadedCount] = useState(0);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const onObject = useCallback((id: string, object: Object3D | null) => {
    if (object) pieces.current.set(id, object);
    else pieces.current.delete(id);
    setLoadedCount(pieces.current.size);
  }, []);
  useEffect(() => {
    if (loadedCount && loadedCount === placements.length) exposeForTests('tourPlacedItemIds', placements.map((p) => p.item_id));
  }, [loadedCount, placements]);
  /** Put a piece somewhere (not saved), and let its shadow follow. */
  const posePiece = useCallback((id: string, x: number, y: number, z: number, yawDeg: number) => {
    const object = pieces.current.get(id);
    if (!object) return;
    poseObject(object, x, y, z, yawDeg);
    shadows.current?.();
  }, []);

  // furniture.drag: the piece follows the finger on the phone's plan before the move is saved.
  const placementsRef = useRef(placements);
  placementsRef.current = placements;
  /** The layout being tried (not kept yet), if any: its pieces are what the room shows. */
  const triedRef = useRef<ReturnType<typeof previewLayout>>(undefined);
  triedRef.current = previewLayout(layouts.data, selectedLayoutId);
  useEffect(() => {
    let dragged: string | null = null;
    let settleTimer: ReturnType<typeof setTimeout> | null = null;
    const restore = (id: string) => {
      const p = placementsRef.current.find((x) => x.item_id === id);
      if (p) posePiece(id, p.position.x, p.position.y, p.position.z, p.yaw_deg);
    };
    const off = onSessionEvent((event) => {
      if (event.type !== 'furniture.drag') return;
      const { item_id, x = 0, z = 0, yaw_deg = 0 } = event.payload;
      if (settleTimer) clearTimeout(settleTimer);
      settleTimer = null;
      if (!item_id) {
        const id = dragged;
        dragged = null;
        if (id) settleTimer = setTimeout(() => restore(id), DRAG_SETTLE_MS);
        return;
      }
      if (dragged && dragged !== item_id) restore(dragged);
      dragged = item_id;
      const object = pieces.current.get(item_id);
      if (object) posePiece(item_id, x, object.position.y, z, yaw_deg);
    });
    return () => {
      if (settleTimer) clearTimeout(settleTimer);
      off();
    };
  }, [posePiece]);

  // Arranging from the phone: while the phone asks for the room from above, or a layout is being
  // tried in a room, the big screen looks down on it, turned like the phone's plan, and follows the
  // selected piece (tinted blue) each time it is moved or turned. Turning it off goes back inside,
  // to where the camera stood. A goto, walk or step takes the camera from it at once.
  const [arranging, setArranging] = useState<string | null>(null);
  const [overview, setOverview] = useState<string | null>(null);
  useEffect(
    () =>
      onSessionEvent((event) => {
        if ((event.type === 'design.showVariants' || event.type === 'layout.select') && event.source === 'agent') layoutShow.current = performance.now() + 3000;
        if (event.type === 'viewer.goto' && event.source === 'agent' && performance.now() < layoutShow.current) return; // part of the show
        if (event.type === 'furniture.select') setArranging(event.payload.item_id ?? null);
        else if (event.type === 'furniture.overview') setOverview(event.payload.room_id ?? null);
        else if (event.type === 'remote.view' && event.payload.view !== 'furniture') {
          setArranging(null);
          setOverview(null);
        } else if ((event.type === 'viewer.goto' || event.type === 'viewer.walk' || event.type === 'viewer.step') && aerial.current) {
          aerialDismissed.current = aerial.current;
          aerial.current = null;
        }
      }),
    [],
  );

  // Hands arrange furniture. While hand control is on, the big screen looks down, from close, on
  // the part of the room the camera stood in. A piece that has been picked up stays the chosen
  // one (blue) after it is put down: the next pinch, anywhere, picks it up again, and only a pinch
  // right on another piece changes the choice. Before that, the piece nearest a hand is pointed
  // out; a pinch picks it up, the hand slides it over the floor, a second pinching hand turns it
  // (as the line between the hands turns), and letting go saves it where it stands.
  const handsOn = useHands((s) => s.status === 'on');
  useEffect(() => {
    if (!ready || !handsOn || !active) return;
    const rig = rigRef.current;
    if (!rig) return;
    const onScreen = (x: number, y: number, z: number) => rig.project(x, y, z);
    // x, z: where the piece is drawn; tx, tz: where the hand has taken it (the drawn one follows, smoothly).
    // shownYaw: the turn the piece is drawn at (yaw is where the hands have turned it to).
    let held: { id: string; hand: number; x: number; y: number; z: number; tx: number; tz: number; yaw: number; shownYaw: number; px: number; py: number; twist: number | null; lostAt: number | null } | null = null;
    let pointed: string | null = null;
    /** The piece nearest the hand and since when: it is pointed out once the hand has rested on it. */
    let aimed: string | null = null;
    let aimedAt = 0;
    /** The chosen piece: picked up once, it stays chosen until another is pinched. */
    let locked: string | null = null;
    const wasPinching = new Set<number>();
    let lastTick = performance.now();
    let shadowAt = 0;
    let shadowDue = false;
    /** Move the carried piece. It is drawn there at once; its shadow follows at most every shadowMs. */
    const carry = (id: string, x: number, y: number, z: number, yawDeg: number, now: number) => {
      const object = pieces.current.get(id);
      if (!object) return;
      poseObject(object, x, y, z, yawDeg);
      shadowDue = true;
      if (now - shadowAt >= HAND_HOLD.shadowMs) {
        shadowAt = now;
        shadowDue = false;
        shadows.current?.();
      }
    };
    const point = (id: string | null) => {
      if (id === pointed) return;
      pointed = id;
      useSession.getState().dispatch('furniture.select', { item_id: id });
    };
    const drop = () => {
      const h = held;
      held = null;
      if (!h) return;
      const before = placementsRef.current.find((p) => p.item_id === h.id);
      const floor = twin.rooms.find((r) => r.room_id === before?.room_id)?.floor_id;
      const yaw = ((h.yaw % 360) + 360) % 360;
      posePiece(h.id, h.tx, h.y, h.tz, h.yaw);
      // A layout that is only being tried is not in the house yet: a piece moved out of it would
      // jump back when the screen reloads. Moving a piece by hand takes the layout, then moves it.
      const tried = triedRef.current;
      const keep = tried && tried.room_id === before?.room_id ? api().selectLayout(tried.layout_id) : Promise.resolve(null);
      keep
        .then(() => api().placePiece(twin.property_id, h.id, { x: h.tx, z: h.tz, yaw_deg: yaw, floor_id: floor, room_id: null, snap: false }))
        .then(() => useSession.getState().dispatch('layout.changed', { layout_id: null }))
        .catch((err: unknown) => {
          // Outside every room, or the server is away: it goes back to where it stood.
          console.warn('[tour] the piece could not be put there', err);
          if (before) posePiece(h.id, before.position.x, before.position.y, before.position.z, before.yaw_deg);
        });
    };
    const off = onHandTick(() => {
      const { w, h } = rig.size();
      const now = performance.now();
      const dt = Math.min(now - lastTick, 100);
      lastTick = now;
      const seen = handEngine.hands;
      const hands = seen.filter((x) => !x.stale);
      if (held) {
        const grip = held;
        if (!pieces.current.has(grip.id)) return drop();
        let hand = hands.find((x) => x.id === grip.hand && x.pinching);
        if (!hand) {
          // The tracker lost the hand for a moment and found it again as a new one: still the same grip.
          const again = grip.lostAt !== null ? hands.filter((x) => x.pinching).sort((a, b) => Math.hypot(a.u * w - grip.px, a.v * h - grip.py) - Math.hypot(b.u * w - grip.px, b.v * h - grip.py))[0] : undefined;
          if (again && Math.hypot(again.u * w - grip.px, again.v * h - grip.py) < 0.25 * h) {
            grip.hand = again.id;
            grip.px = again.u * w;
            grip.py = again.v * h;
            hand = again;
          }
        }
        if (!hand) {
          grip.lostAt ??= now;
          if (now - grip.lostAt > HAND_HOLD.graceMs) return drop();
          return; // a flicker: the piece waits where it is
        }
        grip.lostAt = null;
        const px = hand.u * w;
        const py = hand.v * h;
        const dx = px - grip.px;
        const dy = py - grip.py;
        if (Math.hypot(dx, dy) >= HAND_HOLD.still) {
          grip.px = px;
          grip.py = py;
          // How the piece's place on screen moves per metre along x and along z, here and now;
          // from that, the floor move that keeps it under the hand.
          const o = onScreen(grip.tx, grip.y, grip.tz);
          const a = onScreen(grip.tx + 0.5, grip.y, grip.tz);
          const b = onScreen(grip.tx, grip.y, grip.tz + 0.5);
          const ax = (a[0] - o[0]) * 2;
          const ay = (a[1] - o[1]) * 2;
          const bx = (b[0] - o[0]) * 2;
          const by = (b[1] - o[1]) * 2;
          const det = ax * by - bx * ay;
          if (Math.abs(det) > 25) {
            const mx = (dx * by - bx * dy) / det;
            const mz = (ax * dy - dx * ay) / det;
            const far = Math.hypot(mx, mz);
            const k = far > 0.4 ? 0.4 / far : 1; // a jump in the tracking must not throw the piece
            grip.tx += mx * k;
            grip.tz += mz * k;
          }
        }
        const other = hands.find((x) => x.id !== grip.hand && x.pinching);
        if (other) {
          const angle = (Math.atan2((other.v - hand.v) * h, (other.u - hand.u) * w) * 180) / Math.PI;
          if (grip.twist !== null) grip.yaw -= ((angle - grip.twist + 540) % 360) - 180; // the screen turns clockwise, yaw the other way
          grip.twist = angle;
        } else {
          grip.twist = null;
        }
        const k = 1 - Math.exp(-dt / HAND_HOLD.followMs);
        let x = grip.x + (grip.tx - grip.x) * k;
        let z = grip.z + (grip.tz - grip.z) * k;
        // Arrived: it stops there, and a piece that is not moving is not drawn (or shadowed) again.
        if (Math.hypot(grip.tx - x, grip.tz - z) < 0.002) {
          x = grip.tx;
          z = grip.tz;
        }
        if (x !== grip.x || z !== grip.z || grip.yaw !== grip.shownYaw) {
          grip.x = x;
          grip.z = z;
          grip.shownYaw = grip.yaw;
          carry(grip.id, x, grip.y, z, grip.yaw, now);
        } else if (shadowDue && now - shadowAt >= HAND_HOLD.shadowMs) {
          shadowAt = now;
          shadowDue = false;
          shadows.current?.(); // the shadow catches up with where the piece came to rest
        }
        return;
      }
      const hand = hands.find((x) => x.pinching) ?? hands[0];
      const started = !!hand && hand.pinching && !wasPinching.has(hand.id); // a pinch counts once, when it closes
      // From every hand in view, also one the tracker missed for a moment: its pinch is still the same pinch.
      wasPinching.clear();
      for (const x of seen) if (x.pinching) wasPinching.add(x.id);
      if (!hand) return;
      const px = hand.u * w;
      const py = hand.v * h;
      let best: (typeof placementsRef.current)[number] | null = null;
      let bestD = HAND_GRAB_RADIUS * h;
      for (const p of placementsRef.current) {
        if (!pieces.current.has(p.item_id)) continue;
        // Its middle, not its feet: that is where a hand reaches for a tall piece.
        const tall = itemsById.get(p.item_id)?.dims_m.h ?? 0;
        const [sx, sy] = onScreen(p.position.x, p.position.y + tall / 2, p.position.z);
        // The piece the hand is already on keeps it unless another is clearly nearer, so the
        // choice does not flip between neighbours as the hand trembles.
        const d = Math.hypot(sx - px, sy - py) * (p.item_id === aimed ? HAND_HOLD.steal : 1);
        if (d < bestD) {
          bestD = d;
          best = p;
        }
      }
      if (locked && !placementsRef.current.some((p) => p.item_id === locked)) locked = null; // it was removed
      if (best && best.item_id !== aimed) {
        aimed = best.item_id;
        aimedAt = now;
      }
      // Pointed out once the hand has rested on it: not each piece a moving hand passes.
      if (!locked && best && now - aimedAt >= HAND_HOLD.dwellMs) point(best.item_id);
      if (!started) return;
      // A pinch right on a piece picks that one; anywhere else, the chosen one.
      const take = best ?? placementsRef.current.find((p) => p.item_id === locked) ?? null;
      if (!take) return;
      locked = take.item_id;
      aimed = take.item_id;
      point(take.item_id); // a pinch does not wait
      held = { id: take.item_id, hand: hand.id, x: take.position.x, y: take.position.y, z: take.position.z, tx: take.position.x, tz: take.position.z, yaw: take.yaw_deg, shownYaw: take.yaw_deg, px, py, twist: null, lostAt: null };
    });
    return () => {
      off();
      drop();
      point(null);
    };
  }, [ready, handsOn, active, twin]); // eslint-disable-line react-hooks/exhaustive-deps

  /** What to look down on: a key that changes when the view must, where, and in which room. 'wait': a piece just added is not loaded yet. */
  const aerialTarget = useMemo(() => {
    // A layout being tried comes first: its whole room from above, whoever else wants the camera.
    const tried = previewLayout(layouts.data, selectedLayoutId);
    const triedRoom = tried && twin.rooms.find((r) => r.room_id === tried.room_id);
    if (tried && triedRoom) {
      const [x, z] = centroid(triedRoom.polygon);
      return { key: `layout:${tried.layout_id}`, at: { x, y: triedRoom.floor_y, z }, room: triedRoom };
    }
    if (handsOn && active && roomId) {
      // By hand the camera stays put: it must not chase each piece a hand passes.
      const room = twin.rooms.find((r) => r.room_id === roomId);
      const stood = here.current ? sweepPosition(here.current) : undefined;
      if (room) {
        const [cx, cz] = centroid(room.polygon);
        return { key: `hands:${roomId}`, at: { x: stood?.x ?? cx, y: room.floor_y, z: stood?.z ?? cz }, room, close: HAND_AERIAL_DISTANCE };
      }
    }
    if (overview && arranging) {
      const p = placements.find((x) => x.item_id === arranging);
      const room = p && twin.rooms.find((r) => r.room_id === p.room_id);
      if (!p || !room) return 'wait' as const;
      return { key: `${p.item_id}@${p.position.x.toFixed(2)},${p.position.z.toFixed(2)}`, at: p.position, room };
    }
    const room = overview ? twin.rooms.find((r) => r.room_id === overview) : undefined;
    if (!room) return null;
    const [x, z] = centroid(room.polygon);
    return { key: `room:${room.room_id}`, at: { x, y: room.floor_y, z }, room };
  }, [arranging, overview, placements, layouts.data, selectedLayoutId, twin.rooms, handsOn, active, roomId, sweepPosition]);
  useEffect(() => {
    if (!ready || aerialTarget === 'wait') return;
    const rig = rigRef.current;
    if (!rig) return;
    if (!aerialTarget) {
      aerialDismissed.current = null;
      if (!aerial.current) return;
      aerial.current = null;
      const back = showSweep.current ?? here.current ?? (roomId ? sweepForRoom(twin, roomId)?.sweep_id : undefined);
      showSweep.current = null;
      if (back) {
        here.current = back;
        void rig.standAt(sweepPosition(back)!, null, FLY_MS.back);
      }
      return;
    }
    const { key, at, room } = aerialTarget;
    const close = 'close' in aerialTarget ? aerialTarget.close : undefined;
    if (key === aerial.current || key === aerialDismissed.current) return;
    aerial.current = key;
    const b = bounds(room.polygon);
    const view = aerialView(at as P3, Math.max(b.maxX - b.minX, b.maxZ - b.minZ), close);
    // The floors above would hide the room.
    const storey = houseRef.current?.storeyAt(room.floor_y) ?? null;
    void (async () => {
      while (busy.current) await sleep(100);
      if (aerial.current !== key) return; // a newer move of the piece, or a goto, came first
      await rig.above(view, storey, FLY_MS.above);
    })();
  }, [ready, aerialTarget]); // eslint-disable-line react-hooks/exhaustive-deps

  const onAssetError = useCallback((message: string) => setPlaceError(message), []);
  const onHouse = useCallback((h: House | null) => {
    houseRef.current = h;
    if (h) setReady(true);
  }, []);
  const onShellsReady = useCallback(() => setReady(true), []);

  // Resolution follows the frame rate, as in the Sun Lab.
  const [dprFactor, setDprFactor] = useState(0.5);
  const maxDpr = typeof window === 'undefined' ? 1 : Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  const dpr = 1 + (maxDpr - 1) * dprFactor;

  return (
    <div className="relative h-full w-full bg-black" data-testid="tour" data-sdk-ready={ready} data-active={active}>
      {/* flat: the house's light is worked out in its own shaders, with no tone mapping on top. */}
      <Canvas flat frameloop={active ? 'always' : 'never'} dpr={dpr} camera={{ fov: 72, near: 0.05, far: 3000 }} className="touch-none">
        <PerformanceMonitor flipflops={3} onChange={({ factor }) => setDprFactor(Math.round(factor * 4) / 4)} onFallback={() => setDprFactor(0)} />
        {known && (
          <HouseLight twin={twin} openings={twin.openings} sun={sun} cutOpenings={!rebuilt} lamps="work">
            <ShadowHandle invalidate={shadows} />
            <HouseAO enabled={lid.inside} />
            <TourCamera rig={rigRef} house={houseRef} center={bounds3.center} radius={bounds3.radius} active={active} onPick={onPick} onLid={setLid} />
            {rebuilt ? (
              <HouseModel url={rebuilt.url} info={rebuilt.info} inside={lid.inside} topStorey={lid.topStorey} onAssetError={setError} onHouse={onHouse} />
            ) : (
              <>
                <RoomShells rooms={twin.rooms} onReady={onShellsReady} />
                <Ground twin={twin} />
              </>
            )}
            {placements.map((p) => (
              <AssetBoundary key={p.item_id} label="A furniture model" onError={onAssetError}>
                <Suspense fallback={null}>
                  <Furniture item={p} selected={p.item_id === arranging} onObject={onObject} />
                </Suspense>
              </AssetBoundary>
            ))}
          </HouseLight>
        )}
      </Canvas>
      {/* Covers the start, then fades out. */}
      {!loaderGone && (
        <div className="absolute inset-0 z-20" onTransitionEnd={() => revealed && setLoaderGone(true)}>
          <HouseLoader label={!known ? 'Starting the tour…' : !ready ? 'Loading the house…' : 'Opening the house…'} leaving={revealed} />
        </div>
      )}
      {error && (
        <div className="absolute top-12 left-3 z-30 w-[360px] max-w-[90%]">
          <Notice tone="error" title="The house could not be shown">
            {error} If this is a fixture, check that Git LFS files are pulled.
          </Notice>
        </div>
      )}
      {ready && revealed && <HousePlan twin={twin} />}
      {ready && revealed && arrangingFromPhone && roomId && (
        <div className="absolute bottom-4 left-3 z-10">
          <LiveArrangeMinimap subscribe={(fn) => rigRef.current?.onPose((pose) => fn({ x: pose.x, z: pose.z, yaw_deg: pose.yaw_deg })) ?? (() => {})} twin={twin} roomId={roomId} pieces={roomPieces} items={itemsById} selected={arranging} />
        </div>
      )}
      <div className="pointer-events-none absolute top-16 right-3 flex flex-wrap justify-end gap-1.5">
        {loadedCount > 0 && <GlassBadge tone="leaf">{loadedCount} items placed</GlassBadge>}
        {placeError && (
          <GlassBadge tone="danger" title={placeError}>
            Furniture could not be placed
          </GlassBadge>
        )}
      </div>
      {config.features.has('sound') && ready && revealed && (
        <div className={soundBar ? 'pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center [&>*]:pointer-events-auto' : 'hidden'}>
          <SoundTimelapse twin={twin} active={active} />
        </div>
      )}
    </div>
  );
}
