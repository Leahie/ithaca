'use client';

import { useFrame, useThree } from '@react-three/fiber';
import { useEffect, useRef } from 'react';
import { Color, Matrix4, type Object3D, type Vector3 } from 'three';

/**
 * The aerial Gaussian splat of the property and its surroundings (twin/splat.py), rendered with
 * Spark. The file is the drone capture minus the house (the scanned mesh is the house); its 4x4
 * transform places it in the interior frame. Its colors are the light of the capture day, so they
 * are dimmed with the daylight; it does not take the simulated sun or cast shadows.
 *
 * It is the outside only: hidden whenever the camera is inside the house (`inside`), so the
 * interior is the scan alone.
 *
 * Performance: the capture has ~5.8M Gaussians. A level-of-detail tree (built in a worker after
 * load) lets Spark draw a fixed budget of them, coarser far away; the renderer is switched off
 * along with the splat, so a hidden splat costs nothing per frame.
 */
/** Splats drawn per frame (Spark's desktop default is 2.5M; integrated GPUs choke on that). */
const LOD_SPLATS = 1_200_000;
/** Smallest splat drawn, in screen pixels: smaller ones are folded into their parents. */
const LOD_MIN_PX = 2;
export function Splat({
  url,
  matrix,
  visible,
  inside,
  daylight,
  onError,
}: {
  url: string;
  matrix: number[];
  visible: boolean;
  inside(p: Vector3): boolean;
  daylight: number;
  onError(message: string): void;
}) {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const mesh = useRef<(Object3D & { recolor: Color }) | null>(null);
  const renderer = useRef<Object3D | null>(null);
  const shown = useRef(visible);
  shown.current = visible;
  const errorRef = useRef(onError);
  errorRef.current = onError;

  useEffect(() => {
    let disposed = false;
    const added: Object3D[] = [];
    void (async () => {
      // Spark touches WebGL and WebAssembly when imported: load it on the client only.
      const { SparkRenderer, SplatMesh, SplatFileType } = await import('@sparkjsdev/spark');
      if (disposed) return;
      const spark = new SparkRenderer({ renderer: gl, lodSplatCount: LOD_SPLATS, lodRenderScale: LOD_MIN_PX });
      const splat = new SplatMesh({ url, fileType: SplatFileType.PLY, lod: true });
      new Matrix4().fromArray(matrix).decompose(splat.position, splat.quaternion, splat.scale);
      splat.visible = shown.current;
      spark.visible = shown.current;
      scene.add(spark, splat);
      added.push(spark, splat);
      mesh.current = splat;
      renderer.current = spark;
      splat.initialized.catch((err: unknown) => errorRef.current(`The aerial splat could not be loaded: ${err instanceof Error ? err.message : String(err)}`));
    })().catch((err: unknown) => errorRef.current(`The splat renderer could not start: ${err instanceof Error ? err.message : String(err)}`));
    return () => {
      disposed = true;
      mesh.current = null;
      renderer.current = null;
      for (const o of added) {
        scene.remove(o);
        (o as unknown as { dispose?: () => void }).dispose?.();
      }
    };
  }, [gl, scene, url, matrix]);

  useFrame(({ camera }) => {
    const m = mesh.current;
    if (!m) return;
    m.visible = shown.current && !inside(camera.position);
    if (renderer.current) renderer.current.visible = m.visible;
    m.recolor.setScalar(daylight);
  });
  return null;
}
