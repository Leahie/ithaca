'use client';

import type { InventoryItem, LayoutVariant, Placement, PropertyTwin, Room } from '@ithaca/schema';
import { useGLTF } from '@react-three/drei';
import { Canvas, useThree } from '@react-three/fiber';
import { Component, Suspense, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { DoubleSide, FrontSide, type DirectionalLight } from 'three';
import { api } from '@/lib/api';
import { bounds } from '@/lib/geometry/polygon';
import { floorGeometry, wallGeometry } from '@/lib/three/roomShell';

declare global {
  interface Window {
    /** Section 2.6: true once the final frame is on screen. The thumbnail job waits for it. */
    __RENDER_READY__?: boolean;
    /** Set instead of the ready flag when the layout cannot be drawn. */
    __RENDER_ERROR__?: string;
  }
}

const DEG = Math.PI / 180;

/** Item as a plain box from its dimensions. Used when its GLB cannot be loaded. */
function BoxItem({ item, placement }: { item: InventoryItem; placement: Placement }) {
  const { w, d, h } = item.dims_m;
  return (
    <group position={[placement.position.x, placement.position.y, placement.position.z]} rotation={[0, placement.yaw_deg * DEG, 0]}>
      <mesh position={[0, h / 2, 0]} castShadow receiveShadow>
        <boxGeometry args={[w, h, d]} />
        <meshStandardMaterial color="#9aa3ad" roughness={0.9} />
      </mesh>
    </group>
  );
}

function GlbItem({ item, placement, onLoaded }: { item: InventoryItem; placement: Placement; onLoaded(): void }) {
  const gltf = useGLTF(item.glb_url);
  const scene = useMemo(() => {
    const root = gltf.scene.clone(true);
    root.traverse((o) => {
      o.castShadow = true;
      o.receiveShadow = true;
    });
    return root;
  }, [gltf]);
  useEffect(() => onLoaded(), [onLoaded, scene]);
  // A5: the stored pose, as it is.
  return <primitive object={scene} position={[placement.position.x, placement.position.y, placement.position.z]} rotation={[0, placement.yaw_deg * DEG, 0]} />;
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

/** A9: one fixed light per room, above its center and aimed at it. */
function FixedLight({ room }: { room: Room }) {
  const light = useRef<DirectionalLight>(null);
  const invalidate = useThree((s) => s.invalidate);
  const b = bounds(room.polygon);
  const cx = (b.minX + b.maxX) / 2;
  const cz = (b.minZ + b.maxZ) / 2;
  const reach = Math.hypot(b.maxX - b.minX, b.maxZ - b.minZ) / 2 + 1;
  useEffect(() => {
    const l = light.current;
    if (!l) return;
    l.target.position.set(cx, room.floor_y, cz);
    l.target.updateMatrixWorld();
    invalidate();
  }, [cx, cz, room.floor_y, invalidate]);
  return (
    <directionalLight
      ref={light}
      position={[cx + 3, room.floor_y + 10, cz + 2]}
      intensity={1.6}
      castShadow
      shadow-mapSize={[2048, 2048]}
      shadow-camera-left={-reach}
      shadow-camera-right={reach}
      shadow-camera-top={reach}
      shadow-camera-bottom={-reach}
      shadow-bias={-0.0004}
    />
  );
}

function RoomShell({ room }: { room: Room }) {
  const floor = useMemo(() => floorGeometry(room), [room]);
  const walls = useMemo(() => wallGeometry(room), [room]);
  return (
    <>
      <mesh geometry={floor} receiveShadow>
        <meshStandardMaterial color="#cdbfa9" roughness={0.95} side={DoubleSide} />
      </mesh>
      <mesh geometry={walls} receiveShadow>
        <meshStandardMaterial color="#efe9df" roughness={1} side={FrontSide} />
      </mesh>
    </>
  );
}

interface Loaded {
  layout: LayoutVariant;
  twin: PropertyTwin;
  room: Room;
  items: Map<string, InventoryItem>;
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
        if (live) setData({ layout, twin, room, items: new Map(inventory.map((i) => [i.item_id, i])) });
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

  const drawable = data ? data.layout.placements.filter((p) => data.items.has(p.item_id)) : [];
  const done = !!data && settled >= drawable.length;

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
  if (!data) return <main className="h-dvh bg-[#f3efe8]" />;

  const settle = () => setSettled((n) => n + 1);

  return (
    <main className="h-dvh w-screen bg-[#f3efe8]" data-testid="render" data-layout-id={layoutId} data-ready={done}>
      <Canvas frameloop="demand" dpr={1} shadows gl={{ antialias: true, preserveDrawingBuffer: true }} camera={{ fov: 38, near: 0.1, far: 200 }}>
        <color attach="background" args={['#f3efe8']} />
        <FixedCamera room={data.room} />
        <ambientLight intensity={0.85} />
        <FixedLight room={data.room} />
        <RoomShell room={data.room} />
        {drawable.map((p) => {
          const item = data.items.get(p.item_id)!;
          return (
            <ItemBoundary key={p.item_id} fallback={<BoxItem item={item} placement={p} />} onFailed={settle}>
              <Suspense fallback={null}>
                <GlbItem item={item} placement={p} onLoaded={settle} />
              </Suspense>
            </ItemBoundary>
          );
        })}
      </Canvas>
    </main>
  );
}
