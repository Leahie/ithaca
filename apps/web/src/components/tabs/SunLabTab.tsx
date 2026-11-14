'use client';

import type { Opening, PropertyTwin } from '@ithaca/schema';
import { Canvas } from '@react-three/fiber';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { AnnotationPanel, type AnnotationTool } from '@/components/sunlab/AnnotationPanel';
import { Scene, type PlacedItem, type SunLabView } from '@/components/sunlab/Scene';
import { SunPanel, useRoomSunReport } from '@/components/sunlab/SunPanel';
import { TimeScrubber } from '@/components/sunlab/TimeScrubber';
import { Button, Notice, Spinner, Toggle, cn } from '@/components/ui';
import { api } from '@/lib/api';
import { useInventory, useLayouts } from '@/lib/data/hooks';
import { invalidate } from '@/lib/data/resource';
import { defaultRoom, roomName } from '@/lib/rooms';
import type { V3 } from '@/lib/geometry/frames';
import { compassToInterior } from '@/lib/geometry/frames';
import { useSession } from '@/lib/store/session';
import { openingFromCorners } from '@/lib/sunlab/annotate';
import { sunPosition } from '@/lib/sun/position';

/**
 * Sun Lab: the interior mesh and its surroundings under a physically placed sun.
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
  const inventory = useInventory();

  const room = useMemo(() => twin.rooms.find((r) => r.room_id === roomId) ?? null, [twin, roomId]);
  const [view, setView] = useState<SunLabView>(() => (room ? { kind: 'room', room, seq: 0 } : { kind: 'overview', seq: 0 }));
  const [ready, setReady] = useState(false);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [heatmapOn, setHeatmapOn] = useState(true);
  const [showOpenings, setShowOpenings] = useState(true);

  // viewer.goto flies the camera into the room.
  useEffect(() => {
    if (gotoSeq === 0) return;
    const target = twin.rooms.find((r) => r.room_id === useSession.getState().viewer.room_id);
    if (target) setView({ kind: 'room', room: target, seq: gotoSeq });
  }, [gotoSeq, twin]);

  // Sun: suncalc position, turned into the interior frame by the twin's transform.
  const sun = useMemo(() => {
    const pos = sunPosition(new Date(datetime), twin.lat, twin.lon);
    return { ...pos, direction: compassToInterior(twin.interior_to_world, pos.azimuth_deg, pos.elevation_deg) as V3 };
  }, [datetime, twin]);

  // Furniture of the chosen layout.
  const placements = useMemo<PlacedItem[]>(() => {
    const layout = layouts.data?.find((l) => l.layout_id === selectedLayoutId);
    const items = new Map((inventory.data ?? []).map((i) => [i.item_id, i]));
    return (layout?.placements ?? []).flatMap((p) => {
      const item = items.get(p.item_id);
      return item ? [{ item_id: p.item_id, url: item.glb_url, position: p.position, yaw_deg: p.yaw_deg }] : [];
    });
  }, [layouts.data, inventory.data, selectedLayoutId]);

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
      <Canvas
        shadows
        frameloop={active ? 'always' : 'never'}
        dpr={[1, 1.75]}
        camera={{ fov: 58, near: 0.08, far: 900, position: [-40, 30, 30] }}
        className={cn(tool && 'cursor-crosshair')}
      >
        <Scene
          twin={twin}
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
      </Canvas>

      {!ready && !assetError && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          <Spinner label="Loading the house model…" />
        </div>
      )}
      {!twin.assets.interior_glb && (
        <div className="absolute top-12 left-3 w-[300px]">
          <Notice tone="warn" title="No scanned mesh yet">
            This twin has no interior model, so rooms are drawn from their outlines.
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
          <div className="min-h-0 w-[300px] space-y-4 overflow-y-auto rounded-xl border border-line-strong bg-surface/95 p-4 shadow-xl backdrop-blur">
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
