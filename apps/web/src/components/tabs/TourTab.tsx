'use client';

import { cubicBezier } from '@/lib/geometry/bezier';
import { LiveArrangeMinimap } from '@/components/house/ArrangeMinimap';
import { HousePlan } from '@/components/house/HousePlan';
import { furnitureOnScreen, previewLayout } from '@/lib/layouts';
import type { InventoryItem, PropertyTwin } from '@ithaca/schema';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { HouseLoader } from '@/components/house/HouseLoader';
import { SoundTimelapse } from '@/components/sound/SoundTimelapse';
import { GLASS, GlassBadge, Notice, ViewState, cn, glassToggle } from '@/components/ui';
import { api } from '@/lib/api';
import { config } from '@/lib/config';
import { useHouseFurniture, useInventory, useLayouts } from '@/lib/data/hooks';
import { bounds, centroid } from '@/lib/geometry/polygon';
import { onHandNav } from '@/lib/hands/engine';
import { handEngine, onHandTick, useHands } from '@/lib/hands/store';
import { sweepRooms } from '@/lib/rooms';
import { aerialView, defaultStartSweep, rotationToward, sweepForRoom, viewForRoom } from '@/lib/showcase/rooms';
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
/** In the tour, hands arrange furniture and nothing else: the phone moves the camera. */
const HANDS_MOVE_THE_VIEW = false;
/** Picking a piece by hand: how near the pinch must be to it, as a fraction of the view's height. */
const HAND_GRAB_RADIUS = 0.2;
const HAND_TOUR = { yawDegPerView: 160, pitchDegPerView: 90, speedDegPerS: 900, zoomGain: 2, sweepDegPerView: 220 } as const;

// Phone look and zoom are eased: quick off the mark, soft landing (an ease-out bezier).
const LOOK_EASE = cubicBezier(0.22, 0.8, 0.3, 1);
const LOOK_EASE_MS = 170;
/** Facing a spot on the phone's plan from the scan point the camera already stands on. */
const TURN_IN_PLACE_DEG_PER_S = 140;
const ZOOM_EASE_MS = 280;

const exposeForTests =(key: string, value: unknown) => {
  window.__ITHACA__ = { ...window.__ITHACA__, [key]: value };
};

/** A5: a scene node at a stored pose, as it is. Interior frame == Showcase frame (I-G9); yaw is about +Y (I-G12). */
function poseNode(node: MpSdk.Scene.INode, x: number, y: number, z: number, yawDeg: number) {
  node.position.set(x, y, z);
  const half = (yawDeg * Math.PI) / 360;
  node.quaternion.set(0, Math.sin(half), 0, Math.cos(half));
}

/** The selected piece: each material `mix` of the way to this blue, plus `glow` of it lit from within so dark wood shows it too. */
const SELECTED_TINT = { r: 0.23, g: 0.51, b: 0.96, mix: 0.45, glow: 0.35 };

type Rgb = { r: number; g: number; b: number; setRGB(r: number, g: number, b: number): unknown };
type TintMaterial = { color?: Rgb; emissive?: Rgb };
type TintRoot = { traverse(fn: (o: { material?: TintMaterial | TintMaterial[] }) => void): void };
const untinted = new WeakMap<TintMaterial, { color?: [number, number, number]; emissive?: [number, number, number] }>();

/** Tint a loaded glTF blue, or put its own colours back. */
function tintPiece(root: TintRoot, on: boolean) {
  const { r, g, b, mix: k, glow } = SELECTED_TINT;
  root.traverse((o) => {
    for (const m of ([] as TintMaterial[]).concat(o.material ?? [])) {
      let own = untinted.get(m);
      if (!own) {
        own = { color: m.color && [m.color.r, m.color.g, m.color.b], emissive: m.emissive && [m.emissive.r, m.emissive.g, m.emissive.b] };
        untinted.set(m, own);
      }
      if (m.color && own.color) {
        const [cr, cg, cb] = own.color;
        if (on) m.color.setRGB(cr + (r - cr) * k, cg + (g - cg) * k, cb + (b - cb) * k);
        else m.color.setRGB(cr, cg, cb);
      }
      if (m.emissive && own.emissive) {
        const [er, eg, eb] = own.emissive;
        if (on) m.emissive.setRGB(er + r * glow, eg + g * glow, eb + b * glow);
        else m.emissive.setRGB(er, eg, eb);
      }
    }
  });
}

