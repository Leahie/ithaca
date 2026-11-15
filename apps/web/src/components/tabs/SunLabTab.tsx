'use client';

import { furnitureOnScreen, previewLayout } from '@/lib/layouts';
import type { Opening, PropertyTwin } from '@ithaca/schema';
import { PerformanceMonitor } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AnnotationPanel, type AnnotationTool } from '@/components/sunlab/AnnotationPanel';
import { Scene, type PlacedItem, type SunLabView } from '@/components/sunlab/Scene';
import { SunPanel, useRoomSunReport } from '@/components/sunlab/SunPanel';
import { TimeScrubber } from '@/components/sunlab/TimeScrubber';
import { Button, Notice, Spinner, Toggle, cn } from '@/components/ui';
import { api } from '@/lib/api';
import { useHouseFurniture, useInventory, useLayouts } from '@/lib/data/hooks';
import { invalidate } from '@/lib/data/resource';
import { defaultRoom, roomName } from '@/lib/rooms';
import type { V3 } from '@/lib/geometry/frames';
import { compassToInterior } from '@/lib/geometry/frames';
import { loadHouseInfo, type HouseInfo } from '@/lib/house/house';
import { sunToward } from '@/lib/house/sun';
import { useSession } from '@/lib/store/session';
import { openingFromCorners } from '@/lib/sunlab/annotate';
import { sunPosition } from '@/lib/sun/position';

/**
 * Sun Lab: the house rebuilt from the floor plan, with its baked light and the block around it,
 * under a physically placed sun (components/sunlab/Scene.tsx, lib/house).
 * The light direction is the one value the frontend computes (A3); everything else shown
 * here (sun hours, heatmap) is read from the server's SunReport.
 */
