'use client';

import type { InventoryItem } from '@ithaca/schema';
import { OrbitControls, useGLTF } from '@react-three/drei';
import { Canvas } from '@react-three/fiber';
import { Component, Suspense, useMemo, type ReactNode } from 'react';

function Model({ url, scale = 1 }: { url: string; scale?: number }) {
  const gltf = useGLTF(url);
  const scene = useMemo(() => gltf.scene.clone(true), [gltf]);
  return <primitive object={scene} scale={scale} />;
}

class Boundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}

/** Turntable preview of an inventory item's GLB, on a half-meter grid for scale. */
export function ItemPreview({ item }: { item: Pick<InventoryItem, 'item_id' | 'dims_m' | 'glb_url' | 'scale'> }) {
  const { w, d, h } = item.dims_m;
  const r = Math.max(w, d, h);
  return (
    <div className="relative aspect-[4/3] w-full overflow-hidden rounded-lg border border-line bg-[#1c1a16]" data-testid="item-preview">
      <Boundary fallback={<div className="flex h-full items-center justify-center p-4 text-center text-xs text-danger">The 3D model for this item could not be loaded.</div>}>
        <Canvas key={item.item_id} camera={{ fov: 35, position: [r * 1.5, h * 0.9 + r * 0.6, r * 1.9], near: 0.05, far: 60 }} dpr={[1, 2]}>
          <ambientLight intensity={0.9} />
          <directionalLight position={[3, 5, 4]} intensity={2} />
          <directionalLight position={[-4, 2, -3]} intensity={0.5} />
          <gridHelper args={[4, 8, '#5a5348', '#34302a']} />
          <Suspense fallback={null}>
            <Model url={item.glb_url} scale={item.scale ?? 1} />
          </Suspense>
          <OrbitControls autoRotate autoRotateSpeed={1.2} enablePan={false} target={[0, h / 2, 0]} minDistance={r * 0.8} maxDistance={r * 6} />
        </Canvas>
      </Boundary>
      <span className="pointer-events-none absolute bottom-2 left-2 text-[10px] text-faint">grid: 0.5 m · drag to turn</span>
    </div>
  );
}
