'use client';

import { cubicBezier } from '@/lib/geometry/bezier';
import { HousePlan } from '@/components/house/HousePlan';
import { furnitureOnScreen, previewLayout } from '@/lib/layouts';
import type { InventoryItem, PropertyTwin } from '@ithaca/schema';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { HouseLoader } from '@/components/house/HouseLoader';
import { SoundTimelapse } from '@/components/sound/SoundTimelapse';
import { Badge, Notice, ViewState } from '@/components/ui';
import { api } from '@/lib/api';
import { config } from '@/lib/config';
import { useHouseFurniture, useInventory, useLayouts } from '@/lib/data/hooks';
import { onHandNav } from '@/lib/hands/engine';
import { sweepRooms } from '@/lib/rooms';
import { defaultStartSweep, rotationToward, sweepForRoom, viewForRoom } from '@/lib/showcase/rooms';
import type { MpSdk } from '@/lib/showcase/sdk-types';
import { mountShowcase, type ShowcaseHandle } from '@/lib/showcase/viewer';
import { onSessionEvent, useSession } from '@/lib/store/session';
import { useTour, type TourPlace } from '@/lib/store/tour';

interface SdkSweep {
  id: string;
  position: { x: number; y: number; z: number };
}

/** I-G9 check (C6): do the twin's sweeps sit where the Showcase SDK says they are? */
interface FrameCheck {
  matched: number;
  total: number;
  maxError: number;
}

declare global {
  interface Window {
    /** Test hooks. E2E reads these; nothing in the app does. */
    __ITHACA__?: Record<string, unknown>;
  }
}

/** How far a hand gesture turns and zooms the tour. A drag across the whole view is `*PerView` degrees. */
const HAND_TOUR = { yawDegPerView: 160, pitchDegPerView: 90, speedDegPerS: 900, zoomGain: 2 } as const;

// Phone look and zoom are eased: quick off the mark, soft landing (an ease-out bezier).
const LOOK_EASE = cubicBezier(0.22, 0.8, 0.3, 1);
const LOOK_EASE_MS = 170;
const ZOOM_EASE_MS = 280;

const exposeForTests =(key: string, value: unknown) => {
  window.__ITHACA__ = { ...window.__ITHACA__, [key]: value };
};

/**
 * Showcase view modes, as this app names them. In the current viewer the "Outside" view is
 * the dollhouse mode, drawn with the exterior capture when the model has one.
 */
function placeOf(mode: string | null | undefined): TourPlace {
  switch (mode) {
    case 'mode.inside':
      return 'inside';
    case 'mode.dollhouse':
    case 'mode.outside':
      return 'outside';
    case 'mode.floorplan':
      return 'floorplan';
    case 'mode.transitioning':
      return 'moving';
    default:
      return null;
  }
}

/**
 * Photoreal tour: the Matterport Showcase. `viewer.goto` moves the camera indoors; the room
 * list can also ask for the outside view. With the bundle viewer, the chosen layout's furniture
 * is placed with `Scene.createObjects` + `mp.gltfLoader`, using the stored poses as they are (A5).
 */
