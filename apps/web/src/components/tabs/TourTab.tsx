'use client';

import type { InventoryItem, LayoutVariant, PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Notice, Spinner, ViewState } from '@/components/ui';
import { api } from '@/lib/api';
import { config } from '@/lib/config';
import { useInventory, useLayouts } from '@/lib/data/hooks';
import { sweepForRoom } from '@/lib/showcase/rooms';
import type { MpSdk } from '@/lib/showcase/sdk-types';
import { onSessionEvent, useSession } from '@/lib/store/session';

interface ViewerElement extends HTMLElement {
  /** Resolves when the SDK is connected (app phase "starting"). */
  sdkPromise: Promise<MpSdk>;
  /** Resolves when the tour is interactive (app phase "playing"). */
  playingPromise: Promise<MpSdk>;
}

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

const exposeForTests = (key: string, value: unknown) => {
  window.__ITHACA__ = { ...window.__ITHACA__, [key]: value };
};

/**
 * Photoreal tour: the Matterport Showcase (Bundle SDK via the web component).
 * `viewer.goto` moves the camera; the chosen layout's furniture is placed with
 * `Scene.createObjects` + `mp.gltfLoader` using the stored poses as they are (A5).
 */
export function TourTab({ twin, active }: { twin: PropertyTwin; active: boolean }) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewerRef = useRef<ViewerElement | null>(null);
  const removeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  /** The SDK as soon as it is connected: model data is readable before the tour can move. */
  const [connected, setConnected] = useState<MpSdk | null>(null);
  const [ready, setReady] = useState(false);
  const [sdk, setSdk] = useState<MpSdk | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [frame, setFrame] = useState<FrameCheck | null>(null);
  const [placed, setPlaced] = useState<{ count: number; error: string | null }>({ count: 0, error: null });
  const sdkSweeps = useRef<Map<string, SdkSweep>>(new Map());
  const programmatic = useRef<string | null>(null);

  const roomId = useSession((s) => s.viewer.room_id);
  const sweepId = useSession((s) => s.viewer.sweep_id);
  const gotoSeq = useSession((s) => s.viewer.goto_seq);
  const selectedLayoutId = useSession((s) => s.viewer.selected_layout_id);
  const layouts = useLayouts(twin.property_id);
  const inventory = useInventory();

  const modelId = twin.sources.matterport_model_id; // I-G16: never assume it equals property_id

  // The web component touches `window` on import, so load it on the client only.
  useEffect(() => {
    let live = true;
    import('@matterport/webcomponent').then(
      () => live && setReady(true),
      (err: unknown) => live && setError(`The Matterport viewer failed to load: ${err instanceof Error ? err.message : String(err)}`),
    );
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => {
    const host = hostRef.current;
    if (!ready || !host) return;
    // Built by hand: the viewer reads its config from attributes, and React 19 would set
    // them as properties on a custom element (the model id then arrives empty).
    // One element per model, kept across effect re-runs: the Showcase bundle does not start a
    // second time on a page where a first instance was just torn down (React StrictMode
    // mounts effects twice in development).
    if (removeTimer.current) clearTimeout(removeTimer.current);
    removeTimer.current = null;
    let node = viewerRef.current;
    if (!node || node.getAttribute('m') !== modelId) {
      node?.remove();
      node = document.createElement('matterport-viewer') as ViewerElement;
      const attrs: Record<string, string> = {
        m: modelId,
        'application-key': config.matterportSdkKey,
        'asset-base': '/matterport-assets',
        qs: '1',
        play: '1',
        brand: '0',
        title: '0',
        help: '0',
        search: '0',
      };
      for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
      host.appendChild(node);
      viewerRef.current = node;
    }
    const viewer = node;
    let live = true;
    const onError = (ev: Event) => setError((ev as CustomEvent<{ error: Error }>).detail?.error?.message ?? 'The Matterport viewer reported an error.');
    viewer.addEventListener('mpViewerError', onError);
    viewer.sdkPromise.then(
      (s) => live && setConnected(s),
      () => {},
    );
    viewer.playingPromise.then(
      (s) => live && setSdk(s),
      (err: unknown) => live && setError(err instanceof Error ? err.message : String(err)),
    );
    return () => {
      live = false;
      viewer.removeEventListener('mpViewerError', onError);
      // Remove on a real unmount only: a StrictMode re-run cancels this timer first.
      removeTimer.current = setTimeout(() => {
        viewer.remove();
        if (viewerRef.current === viewer) viewerRef.current = null;
      }, 0);
    };
  }, [ready, modelId]);

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
    const twinSweeps = new Map(twin.sweeps.map((s) => [s.sweep_id, s]));
    const currentSub = sdk.Sweep.current.subscribe((current) => {
      const id = current.id;
      if (!id) return;
      if (programmatic.current) {
        if (programmatic.current === id) programmatic.current = null;
        return;
      }
      const s = useSession.getState();
      const room = twinSweeps.get(id)?.room_id;
      if (room && room !== s.viewer.room_id) s.dispatch('viewer.goto', { sweep_id: id });
    });
    return () => currentSub.cancel();
  }, [sdk, twin]);

  // viewer.goto -> Sweep.moveTo
  useEffect(() => {
    if (!sdk || gotoSeq === 0) return;
    const wanted = sweepId ?? (roomId ? sweepForRoom(twin, roomId)?.sweep_id : undefined);
    if (!wanted) return;
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
    programmatic.current = target;
    sdk.Sweep.moveTo(target, { transition: sdk.Sweep.Transition.FLY, transitionTime: 1400 }).catch((err: unknown) => {
      programmatic.current = null;
      console.warn('[tour] moveTo failed', err);
    });
    // The sweep list is read at the moment of the move; it is not a trigger.
  }, [sdk, gotoSeq]); // eslint-disable-line react-hooks/exhaustive-deps

  // viewer.look (phone joystick / gyroscope)
  useEffect(() => {
    if (!sdk) return;
    return onSessionEvent((event) => {
      if (event.type === 'viewer.look' && useSession.getState().viewer.mode === 'tour') {
        void sdk.Camera.rotate(event.payload.yaw_delta_deg, event.payload.pitch_delta_deg, { speed: 180 }).catch(() => {});
      }
    });
  }, [sdk]);

  // The chosen layout's furniture, in the Showcase scene.
  const placements = useMemo(() => {
    const layout: LayoutVariant | undefined = layouts.data?.find((l) => l.layout_id === selectedLayoutId);
    const items = new Map<string, InventoryItem>((inventory.data ?? []).map((i) => [i.item_id, i]));
    return (layout?.placements ?? []).flatMap((p) => {
      const item = items.get(p.item_id);
      return item ? [{ ...p, url: api().assetUrl(item.glb_url) }] : [];
    });
  }, [layouts.data, inventory.data, selectedLayoutId]);

  useEffect(() => {
    if (!sdk) return;
    let cancelled = false;
    let stop: (() => void) | null = null;
    exposeForTests('tourPlacedItemIds', []);
    if (!placements.length) {
      setPlaced({ count: 0, error: null });
      return;
    }
    void (async () => {
      try {
        const [object] = await sdk.Scene.createObjects(1);
        if (!object) throw new Error('Scene.createObjects returned nothing');
        if (cancelled) return object.stop();
        const lights = object.addNode();
        lights.addComponent('mp.ambientLight', { intensity: 1.1 });
        lights.addComponent('mp.directionalLight', { intensity: 1.4, position: { x: 2, y: 6, z: 3 }, target: { x: 0, y: 0, z: 0 } });
        for (const p of placements) {
          const node = object.addNode();
          node.addComponent('mp.gltfLoader', { url: p.url, colliderEnabled: false });
          // A5: the stored pose, as it is. Interior frame == Showcase frame (I-G9); yaw is about +Y (I-G12).
          node.position.set(p.position.x, p.position.y, p.position.z);
          const half = (p.yaw_deg * Math.PI) / 360;
          node.quaternion.set(0, Math.sin(half), 0, Math.cos(half));
        }
        object.start();
        stop = () => object.stop();
        setPlaced({ count: placements.length, error: null });
        exposeForTests('tourPlacedItemIds', placements.map((p) => p.item_id));
      } catch (err) {
        if (!cancelled) setPlaced({ count: 0, error: err instanceof Error ? err.message : String(err) });
      }
    })();
    return () => {
      cancelled = true;
      stop?.();
    };
  }, [sdk, placements]);

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
      <div ref={hostRef} className="h-full w-full" />
      {!sdk && (
        <div className="pointer-events-none absolute right-3 bottom-3 rounded-lg border border-line bg-ink/85 px-3 py-2">
          <Spinner label={connected ? 'Loading the tour…' : 'Starting the tour…'} />
        </div>
      )}
      <div className="pointer-events-none absolute top-3 left-3 flex flex-wrap gap-1.5">
        {frame && (
          <Badge
            tone={frameOk ? 'leaf' : 'sun'}
            className="bg-ink/80"
            title="Compares the twin's sweep positions with the positions the Showcase SDK reports. They must match for furniture to land where the layout says (I-G9)."
          >
            Frame check: {frame.matched} of {frame.total} sweeps match, max {(frame.maxError * 100).toFixed(1)} cm apart
          </Badge>
        )}
        {placed.count > 0 && (
          <Badge tone="sky" className="bg-ink/80">
            {placed.count} items placed
          </Badge>
        )}
        {placed.error && (
          <Badge tone="danger" className="bg-ink/80" title={placed.error}>
            Furniture could not be placed
          </Badge>
        )}
      </div>
    </div>
  );
}