export function SunLabTab({ twin, active }: { twin: PropertyTwin; active: boolean }) {
  const datetime = useSession((s) => s.viewer.datetime);
  const roomId = useSession((s) => s.viewer.room_id);
  const gotoSeq = useSession((s) => s.viewer.goto_seq);
  const selectedLayoutId = useSession((s) => s.viewer.selected_layout_id);
  const dispatch = useSession((s) => s.dispatch);
  const layouts = useLayouts(twin.property_id);
  const house = useHouseFurniture(twin.property_id);
  const inventory = useInventory();

  const room = useMemo(() => twin.rooms.find((r) => r.room_id === roomId) ?? null, [twin, roomId]);
  const [view, setView] = useState<SunLabView>(() => (room ? { kind: 'room', room, seq: 0 } : { kind: 'overview', seq: 0 }));
  const [ready, setReady] = useState(false);
  const [assetError, setAssetError] = useState<string | null>(null);
  // Resolution follows the frame rate: from 1 up to the screen's own pixel ratio (at most 2), in
  // quarter steps. drei's monitor lowers it below ~60 fps (on a 120 Hz screen) and raises it above ~100.
  const [dprFactor, setDprFactor] = useState(0.5);
  const maxDpr = typeof window === 'undefined' ? 1 : Math.min(2, Math.max(1, window.devicePixelRatio || 1));
  const dpr = 1 + (maxDpr - 1) * dprFactor;
  // A clean view first: the live sun on the scan. Numbers, heatmap and window outlines are a click away.
  const [panelOpen, setPanelOpen] = useState(false);
  const [heatmapOn, setHeatmapOn] = useState(false);
  const [showOpenings, setShowOpenings] = useState(false);

  // The rebuilt house: its house.json first (it says which way north is), then the model.
  const houseUrl = twin.assets.house ?? null;
  const [rebuilt, setRebuilt] = useState<{ url: string; info: HouseInfo } | null>(null);
  const [houseFailed, setHouseFailed] = useState(false);
  useEffect(() => {
    setRebuilt(null);
    setHouseFailed(false);
    if (!houseUrl) return;
    let alive = true;
    loadHouseInfo(houseUrl).then(
      (info) => alive && setRebuilt({ url: houseUrl, info }),
      (err) => {
        if (!alive) return;
        setHouseFailed(true);
        setAssetError(`The rebuilt house could not be loaded: ${err instanceof Error ? err.message : String(err)}`);
      },
    );
    return () => {
      alive = false;
    };
  }, [houseUrl]);
  // Draw once it is known what to draw: the house, or (without one) the rooms' outlines.
  const sceneReady = !houseUrl || !!rebuilt || houseFailed;

  // Start indoors: with no room chosen yet, go to the main room. A viewer.goto like any other (A1).
  useEffect(() => {
    if (!active || useSession.getState().viewer.room_id) return;
    const target = defaultRoom(twin);
    if (target) dispatch('viewer.goto', { room_id: target.room_id });
  }, [active, twin, dispatch]);

  // viewer.goto flies the camera into the room.
  useEffect(() => {
    if (gotoSeq === 0) return;
    const target = twin.rooms.find((r) => r.room_id === useSession.getState().viewer.room_id);
    if (target) setView({ kind: 'room', room: target, seq: gotoSeq });
  }, [gotoSeq, twin]);

  // Sun: suncalc position, turned into the interior frame. North is the one measured for the
  // rebuilt house on Vexcel's aerial survey when there is one (Matterport's georeference, which
  // the twin's transform comes from, is several degrees off); else the twin's transform.
  const north = rebuilt?.info.orientation?.north ?? null;
  const sun = useMemo(() => {
    const pos = sunPosition(new Date(datetime), twin.lat, twin.lon);
    const direction = north ? sunToward(pos.azimuth_deg, pos.elevation_deg, north) : compassToInterior(twin.interior_to_world, pos.azimuth_deg, pos.elevation_deg);
    return { ...pos, direction: direction as V3 };
  }, [datetime, twin, north]);

  // The house's furniture (or the layout being tried in its room).
  const placements = useMemo<PlacedItem[]>(() => {
    const items = new Map((inventory.data ?? []).map((i) => [i.item_id, i]));
    return furnitureOnScreen(house.data, previewLayout(layouts.data, selectedLayoutId)).flatMap((p) => {
      const item = items.get(p.item_id);
      return item ? [{ item_id: p.item_id, url: item.glb_url, position: p.position, yaw_deg: p.yaw_deg }] : [];
    });
  }, [house.data, layouts.data, inventory.data, selectedLayoutId]);

  // Openings: a working copy, so marks show (and let light in) before they are saved.
  const [draft, setDraft] = useState<Opening[] | null>(null);
  const openings = draft ?? twin.openings;
  const [tool, setTool] = useState<AnnotationTool | null>(null);
  const [corner, setCorner] = useState<{ point: V3; normal: V3 } | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  useEffect(() => {
    setCorner(null);
    setHint(tool ? 'Click the first corner.' : null);
  }, [tool]);

  const onWallClick = useCallback(
    (point: V3, normal: V3) => {
      if (!tool) return;
      if (!corner) {
        setCorner({ point, normal });
        setHint('Now click the opposite corner.');
        return;
      }
      const result = openingFromCorners(twin, { a: corner.point, b: point, normal: corner.normal, ...tool });
      setCorner(null);
      if (!result.ok) {
        setHint(result.reason);
        return;
      }
      setDraft([...openings, result.opening]);
      setHint(`Added a ${tool.kind}. Click the first corner of the next one.`);
    },
    [tool, corner, twin, openings],
  );

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setSaveError(null);
    try {
      await api().putOpenings(twin.property_id, draft);
      invalidate(`twin:${twin.property_id}`);
      invalidate(`sun:${twin.property_id}`);
      setDraft(null);
      setTool(null);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const report = useRoomSunReport(twin, room);
  const heatmap = heatmapOn && room && report.data?.heatmap ? { room, data: report.data.heatmap } : null;
  const onReady = useCallback(() => setReady(true), []);

  return (
    <div
      className="relative h-full w-full bg-[#0a0e18]"
      data-testid="sun-lab"
      data-ready={ready}
      data-sun-azimuth={sun.azimuth_deg.toFixed(3)}
      data-sun-elevation={sun.elevation_deg.toFixed(3)}
      data-sun-direction={sun.direction.map((v) => v.toFixed(5)).join(',')}
      data-openings={openings.length}
      data-placed={placements.length}
    >
      {/* flat: the house's light is worked out in its own shaders, with no tone mapping on top. */}
      <Canvas
        flat
        frameloop={active ? 'always' : 'never'}
        dpr={dpr}
        camera={{ fov: 58, near: 0.08, far: 3000, position: [-40, 30, 30] }}
        className={cn(tool && 'cursor-crosshair')}
      >
        <PerformanceMonitor flipflops={3} onChange={({ factor }) => setDprFactor(Math.round(factor * 4) / 4)} onFallback={() => setDprFactor(0)} />
        {sceneReady && (
          <Scene
            twin={twin}
            house={rebuilt}
            openings={openings}
            sun={sun}
            placements={placements}
            heatmap={heatmap}
            showOpenings={showOpenings}
            onWallClick={tool ? onWallClick : null}
            pendingCorner={corner?.point ?? null}
            view={view}
            onAssetError={setAssetError}
            onReady={onReady}
          />
        )}
      </Canvas>

      {!ready && !assetError && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <Spinner label="Loading the house…" />
        </div>
      )}
      {(!houseUrl || houseFailed) && (
        <div className="absolute top-12 left-3 w-[300px]">
          <Notice tone="warn" title="No rebuilt house yet">
            This twin has no house rebuilt from its floor plan, so rooms are drawn from their outlines.
          </Notice>
        </div>
      )}
      {assetError && (
        <div className="absolute top-3 left-1/2 w-[420px] max-w-[90%] -translate-x-1/2">
          <Notice tone="error" title="A 3D asset is missing">
            {assetError} If this is a fixture, check that Git LFS files are pulled.
          </Notice>
        </div>
      )}

      <div className="absolute top-3 left-3 flex gap-1.5">
        <Button size="sm" variant={view.kind === 'overview' ? 'primary' : 'subtle'} onClick={() => setView({ kind: 'overview', seq: Date.now() })}>
          Outside
        </Button>
        <Button
          size="sm"
          variant={view.kind === 'room' ? 'primary' : 'subtle'}
          title={room ? `Fly into ${roomName(twin, room.room_id)}` : 'Fly into the house'}
          onClick={() => {
            if (room) return setView({ kind: 'room', room, seq: Date.now() });
            // No room chosen yet: go into the main room. It is a viewer.goto like any other (A1).
            const target = defaultRoom(twin);
            if (target) dispatch('viewer.goto', { room_id: target.room_id });
          }}
        >
          {room ? `Inside ${roomName(twin, room.room_id)}` : 'Go inside'}
        </Button>
      </div>

      <div className="absolute top-3 right-3 bottom-3 flex flex-col items-end gap-2">
        <Button size="sm" onClick={() => setPanelOpen(!panelOpen)} aria-expanded={panelOpen}>
          {panelOpen ? 'Hide details' : 'Sun details'}
        </Button>
        {panelOpen && (
          <div className="min-h-0 w-[300px] space-y-4 overflow-y-auto rounded-[var(--radius-card)] border border-line bg-raised p-4 shadow-float">
            <SunPanel twin={twin} room={room} heatmapOn={heatmapOn} onHeatmap={setHeatmapOn} />
            <div className="space-y-3 border-t border-line pt-3">
              <Toggle label="Show windows and doors" checked={showOpenings} onChange={setShowOpenings} />
              <AnnotationPanel
                twin={twin}
                openings={openings}
                roomId={room?.room_id ?? null}
                dirty={draft !== null}
                saving={saving}
                error={saveError}
                hint={hint}
                tool={tool}
                onTool={setTool}
                onDelete={(id) => setDraft(openings.filter((o) => o.opening_id !== id))}
                onSave={save}
                onDiscard={() => {
                  setDraft(null);
                  setTool(null);
                  setSaveError(null);
                }}
              />
            </div>
          </div>
        )}
      </div>

      <div className={cn('absolute bottom-3 left-3 w-[min(560px,calc(100%-24px))]', panelOpen && 'max-w-[calc(100%-340px)]')}>
        <TimeScrubber twin={twin} />
      </div>
    </div>
  );
}
