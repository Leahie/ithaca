'use client';

import type { InventoryItem, LayoutVariant, Placement, PropertyTwin, Room } from '@ithaca/schema';
import { Canvas, useThree } from '@react-three/fiber';
import { Component, Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Furniture, FurnitureBox, HouseLight, HouseModel, RoomShells, sunAt, type RebuiltHouse } from '@/components/house3d/House3D';
import { api } from '@/lib/api';
import { bounds } from '@/lib/geometry/polygon';
import { loadHouseInfo } from '@/lib/house/house';
import { fromZonedParts } from '@/lib/time';

declare global {
  interface Window {
    /** Section 2.6: true once the final frame is on screen. The thumbnail job waits for it. */
    __RENDER_READY__?: boolean;
    /** Set instead of the ready flag when the layout cannot be drawn. */
    __RENDER_ERROR__?: string;
  }
}

const DEG = Math.PI / 180;

/** A9: one fixed light for every picture: the sun of a spring morning in the house's own time zone. */
const MOMENT = { year: 2026, month: 3, day: 20, hour: 10, minute: 0 };

function GlbItem({ item, placement, onLoaded }: { item: InventoryItem; placement: Placement; onLoaded(): void }) {
  return <Furniture item={{ item_id: item.item_id, url: item.glb_url, position: placement.position, yaw_deg: placement.yaw_deg }} onLoaded={onLoaded} />;
}

class ItemBoundary extends Component<{ fallback: ReactNode; onFailed(): void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    this.props.onFailed();
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/** A9: one fixed camera per room, derived from the room outline only. */
function FixedCamera({ room }: { room: Room }) {
  const camera = useThree((s) => s.camera);
  const invalidate = useThree((s) => s.invalidate);
  useEffect(() => {
    const b = bounds(room.polygon);
    const cx = (b.minX + b.maxX) / 2;
    const cz = (b.minZ + b.maxZ) / 2;
    const r = Math.hypot(b.maxX - b.minX, b.maxZ - b.minZ) / 2;
    // Far enough that the room's bounding circle fits the vertical field of view.
    const fov = 'fov' in camera ? (camera.fov as number) : 38;
    const d = (r / Math.sin((fov * DEG) / 2)) * 1.02;
    camera.position.set(cx + d * 0.12, room.floor_y + d * 0.8, cz + d * 0.59);
    camera.lookAt(cx, room.floor_y + 0.3, cz);
    camera.updateProjectionMatrix();
    invalidate();
  }, [camera, invalidate, room]);
  return null;
}

interface Loaded {
  layout: LayoutVariant;
  twin: PropertyTwin;
  room: Room;
  items: Map<string, InventoryItem>;
  /** The rebuilt house, if the twin has one. */
  house: RebuiltHouse | null;
}

/** Draws once more when everything is in, so the last frame has every shadow. */
function FinalFrame({ done }: { done: boolean }) {
  const frame = useThree((s) => s.invalidate);
  useEffect(() => {
    if (done) frame();
  }, [done, frame]);
  return null;
}

/**
 * Deterministic render of one layout for the thumbnail job (section 2.6, A9):
 * fixed camera, fixed light, no animation, frames on demand only.
 */
export function LayoutRender({ layoutId }: { layoutId: string }) {
  const [data, setData] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settled, setSettled] = useState(0);

  useEffect(() => {
    let live = true;
    window.__RENDER_READY__ = false;
    void (async () => {
      try {
        const layout = await api().getLayout(layoutId);
        const [twin, inventory] = await Promise.all([api().getTwin(layout.property_id), api().listInventory()]);
        const room = twin.rooms.find((r) => r.room_id === layout.room_id);
        if (!room) throw new Error(`Room ${layout.room_id} is not in the twin of ${layout.property_id}`);
        const house = twin.assets.house ? { url: twin.assets.house, info: await loadHouseInfo(twin.assets.house) } : null;
        if (live) setData({ layout, twin, room, items: new Map(inventory.map((i) => [i.item_id, i])), house });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        window.__RENDER_ERROR__ = message;
        if (live) setError(message);
      }
    })();
    return () => {
      live = false;
    };
  }, [layoutId]);

  const [houseIn, setHouseIn] = useState(false);
  const drawable = data ? data.layout.placements.filter((p) => data.items.has(p.item_id)) : [];
  const done = !!data && settled >= drawable.length && (!data.house || houseIn);
  const sun = useMemo(() => (data ? sunAt(data.twin, data.house, fromZonedParts(MOMENT, data.twin.timezone)) : null), [data]);
  const fail = useCallback((message: string) => {
    window.__RENDER_ERROR__ = message;
    setError(message);
  }, []);

  // Ready means: every item has loaded (or fallen back to a box) and two frames have passed.
  useEffect(() => {
    if (!done) return;
    let raf2 = 0;
    const raf1 = requestAnimationFrame(() => {
      raf2 = requestAnimationFrame(() => {
        window.__RENDER_READY__ = true;
        document.documentElement.dataset.renderReady = 'true';
      });
    });
    return () => {
      cancelAnimationFrame(raf1);
      cancelAnimationFrame(raf2);
    };
  }, [done]);

  if (error) {
    return (
      <main className="flex h-dvh items-center justify-center bg-white p-8 text-center text-sm text-red-700" data-testid="render-error">
        Cannot render layout {layoutId}: {error}
      </main>
    );
  }
  if (!data || !sun) return <main className="h-dvh bg-[#f3efe8]" />;

  const settle = () => setSettled((n) => n + 1);
  const { twin, room, house } = data;

  return (
    <main className="h-dvh w-screen bg-[#f3efe8]" data-testid="render" data-layout-id={layoutId} data-ready={done}>
      {/* flat: the house's light is worked out in its own shaders, with no tone mapping on top. */}
      <Canvas flat frameloop="demand" dpr={1} gl={{ antialias: true, preserveDrawingBuffer: true }} camera={{ fov: 38, near: 0.1, far: 3000 }}>
        <FixedCamera room={room} />
        <HouseLight twin={twin} openings={twin.openings} sun={sun} cutOpenings={!house} lamps="work">
          {house ? (
            // From above, without the lid or the storeys over the room.
            <HouseModel url={house.url} info={house.info} inside={false} topStorey={storeyOf(house, room)} onAssetError={fail} onComplete={() => setHouseIn(true)} />
          ) : (
            <RoomShells rooms={[room]} />
          )}
          {drawable.map((p) => {
            const item = data.items.get(p.item_id)!;
            return (
              <ItemBoundary key={p.item_id} fallback={<FurnitureBox position={p.position} yaw_deg={p.yaw_deg} dims={item.dims_m} />} onFailed={settle}>
                <Suspense fallback={null}>
                  <GlbItem item={item} placement={p} onLoaded={settle} />
                </Suspense>
              </ItemBoundary>
            );
          })}
        </HouseLight>
        <FinalFrame done={done} />
      </Canvas>
    </main>
  );
}

/** The house's storey a room is on: the one whose floor is nearest the room's. */
function storeyOf(house: RebuiltHouse, room: Room): number {
  let best = 0;
  house.info.storeys.forEach((s, i) => {
    if (Math.abs(s.z - room.floor_y) < Math.abs((house.info.storeys[best]?.z ?? 0) - room.floor_y)) best = i;
  });
  return best;
}