export function TourTab({ twin, active }: { twin: PropertyTwin; active: boolean }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const handleRef = useRef<{ handle: ShowcaseHandle; modelId: string } | null>(null);
  const disposeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** The SDK as soon as it is connected: model data is readable before the tour can move. */
  const [connected, setConnected] = useState<MpSdk | null>(null);
  const [sdk, setSdk] = useState<MpSdk | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** The first view is in place: the loading screen fades out, then goes. */
  const [revealed, setRevealed] = useState(false);
  const [loaderGone, setLoaderGone] = useState(false);
  const [frame, setFrame] = useState<FrameCheck | null>(null);
  const [placed, setPlaced] = useState<{ count: number; error: string | null }>({ count: 0, error: null });
  const sdkSweeps = useRef<Map<string, SdkSweep>>(new Map());
  const programmatic = useRef<string | null>(null);
  /** The sweep the camera stands on now, as the viewer last reported it. */
  const here = useRef<string | null>(null);

  const roomId = useSession((s) => s.viewer.room_id);
  const sweepId = useSession((s) => s.viewer.sweep_id);
  const gotoSeq = useSession((s) => s.viewer.goto_seq);
  const selectedLayoutId = useSession((s) => s.viewer.selected_layout_id);
  const layouts = useLayouts(twin.property_id);
  const house = useHouseFurniture(twin.property_id);
  const inventory = useInventory();
  const outsideSeq = useTour((s) => s.outsideSeq);
  const canPlaceObjects = config.matterportViewer === 'bundle';

  const modelId = twin.sources.matterport_model_id; // I-G16: never assume it equals property_id

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !config.matterportSdkKey) return;
    // One viewer per model, kept across effect re-runs: React StrictMode mounts effects twice
    // in development, and a viewer that is torn down and rebuilt at once does not start.
    if (disposeTimer.current) clearTimeout(disposeTimer.current);
    disposeTimer.current = null;
    if (!handleRef.current || handleRef.current.modelId !== modelId) {
      handleRef.current?.handle.dispose();
      handleRef.current = { handle: mountShowcase(host, modelId), modelId };
    }
    const { handle } = handleRef.current;
    let live = true;
    const fail = (err: unknown) => live && setError(err instanceof Error ? err.message : typeof err === 'string' ? err : 'The Matterport viewer could not start.');
    handle.connected.then((s) => live && setConnected(s), fail);
    handle.playing.then((s) => live && setSdk(s), fail);
    return () => {
      live = false;
      // Dispose on a real unmount only: a StrictMode re-run cancels this timer first.
      disposeTimer.current = setTimeout(() => {
        handle.dispose();
        if (handleRef.current?.handle === handle) handleRef.current = null;
      }, 0);
    };
  }, [modelId]);

  // Where the camera is: inside, outside, dollhouse. The room list shows it.
  useEffect(() => {
    if (!sdk) return;
    exposeForTests('tourSdk', sdk);
    const sub = sdk.Mode.current.subscribe((mode) => useTour.getState().setPlace(placeOf(mode)));
    return () => {
      sub.cancel();
      useTour.getState().setPlace(null);
    };
  }, [sdk]);

  // "Outside" from the room list: orbit and zoom around the building.
  useEffect(() => {
    if (!sdk || outsideSeq === 0) return;
    queued.current = null; // a room move that was waiting is superseded
    sdk.Mode.moveTo(sdk.Mode.Mode.DOLLHOUSE).catch((err: unknown) => console.warn('[tour] could not go outside', err));
  }, [sdk, outsideSeq]); // eslint-disable-line react-hooks/exhaustive-deps

  // The SDK's own sweep list and the frame check. Available as soon as the SDK connects.
  useEffect(() => {
    if (!connected) return;
    const twinSweeps = new Map(twin.sweeps.map((s) => [s.sweep_id, s]));
    const dataSub = connected.Sweep.data.subscribe({
      onCollectionUpdated(collection) {
        const map = new Map<string, SdkSweep>();
        let matched = 0;
        let maxError = 0;
        for (const [, s] of collection as unknown as Iterable<[string, SdkSweep]>) {
          map.set(s.id, s);
          const t = twinSweeps.get(s.id);
          if (t) {
            matched++;
            maxError = Math.max(maxError, Math.hypot(t.position.x - s.position.x, t.position.y - s.position.y, t.position.z - s.position.z));
          }
        }
        sdkSweeps.current = map;
        const check = { matched, total: twin.sweeps.length, maxError };
        setFrame(check);
        exposeForTests('frameCheck', check);
      },
    });
    return () => dataSub.cancel();
  }, [connected, twin]);

  // When the user walks in the Showcase, the session follows: same event as a click on the room list (A1).
  useEffect(() => {
    if (!sdk) return;
    const rooms = sweepRooms(twin);
    const currentSub = sdk.Sweep.current.subscribe((current) => {
      const id = current.id;
      if (id) here.current = id;
      if (!id || !(id in rooms)) return;
      if (programmatic.current) {
        if (programmatic.current === id) programmatic.current = null;
        return;
      }
      const s = useSession.getState();
      // Into no room too (stairs, patio): the label must not keep the last room.
      if (rooms[id] !== s.viewer.room_id) s.dispatch('viewer.goto', { sweep_id: id });
    });
    return () => currentSub.cancel();
  }, [sdk, twin]);

  // One camera move at a time. The viewer rejects a move that starts while another move or a
  // mode change (inside <-> outside) is in flight, so a request waits and is tried again; a
  // newer request replaces a waiting one, so the last request wins.
  type Facing = { x: number; y: number };
  const moving = useRef(false);
  const queued = useRef<{ target: string; instant: boolean; attempt: number; facing?: Facing } | null>(null);
  /** Bumped by every new walk and every goto: a walk in progress stops at its next step. */
  const walkToken = useRef(0);
  const moveTo = useCallback(
    (target: string, instant: boolean, attempt = 0, facing?: Facing) => {
      if (!sdk) return;
      if (moving.current) {
        queued.current = { target, instant, attempt: 0, facing };
        return;
      }
      moving.current = true;
      programmatic.current = target;
      const options = {
        ...(instant ? { transition: sdk.Sweep.Transition.INSTANT } : { transition: sdk.Sweep.Transition.FLY, transitionTime: 1400 }),
        ...(facing ? { rotation: facing } : {}), // arrive facing the room, not whatever was ahead
      };
      sdk.Sweep.moveTo(target, options)
        .catch((err: unknown) => {
          programmatic.current = null;
          if (attempt < 4 && !queued.current) queued.current = { target, instant, attempt: attempt + 1, facing };
          else if (!queued.current) console.warn('[tour] moveTo failed', err);
          return new Promise((resolve) => setTimeout(resolve, 700));
        })
        .finally(() => {
          moving.current = false;
          // The viewer does not always report arriving (a move to where it already is); a guard
          // left standing would ignore the user's next walk into another room.
          if (programmatic.current === target) programmatic.current = null;
          const next = queued.current;
          queued.current = null;
          if (next) moveTo(next.target, next.instant, next.attempt, next.facing);
        });
    },
    [sdk],
  );

  /** Start the move that was waiting, if any. */
  const runQueued = useCallback(() => {
    const next = queued.current;
    queued.current = null;
    if (next) moveTo(next.target, next.instant, next.attempt, next.facing);
  }, [moveTo]);

  /** A sweep's position: the viewer's own if it has the sweep, else the twin's. */
  const sweepPosition = useCallback(
    (id: string) => sdkSweeps.current.get(id)?.position ?? twin.sweeps.find((s) => s.sweep_id === id)?.position,
    [twin],
  );

  // viewer.goto -> Sweep.moveTo. Also runs once when the tour becomes interactive: it goes to
  // the room the session already names. With none, the hosted viewer opens outside; the bundle
  // viewer starts indoors (its start view is the missing outside one).
  useEffect(() => {
    if (!sdk) return;
    const first = gotoSeq === 0;
    walkToken.current++; // a goto replaces a walk in progress
    // Going to a room (not to one sweep): stand where the room shows best and face into it. The
    // agent sends that view with the goto; for a click on the room list it is worked out here.
    const asked = useSession.getState().viewer.view;
    const best = !sweepId && roomId ? (asked ? { sweep_id: asked.at_sweep_id, lookAt: asked.look_at } : (() => {
      const v = viewForRoom(twin, roomId);
      return v ? { sweep_id: v.sweep.sweep_id, lookAt: v.lookAt } : undefined;
    })()) : undefined;
    const wanted = sweepId ?? best?.sweep_id ?? (roomId ? sweepForRoom(twin, roomId)?.sweep_id : undefined) ?? (first && canPlaceObjects ? defaultStartSweep(twin)?.sweep_id : undefined);
    if (!wanted) {
      // Nothing asked for yet: the hosted viewer opens on the outside view, where the house can
      // be dragged round and zoomed (the bundle viewer has none, and started indoors above).
      if (first && !canPlaceObjects) {
        sdk.Mode.moveTo(sdk.Mode.Mode.DOLLHOUSE, { transition: sdk.Mode.TransitionType.INSTANT })
          .catch((err: unknown) => console.warn('[tour] could not open outside', err))
          // A moment for the viewer to draw the outside before the loading screen lifts.
          .finally(() => setTimeout(() => setRevealed(true), 250));
      } else {
        setRevealed(true);
      }
      return;
    }
    let target = wanted;
    if (sdkSweeps.current.size && !sdkSweeps.current.has(wanted)) {
      // The twin and the SDK disagree on sweep ids: fall back to the nearest SDK sweep.
      const pos = twin.sweeps.find((s) => s.sweep_id === wanted)?.position;
      if (!pos) return;
      let best = Infinity;
      for (const s of sdkSweeps.current.values()) {
        const d = Math.hypot(s.position.x - pos.x, s.position.y - pos.y, s.position.z - pos.z);
        if (d < best) {
          best = d;
          target = s.id;
        }
      }
      console.warn(`[tour] sweep ${wanted} is not in the Showcase model; using the nearest sweep ${target} (${best.toFixed(2)} m away)`);
    }
    // The user walked here themselves: the session is following the camera, not moving it.
    if (!first && sweepId && target === here.current) return;
    const from = sweepPosition(target);
    moveTo(target, first, 0, best && from ? rotationToward(from, best.lookAt) : undefined);
    if (first) setTimeout(() => setRevealed(true), 400); // an instant move
    // The sweep list is read at the moment of the move; it is not a trigger.
  }, [sdk, gotoSeq]); // eslint-disable-line react-hooks/exhaustive-deps

  // viewer.walk: step the camera from sweep to sweep along the route, facing the way it is going,
  // and face the room on arrival. A new walk, a goto or a click on the room list stops it.
  useEffect(() => {
    if (!sdk) return;
    const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
    return onSessionEvent((event) => {
      if (event.type !== 'viewer.walk') return;
      const token = ++walkToken.current;
      const { sweep_ids, look_at, duration_s } = event.payload;
      // Only sweeps the viewer has: a few of the twin's are not in the Showcase model.
      const steps = sweep_ids.filter((id) => !sdkSweeps.current.size || sdkSweeps.current.has(id));
      if (!steps.length) return;
      const stepMs = Math.max(700, Math.min(1600, (duration_s * 1000) / steps.length));
      void (async () => {
        queued.current = null;
        while (moving.current) await sleep(100); // let a move in flight finish
        if (walkToken.current !== token) return;
        moving.current = true;
        try {
          for (let i = 0; i < steps.length; i++) {
            if (walkToken.current !== token) return;
            const id = steps[i]!;
            const here = sweepPosition(id);
            const nextId = steps[i + 1];
            const ahead = nextId ? sweepPosition(nextId) : look_at;
            programmatic.current = id;
            try {
              await sdk.Sweep.moveTo(id, {
                transition: sdk.Sweep.Transition.FLY,
                transitionTime: i === 0 ? 1200 : stepMs,
                ...(here && ahead ? { rotation: rotationToward(here, ahead) } : {}),
              });
            } catch (err) {
              console.warn('[tour] a step of the walk failed', err);
              await sleep(300);
            }
          }
        } finally {
          programmatic.current = null;
          moving.current = false;
          runQueued(); // a goto that arrived during the walk
        }
      })();
    });
  }, [sdk, runQueued, sweepPosition]);

  // viewer.look and viewer.zoom (the phone's drag and pinch), smoothed. Each message is not applied
  // at once: it is played out over a short time along a bezier ease-out curve, so the uneven
  // arrival of messages over the network becomes one even motion. Every frame, what the running
  // eases have released is gathered; the viewer takes one camera call at a time, so it goes out
  // when the last call is done.
  useEffect(() => {
    if (!sdk) return;
    type Ease = { start: number; ms: number; yaw: number; pitch: number; zoom: number; done: number };
    let eases: Ease[] = [];
    let yaw = 0;
    let pitch = 0;
    let zoom = 0;
    let busy = false;
    let live = true;
    let frame: number | null = null;
    const flush = () => {
      if (busy || !live) return;
      const size = Math.hypot(yaw, pitch);
      let call: Promise<unknown> | null = null;
      if (size >= 0.02) {
        // The ease gives the motion its shape; the call itself should take about a frame.
        call = sdk.Camera.rotate(yaw, pitch, { speed: Math.max(240, size / 0.016) });
        yaw = pitch = 0;
      } else if (Math.abs(zoom) >= 0.004) {
        // Inside only: the viewer has no zoom in the outside (dollhouse) view.
        call = sdk.Camera.zoomBy(zoom);
        zoom = 0;
      }
      if (!call) return;
      busy = true;
      call.catch(() => {}).finally(() => {
        busy = false;
        flush();
      });
    };
    const tick = (now: number) => {
      frame = null;
      for (const e of eases) {
        const at = LOOK_EASE((now - e.start) / e.ms);
        const step = at - e.done;
        e.done = at;
        yaw += e.yaw * step;
        pitch += e.pitch * step;
        zoom += e.zoom * step;
      }
      eases = eases.filter((e) => e.done < 1);
      flush();
      if (eases.length && live) frame = requestAnimationFrame(tick);
    };
    const off = onSessionEvent((event) => {
      if (useSession.getState().viewer.mode !== 'tour') return;
      const start = performance.now();
      if (event.type === 'viewer.look') eases.push({ start, ms: LOOK_EASE_MS, yaw: event.payload.yaw_delta_deg, pitch: event.payload.pitch_delta_deg, zoom: 0, done: 0 });
      else if (event.type === 'viewer.zoom') eases.push({ start, ms: ZOOM_EASE_MS, yaw: 0, pitch: 0, zoom: event.payload.delta, done: 0 });
      else return;
      frame ??= requestAnimationFrame(tick);
    });
    return () => {
      live = false;
      if (frame) cancelAnimationFrame(frame);
      off();
    };
  }, [sdk]);

  // viewer.step (the phone's joystick): hop to the neighbouring scan position that lies most nearly
  // ahead of (or behind) the way the camera faces, keeping the camera's direction.
  useEffect(() => {
    if (!sdk) return;
    let rotation = { x: 0, y: 0 };
    let here: string | null = null;
    const poseSub = sdk.Camera.pose.subscribe((pose) => {
      rotation = { x: pose.rotation.x, y: pose.rotation.y };
    });
    const sweepSub = sdk.Sweep.current.subscribe((current) => {
      if (current.id) here = current.id;
    });
    /** One hop the given way (clockwise from straight ahead); then the rest, if more were asked for. */
    const hop = (angleDeg: number, left: number) => {
      if (!here || moving.current) return;
      const from = sweepPosition(here);
      const neighbors = twin.sweeps.find((s) => s.sweep_id === here)?.neighbors ?? [];
      if (!from || !neighbors.length) return;
      // Showcase yaw: 0 looks along -Z, positive turns left.
      const yaw = (rotation.y * Math.PI) / 180;
      const angle = (angleDeg * Math.PI) / 180;
      const fx = -Math.sin(yaw) * Math.cos(angle) + Math.cos(yaw) * Math.sin(angle);
      const fz = -Math.cos(yaw) * Math.cos(angle) - Math.sin(yaw) * Math.sin(angle);
      let best: string | null = null;
      let bestScore = 0.35; // within about 70 degrees of that way
      for (const id of neighbors) {
        const to = sweepPosition(id);
        if (!to || (sdkSweeps.current.size && !sdkSweeps.current.has(id))) continue;
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
      ++walkToken.current;
      moving.current = true;
      void sdk.Sweep.moveTo(target, { transition: sdk.Sweep.Transition.FLY, transitionTime: 650, rotation: { x: rotation.x, y: rotation.y } })
        .catch((err: unknown) => console.warn('[tour] step failed', err))
        .finally(() => {
          moving.current = false;
          here = target;
          if (left > 1) hop(angleDeg, left - 1);
          else runQueued();
        });
    };
    const off = onSessionEvent((event) => {
      if (event.type !== 'viewer.step' || useSession.getState().viewer.mode !== 'tour') return;
      hop(event.payload.angle_deg ?? (event.payload.direction === 'back' ? 180 : 0), event.payload.steps ?? 1);
    });
    return () => {
      poseSub.cancel();
      sweepSub.cancel();
      off();
    };
  }, [sdk, twin, runQueued, sweepPosition]);

  // Hand control: a pinch-drag turns the view, both hands pinching zoom. The viewer takes one
  // camera call at a time, so movement gathers while a call is out and goes in the next one.
  useEffect(() => {
    if (!sdk || !active) return;
    let yaw = 0;
    let pitch = 0;
    let zoom = 0;
    let busy = false;
    let live = true;
    const flush = () => {
      if (busy || !live) return;
      let call: Promise<unknown> | null = null;
      if (Math.abs(yaw) >= 0.2 || Math.abs(pitch) >= 0.2) {
        call = sdk.Camera.rotate(yaw, pitch, { speed: HAND_TOUR.speedDegPerS });
        yaw = pitch = 0;
      } else if (Math.abs(zoom) >= 0.01) {
        // Inside only: the viewer has no zoom in the outside (dollhouse) view.
        call = sdk.Camera.zoomBy(zoom);
        zoom = 0;
      }
      if (!call) return;
      busy = true;
      call.catch(() => {}).finally(() => {
        busy = false;
        flush();
      });
    };
    const off = onHandNav((nav) => {
      if (nav.type === 'drag') {
        // The scene follows the hand: moving it right turns the view left.
        yaw -= nav.du * HAND_TOUR.yawDegPerView;
        pitch += nav.dv * HAND_TOUR.pitchDegPerView;
      } else {
        zoom += (nav.factor - 1) * HAND_TOUR.zoomGain;
      }
      flush();
    });
    return () => {
      live = false;
      off();
    };
  }, [sdk, active]);

  // The house's furniture (or the layout being tried in its room), in the Showcase scene.
  const placements = useMemo(() => {
    const items = new Map<string, InventoryItem>((inventory.data ?? []).map((i) => [i.item_id, i]));
    return furnitureOnScreen(house.data, previewLayout(layouts.data, selectedLayoutId)).flatMap((p) => {
      const item = items.get(p.item_id);
      return item ? [{ ...p, url: api().assetUrl(item.glb_url) }] : [];
    });
  }, [house.data, layouts.data, inventory.data, selectedLayoutId]);

  // One scene object per piece, kept between changes: a piece that only moved or turned is
  // re-posed where it stands, a new one is loaded, one that left is taken out. Nothing else is
  // touched, so the rest of the room does not blink while one piece is edited.
  const pieces = useRef(new Map<string, { url: string; object: MpSdk.Scene.IObject; node: MpSdk.Scene.INode }>());
  const sceneLights = useRef<MpSdk.Scene.IObject | null>(null);
  const placeRun = useRef(0);
  useEffect(() => {
    if (!sdk || !canPlaceObjects) return;
    const run = ++placeRun.current;
    const have = pieces.current;
    const want = new Map(placements.map((p) => [p.item_id, p]));
    for (const [id, piece] of [...have]) {
      if (want.get(id)?.url === piece.url) continue;
      piece.object.stop();
      have.delete(id);
    }
    const pose = (node: MpSdk.Scene.INode, p: (typeof placements)[number]) => {
      // A5: the stored pose, as it is. Interior frame == Showcase frame (I-G9); yaw is about +Y (I-G12).
      node.position.set(p.position.x, p.position.y, p.position.z);
      const half = (p.yaw_deg * Math.PI) / 360;
      node.quaternion.set(0, Math.sin(half), 0, Math.cos(half));
    };
    void (async () => {
      try {
        if (placements.length && !sceneLights.current) {
          const [object] = await sdk.Scene.createObjects(1);
          if (!object) throw new Error('Scene.createObjects returned nothing');
          if (sceneLights.current) object.stop();
          else {
            const lights = object.addNode();
            lights.addComponent('mp.ambientLight', { intensity: 1.1 });
            lights.addComponent('mp.directionalLight', { intensity: 1.4, position: { x: 2, y: 6, z: 3 }, target: { x: 0, y: 0, z: 0 } });
            object.start();
            sceneLights.current = object;
          }
        }
        for (const p of placements) {
          if (placeRun.current !== run) return; // a newer change takes over from here
          const piece = have.get(p.item_id);
          if (piece) {
            pose(piece.node, p);
            continue;
          }
          const [object] = await sdk.Scene.createObjects(1);
          if (!object) throw new Error('Scene.createObjects returned nothing');
          if (placeRun.current !== run || have.has(p.item_id)) {
            object.stop();
            if (placeRun.current !== run) return;
            continue;
          }
          const node = object.addNode();
          node.addComponent('mp.gltfLoader', { url: p.url, colliderEnabled: false });
          pose(node, p);
          object.start();
          have.set(p.item_id, { url: p.url, object, node });
        }
        setPlaced({ count: placements.length, error: null });
        exposeForTests('tourPlacedItemIds', placements.map((p) => p.item_id));
      } catch (err) {
        if (placeRun.current === run) setPlaced({ count: have.size, error: err instanceof Error ? err.message : String(err) });
      }
    })();
  }, [sdk, placements]);
  // furniture.select: the piece selected on the phone's plan swells and settles, over and over,
  // so it can be found in the room. Leaving the phone's furniture screen ends it.
  useEffect(() => {
    if (!sdk) return;
    let id: string | null = null;
    const settle = () => {
      const node = id ? pieces.current.get(id)?.node : undefined;
      node?.scale.set(1, 1, 1);
    };
    const beat = setInterval(() => {
      const node = id ? pieces.current.get(id)?.node : undefined;
      if (!node) return;
      const k = 1 + 0.045 * (0.5 + 0.5 * Math.sin(performance.now() / 260));
      node.scale.set(k, k, k);
    }, 40);
    const off = onSessionEvent((event) => {
      if (event.type === 'furniture.select') {
        settle();
        id = event.payload.item_id ?? null;
      } else if (event.type === 'remote.view' && event.payload.view !== 'furniture') {
        settle();
        id = null;
      }
    });
    return () => {
      clearInterval(beat);
      settle();
      off();
    };
  }, [sdk]);

  // The viewer going away takes every piece with it.
  useEffect(() => {
    const have = pieces.current;
    return () => {
      placeRun.current += 1;
      for (const piece of have.values()) piece.object.stop();
      have.clear();
      sceneLights.current?.stop();
      sceneLights.current = null;
    };
  }, [sdk]);

  // A tour that never becomes interactive must say so, not sit there looking broken.
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (sdk) return setSlow(false);
    const t = setTimeout(() => setSlow(true), 30_000);
    return () => clearTimeout(t);
  }, [sdk]);

  if (!config.matterportSdkKey) {
    return (
      <ViewState>
        <Notice tone="warn" title="The tour is not configured">
          Set NEXT_PUBLIC_MATTERPORT_SDK_KEY to a Matterport Showcase SDK key to enable the photoreal tour. Every other tab works without it.
        </Notice>
      </ViewState>
    );
  }
  if (error) {
    return (
      <ViewState>
        <Notice tone="error" title="The tour could not start">
          {error} Check the network connection, that the SDK key allows this domain, and that the model is public.
        </Notice>
      </ViewState>
    );
  }

  // Some twin sweeps (unaligned 360 views) are not navigable in the Showcase; positions must agree for the rest.
  const frameOk = frame && frame.matched > 0 && frame.maxError < 0.05;

  return (
    <div className="relative h-full w-full bg-black" data-testid="tour" data-sdk-ready={!!sdk} data-active={active}>
      <div ref={hostRef} className="h-full w-full" data-viewer={config.matterportViewer} />
      {/* Covers the viewer's own loading screen and its first jump outside, then fades out. */}
      {!loaderGone && (
        <div className="absolute inset-0 z-20" onTransitionEnd={() => revealed && setLoaderGone(true)}>
          <HouseLoader label={!connected ? 'Starting the tour…' : !sdk ? 'Loading the house…' : 'Going outside…'} leaving={revealed} />
        </div>
      )}
      {slow && !sdk && (
        <div className="absolute top-12 left-3 z-30 w-[360px] max-w-[90%]">
          <Notice tone="warn" title="The tour is not responding">
            The Matterport viewer has been loading for over 30 seconds. Reload the page. If it keeps happening, this model may use a feature the bundled viewer cannot load; the Sun Lab still shows the scanned
            interior.
          </Notice>
        </div>
      )}
      {sdk && revealed && (
        <div className="pointer-events-none absolute top-3 left-3 z-10">
          <HousePlan twin={twin} />
        </div>
      )}
      <div className="pointer-events-none absolute top-16 right-3 flex flex-wrap justify-end gap-1.5">
        {/* The frame check runs always (see window.__ITHACA__.frameCheck); it shows only when it fails. */}
        {frame && !frameOk && (
          <Badge
            tone={frameOk ? 'leaf' : 'sun'}
            title="Compares the twin's sweep positions with the positions the Showcase SDK reports. They must match for furniture to land where the layout says (I-G9)."
          >
            Frame check: {frame.matched} of {frame.total} sweeps match, max {(frame.maxError * 100).toFixed(1)} cm apart
          </Badge>
        )}
        {!canPlaceObjects && placements.length > 0 && (
          <Badge title="The hosted Matterport viewer cannot add 3D objects. Set NEXT_PUBLIC_MATTERPORT_VIEWER=bundle to place furniture in the tour, or see it in the Sun Lab.">
            Furniture is not shown in this viewer
          </Badge>
        )}
        {placed.count > 0 && (
          <Badge tone="leaf">
            {placed.count} items placed
          </Badge>
        )}
        {placed.error && (
          <Badge tone="danger" title={placed.error}>
            Furniture could not be placed
          </Badge>
        )}
      </div>
      {config.features.has('sound') && sdk && revealed && (
        <div className="pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center [&>*]:pointer-events-auto">
          <SoundTimelapse twin={twin} active={active} />
        </div>
      )}
    </div>
  );
}