/** After a drag ends, the saved pose arrives with the furniture reload; a refused move must still go back. */
const DRAG_SETTLE_MS = 2500;

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
  const firstMove = useRef<MpSdk | null>(null);
  /** One hop the given way, for the hand gestures; set while the tour can walk. */
  const handStep = useRef<((angleDeg: number, steps: number) => void) | null>(null);
  const [loaderGone, setLoaderGone] = useState(false);
  const [frame, setFrame] = useState<FrameCheck | null>(null);
  const [placed, setPlaced] = useState<{ count: number; error: string | null }>({ count: 0, error: null });
  const sdkSweeps = useRef<Map<string, SdkSweep>>(new Map());
  const programmatic = useRef<string | null>(null);
  /** The sweep the camera stands on now, as the viewer last reported it. */
  const here = useRef<string | null>(null);
  /** The view from above while arranging furniture: what it shows (see aerialKey), or null. */
  const aerial = useRef<string | null>(null);
  /** The viewer's mode as it last reported it (inside, dollhouse, …). */
  const viewMode = useRef<string | null>(null);
  /** A view from above that a goto, walk, step or "Outside" took over from: not shown again. */
  const aerialDismissed = useRef<string | null>(null);

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
    // The view from above while arranging is the dollhouse too, but it is still the room.
    const sub = sdk.Mode.current.subscribe((mode) => {
      viewMode.current = mode;
      useTour.getState().setPlace(mode === sdk.Mode.Mode.DOLLHOUSE && aerial.current ? 'inside' : placeOf(mode));
    });
    return () => {
      sub.cancel();
      useTour.getState().setPlace(null);
    };
  }, [sdk]);

  // No furniture by default: the model's defurnished view, the seller's furniture painted out of
  // the scan photos. The camera stays where it is. Models without one stay as they are.
  const defurnished = useTour((s) => s.defurnished);
  const [canDefurnish, setCanDefurnish] = useState(false);
  useEffect(() => {
    if (!sdk?.View?.views) return;
    let live = true;
    const sub = sdk.View.views.subscribe({
      onCollectionUpdated(collection) {
        const c = collection as unknown as Iterable<[string, MpSdk.View.View]> | Record<string, MpSdk.View.View>;
        const views = Symbol.iterator in c ? [...(c as Iterable<[string, MpSdk.View.View]>)].map(([, v]) => v) : Object.values(c);
        const painted = views.find((v) => v.type === sdk.View.ViewType.DEFURNISH);
        const base = views.find((v) => v.type === sdk.View.ViewType.BASE || v.type === sdk.View.ViewType.LAYERED_BASE);
        if (live) setCanDefurnish(!!painted);
        const want = defurnished ? painted : base;
        if (want && !want.active) want.setActive(false).catch((err: unknown) => console.warn('[tour] could not switch the furniture view', err));
      },
    });
    return () => {
      live = false;
      sub.cancel();
    };
  }, [sdk, defurnished]);

  // "Outside" from the room list: orbit and zoom around the building.
  useEffect(() => {
    if (!sdk || outsideSeq === 0) return;
    queued.current = null; // a room move that was waiting is superseded
    aerialDismissed.current = aerial.current;
    aerial.current = null;
    sdk.Mode.moveTo(sdk.Mode.Mode.DOLLHOUSE)
      .then(() => sdk.Floor.showAll()) // the view from above may have left one floor showing
      .catch((err: unknown) => console.warn('[tour] could not go outside', err));
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
      // Already standing there: a move would not turn the camera, so just turn it.
      if (facing && target === here.current && viewMode.current === sdk.Mode.Mode.INSIDE) {
        moving.current = true;
        sdk.Camera.setRotation(facing, { speed: TURN_IN_PLACE_DEG_PER_S })
          .catch((err: unknown) => console.warn('[tour] could not turn', err))
          .finally(() => {
            moving.current = false;
            const next = queued.current;
            queued.current = null;
            if (next) moveTo(next.target, next.instant, next.attempt, next.facing);
          });
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

  // Whatever happens in the first move, the loading screen never stays for good.
  useEffect(() => {
    if (!sdk || revealed) return;
    const t = setTimeout(() => setRevealed(true), 6000);
    return () => clearTimeout(t);
  }, [sdk, revealed]);

  // viewer.goto -> Sweep.moveTo. Also runs once when the tour becomes interactive: it goes to
  // the room the session already names. With none, the hosted viewer opens outside; the bundle
  // viewer starts indoors (its start view is the missing outside one).
  useEffect(() => {
    if (!sdk) return;
    // The first run for this viewer, whatever the session's goto count is: after a page reload
    // the session comes back with its room and a count above zero, and the loading screen must
    // still lift (it used to wait for a "first" goto that never came).
    const first = firstMove.current !== sdk;
    firstMove.current = sdk;
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
      if (!pos) {
        if (first) setRevealed(true);
        return;
      }
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
    type Ease = { start: number; ms: number; yaw: number; pitch: number; zoom: number; done: number; slow?: boolean };
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
        if (now < e.start) continue; // a slow turn that waits for the walk to the room
        const at = e.slow ? Math.min(1, (now - e.start) / e.ms) : LOOK_EASE((now - e.start) / e.ms);
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
      if (event.type === 'viewer.look') {
        const { ms, delay_ms } = event.payload;
        // The guide's slow turn around a room: even speed, and a hand on the view takes over.
        if (ms) eases = eases.filter((e) => !e.slow);
        else if (event.source !== 'agent') eases = eases.filter((e) => !e.slow);
        eases.push({ start: start + (delay_ms ?? 0), ms: ms ?? LOOK_EASE_MS, yaw: event.payload.yaw_delta_deg, pitch: event.payload.pitch_delta_deg, zoom: 0, done: 0, slow: !!ms });
      }
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
    handStep.current = hop;
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
    if (!sdk || !active || !HANDS_MOVE_THE_VIEW) return;
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
      } else if (nav.type === 'turn') {
        // An open-hand sweep turns you the way the hand goes, on the spot.
        yaw += nav.du * HAND_TOUR.sweepDegPerView;
      } else if (nav.type === 'step') {
        handStep.current?.(nav.forward ? 0 : 180, 1); // the same hop as the phone's stick
        return;
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
  const itemsById = useMemo(() => new Map<string, InventoryItem>((inventory.data ?? []).map((i) => [i.item_id, i])), [inventory.data]);
  const placements = useMemo(
    () =>
      furnitureOnScreen(house.data, previewLayout(layouts.data, selectedLayoutId)).flatMap((p) => {
        const item = itemsById.get(p.item_id);
        return item ? [{ ...p, url: api().assetUrl(item.glb_url) }] : [];
      }),
    [house.data, layouts.data, itemsById, selectedLayoutId],
  );
  const roomPieces = useMemo(() => placements.filter((p) => p.room_id === roomId), [placements, roomId]);
  const arrangingFromPhone = useSession((s) => s.peers.phone && s.remoteView === 'furniture');

  // One scene object per piece, kept between changes: a piece that only moved or turned is
  // re-posed where it stands, a new one is loaded, one that left is taken out. Nothing else is
  // touched, so the rest of the room does not blink while one piece is edited.
  const pieces = useRef(new Map<string, { url: string; object: MpSdk.Scene.IObject; node: MpSdk.Scene.INode; loader: MpSdk.Scene.IComponent }>());
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
    const pose = (node: MpSdk.Scene.INode, p: (typeof placements)[number]) => poseNode(node, p.position.x, p.position.y, p.position.z, p.yaw_deg);
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
          const loader = node.addComponent('mp.gltfLoader', { url: p.url, colliderEnabled: false });
          pose(node, p);
          object.start();
          have.set(p.item_id, { url: p.url, object, node, loader });
        }
        setPlaced({ count: placements.length, error: null });
        exposeForTests('tourPlacedItemIds', placements.map((p) => p.item_id));
      } catch (err) {
        if (placeRun.current === run) setPlaced({ count: have.size, error: err instanceof Error ? err.message : String(err) });
      }
    })();
  }, [sdk, placements]);
  // furniture.select: the piece selected on a plan is tinted blue, so it can be found in the room.
  // Leaving the phone's furniture screen ends it. A piece still loading is tinted once it is in.
  useEffect(() => {
    if (!sdk) return;
    let id: string | null = null;
    let wait: ReturnType<typeof setInterval> | null = null;
    const root = (key: string | null) => (key ? (pieces.current.get(key)?.loader.outputs.objectRoot as TintRoot | null | undefined) : null);
    const select = (next: string | null) => {
      if (wait) clearInterval(wait);
      wait = null;
      const was = root(id);
      if (was) tintPiece(was, false);
      id = next;
      if (!id) return;
      let tries = 0;
      const apply = () => {
        const now = root(id);
        if (now) tintPiece(now, true);
        return !!now || ++tries > 40;
      };
      if (!apply()) wait = setInterval(() => apply() && wait && clearInterval(wait), 250);
    };
    const off = onSessionEvent((event) => {
      if (event.type === 'furniture.select') select(event.payload.item_id ?? null);
      else if (event.type === 'remote.view' && event.payload.view !== 'furniture') select(null);
    });
    return () => {
      select(null);
      off();
    };
  }, [sdk]);

  // furniture.drag: the piece follows the finger on the phone's plan before the move is saved.
  const placementsRef = useRef(placements);
  placementsRef.current = placements;
  useEffect(() => {
    if (!sdk) return;
    let dragged: string | null = null;
    let settleTimer: ReturnType<typeof setTimeout> | null = null;
    const restore = (id: string) => {
      const p = placementsRef.current.find((x) => x.item_id === id);
      const node = pieces.current.get(id)?.node;
      if (p && node) poseNode(node, p.position.x, p.position.y, p.position.z, p.yaw_deg);
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
      const node = pieces.current.get(item_id)?.node;
      if (node) poseNode(node, x, node.position.y, z, yaw_deg);
    });
    return () => {
      if (settleTimer) clearTimeout(settleTimer);
      off();
    };
  }, [sdk]);

  // Arranging from the phone: while the phone asks for the room from above, or a layout is being
  // tried in a room, the big screen looks down on it, turned like the phone's plan, and follows the
  // selected piece each time it is moved or turned. Turning it off goes back inside, to where the
  // camera stood. A goto, walk or step takes the camera from it at once. Inside, the phone moves
  // the camera itself (a viewer.goto to the scan point nearest what was tapped).
  const [arranging, setArranging] = useState<string | null>(null);
  const [overview, setOverview] = useState<string | null>(null);
  useEffect(
    () =>
      onSessionEvent((event) => {
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
  // Hands arrange furniture. While hand control is on, the big screen looks down on the room the
  // camera is in. The piece nearest a hand is pointed out; a pinch picks it up, the hand slides
  // it over the floor, a second pinching hand turns it (as the line between the hands turns),
  // and letting go saves it where it stands.
  const handsOn = useHands((s) => s.status === 'on');
  useEffect(() => {
    if (!handsOn || !active || !roomId) return;
    setOverview(roomId);
    return () => setOverview(null);
  }, [handsOn, active, roomId]);
  useEffect(() => {
    if (!sdk || !handsOn || !active || !canPlaceObjects) return;
    let pose: MpSdk.Camera.Pose | null = null;
    const poseSub = sdk.Camera.pose.subscribe((p) => {
      pose = p;
    });
    const size = () => ({ w: hostRef.current?.clientWidth || 1, h: hostRef.current?.clientHeight || 1 });
    const onScreen = (x: number, y: number, z: number): [number, number] => {
      const at = sdk.Conversion.worldToScreen({ x, y, z }, pose!, size());
      return [at.x, at.y];
    };
    let held: { id: string; hand: number; x: number; y: number; z: number; yaw: number; px: number; py: number; twist: number | null } | null = null;
    let pointed: string | null = null;
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
      api()
        .placePiece(twin.property_id, h.id, { x: h.x, z: h.z, yaw_deg: yaw, floor_id: floor, room_id: null, snap: false })
        .then(() => useSession.getState().dispatch('layout.changed', { layout_id: null }))
        .catch((err: unknown) => {
          // Outside every room, or the server is away: it goes back to where it stood.
          console.warn('[tour] the piece could not be put there', err);
          const node = pieces.current.get(h.id)?.node;
          if (before && node) poseNode(node, before.position.x, before.position.y, before.position.z, before.yaw_deg);
        });
    };
    const off = onHandTick(() => {
      if (!pose) return;
      const { w, h } = size();
      const hands = handEngine.hands.filter((x) => !x.stale);
      if (held) {
        const grip = held;
        const hand = hands.find((x) => x.id === grip.hand);
        const node = pieces.current.get(grip.id)?.node;
        if (!hand || !hand.pinching || !node) return drop();
        const px = hand.u * w;
        const py = hand.v * h;
        const dx = px - grip.px;
        const dy = py - grip.py;
        grip.px = px;
        grip.py = py;
        if (dx || dy) {
          // How the piece's place on screen moves per metre along x and along z, here and now;
          // from that, the floor move that keeps it under the hand.
          const o = onScreen(grip.x, grip.y, grip.z);
          const a = onScreen(grip.x + 0.5, grip.y, grip.z);
          const b = onScreen(grip.x, grip.y, grip.z + 0.5);
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
            grip.x += mx * k;
            grip.z += mz * k;
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
        poseNode(node, grip.x, grip.y, grip.z, grip.yaw);
        return;
      }
      const hand = hands.find((x) => x.pinching) ?? hands[0];
      if (!hand) return;
      const px = hand.u * w;
      const py = hand.v * h;
      let best: (typeof placementsRef.current)[number] | null = null;
      let bestD = HAND_GRAB_RADIUS * h;
      for (const p of placementsRef.current) {
        if (!pieces.current.has(p.item_id)) continue;
        const [sx, sy] = onScreen(p.position.x, p.position.y, p.position.z);
        const d = Math.hypot(sx - px, sy - py);
        if (d < bestD) {
          bestD = d;
          best = p;
        }
      }
      if (best) point(best.item_id);
      if (best && hand.pinching) held = { id: best.item_id, hand: hand.id, x: best.position.x, y: best.position.y, z: best.position.z, yaw: best.yaw_deg, px, py, twist: null };
    });
    return () => {
      off();
      poseSub.cancel();
      drop();
      point(null);
    };
  }, [sdk, handsOn, active, twin]); // eslint-disable-line react-hooks/exhaustive-deps

  /** What to look down on: a key that changes when the view must, where, and in which room. 'wait': a piece just added is not loaded yet. */
  const aerialTarget = useMemo(() => {
    // By hand the whole room stays in view: the camera must not chase each piece a hand passes.
    if (overview && arranging && !handsOn) {
      const p = placements.find((x) => x.item_id === arranging);
      const room = p && twin.rooms.find((r) => r.room_id === p.room_id);
      if (!p || !room) return 'wait' as const;
      return { key: `${p.item_id}@${p.position.x.toFixed(2)},${p.position.z.toFixed(2)}`, at: p.position, room };
    }
    const tried = previewLayout(layouts.data, selectedLayoutId);
    const triedRoom = tried && twin.rooms.find((r) => r.room_id === tried.room_id);
    if (tried && triedRoom) {
      const [x, z] = centroid(triedRoom.polygon);
      return { key: `layout:${tried.layout_id}`, at: { x, y: triedRoom.floor_y, z }, room: triedRoom };
    }
    const room = overview ? twin.rooms.find((r) => r.room_id === overview) : undefined;
    if (!room) return null;
    const [x, z] = centroid(room.polygon);
    return { key: `room:${room.room_id}`, at: { x, y: room.floor_y, z }, room };
  }, [arranging, overview, placements, layouts.data, selectedLayoutId, twin.rooms, handsOn]);
  useEffect(() => {
    if (!sdk || !canPlaceObjects || aerialTarget === 'wait') return;
    if (!aerialTarget) {
      aerialDismissed.current = null;
      if (!aerial.current) return;
      aerial.current = null;
      const back = here.current ?? (roomId ? sweepForRoom(twin, roomId)?.sweep_id : undefined);
      if (back) moveTo(back, false);
      return;
    }
    const { key, at, room } = aerialTarget;
    if (key === aerial.current || key === aerialDismissed.current) return;
    aerial.current = key;
    const b = bounds(room.polygon);
    const view = aerialView(at, Math.max(b.maxX - b.minX, b.maxZ - b.minZ));
    const floor = twin.floors.find((f) => f.floor_id === room.floor_id)?.index;
    void (async () => {
      while (moving.current) await new Promise((resolve) => setTimeout(resolve, 100));
      if (aerial.current !== key) return; // a newer move of the piece, or a goto, came first
      moving.current = true;
      const above = { ...view, transition: sdk.Mode.TransitionType.FLY };
      try {
        await sdk.Mode.moveTo(sdk.Mode.Mode.DOLLHOUSE, above);
        // The floors above would hide the room. Changing floors pulls the camera far back, so the
        // view is set again after it.
        const shown = await new Promise<number | undefined>((resolve) => {
          const sub = sdk.Floor.current.subscribe((f) => {
            resolve(f.sequence);
            queueMicrotask(() => sub.cancel());
          });
        });
        if (floor !== undefined && shown !== floor) {
          await sdk.Floor.moveTo(floor);
          await sdk.Mode.moveTo(sdk.Mode.Mode.DOLLHOUSE, above);
        }
      } catch (err) {
        console.warn('[tour] could not look down on the room', err);
      } finally {
        moving.current = false;
        runQueued();
      }
    })();
  }, [sdk, aerialTarget]); // eslint-disable-line react-hooks/exhaustive-deps

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
          <HouseLoader label={!connected ? 'Starting the tour…' : !sdk ? 'Loading the house…' : 'Opening the house…'} leaving={revealed} />
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
      {sdk && revealed && <HousePlan twin={twin} />}
      {sdk && revealed && arrangingFromPhone && roomId && (
        <div className="absolute bottom-4 left-3 z-10">
          <LiveArrangeMinimap sdk={sdk} twin={twin} roomId={roomId} pieces={roomPieces} items={itemsById} selected={arranging} />
        </div>
      )}
      <div className="pointer-events-none absolute top-16 right-3 flex flex-wrap justify-end gap-1.5">
        {/* The frame check runs always (see window.__ITHACA__.frameCheck); it shows only when it fails. */}
        {frame && !frameOk && (
          <GlassBadge
            tone={frameOk ? 'leaf' : 'sun'}
            title="Compares the twin's sweep positions with the positions the Showcase SDK reports. They must match for furniture to land where the layout says (I-G9)."
          >
            Frame check: {frame.matched} of {frame.total} sweeps match, max {(frame.maxError * 100).toFixed(1)} cm apart
          </GlassBadge>
        )}
        {!canPlaceObjects && placements.length > 0 && (
          <GlassBadge tone="sun" title="The hosted Matterport viewer cannot add 3D objects. Set NEXT_PUBLIC_MATTERPORT_VIEWER=bundle to place furniture in the tour, or see it in the Sun Lab.">
            Furniture is not shown in this viewer
          </GlassBadge>
        )}
        {placed.count > 0 && (
          <GlassBadge tone="leaf">
            {placed.count} items placed
          </GlassBadge>
        )}
        {placed.error && (
          <GlassBadge tone="danger" title={placed.error}>
            Furniture could not be placed
          </GlassBadge>
        )}
      </div>
      {sdk && revealed && canDefurnish && (
        <div className={cn(GLASS, 'absolute right-3 bottom-4 z-10 rounded-full p-1')}>
          <button type="button" aria-pressed={defurnished} onClick={() => useTour.getState().setDefurnished(!defurnished)} data-testid="defurnish-toggle"
            title={defurnished ? "Show the seller's furniture in the scan" : "Paint the seller's furniture out of the scan"}
            className={cn('h-8 rounded-full px-3.5 text-sm font-semibold transition-colors', glassToggle(defurnished))}>
            No furniture
          </button>
        </div>
      )}
      {config.features.has('sound') && sdk && revealed && (
        <div className="pointer-events-none absolute inset-x-0 bottom-4 z-10 flex justify-center [&>*]:pointer-events-auto">
          <SoundTimelapse twin={twin} active={active} />
        </div>
      )}
    </div>
  );
}
