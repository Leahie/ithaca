'use client';

import type { Opening, PropertyTwin, Room, SunHeatmap, Vec3 } from '@ithaca/schema';
import { CameraControls, useGLTF } from '@react-three/drei';
import { useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { Component, Suspense, createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  BufferAttribute,
  BufferGeometry,
  Color,
  DataTexture,
  DoubleSide,
  FrontSide,
  LinearFilter,
  MeshStandardMaterial,
  RGBAFormat,
  SRGBColorSpace,
  Vector3,
  type Mesh,
  type Object3D,
  type PerspectiveCamera,
} from 'three';
import { CSM } from 'three/examples/jsm/csm/CSM.js';
import { rampColor } from '@/components/charts/kit';
import type { V3 } from '@/lib/geometry/frames';
import { bounds, centroid, pointInPolygon } from '@/lib/geometry/polygon';
import { onSessionEvent, useSession } from '@/lib/store/session';
import { createPortalDepthMaterial, createPortalUniforms, passesLight, setPortals, type PortalUniforms } from '@/lib/sunlab/portals';
import { floorGeometry, wallGeometry } from '@/lib/three/roomShell';

const DEG = Math.PI / 180;
const smoothstep = (a: number, b: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

export interface SunState {
  /** Unit vector toward the sun, interior frame. */
  direction: V3;
  elevation_deg: number;
}

export interface PlacedItem {
  item_id: string;
  url: string;
  position: Vec3;
  yaw_deg: number;
}

export type SunLabView = { kind: 'overview'; seq: number } | { kind: 'room'; room: Room; seq: number };

export interface SceneProps {
  twin: PropertyTwin;
  openings: Opening[];
  sun: SunState;
  placements: PlacedItem[];
  heatmap: { room: Room; data: SunHeatmap } | null;
  showOpenings: boolean;
  /** Set while annotating: receives each wall click. */
  onWallClick: ((point: V3, normal: V3) => void) | null;
  /** First corner of the opening being drawn. */
  pendingCorner: V3 | null;
  view: SunLabView;
  onAssetError(message: string): void;
  onReady(): void;
}

// ---------------------------------------------------------------------------------------------
// Sun light: cascaded shadow maps that follow the camera
// ---------------------------------------------------------------------------------------------

const CsmContext = createContext<CSM | null>(null);

const NIGHT = new Color('#0a0e18');
const DUSK = new Color('#d98a5a');
const DAY = new Color('#a9c9e6');
const SUN_LOW = new Color('#ffb070');
const SUN_HIGH = new Color('#fff3e0');

function SunRig({ sun, children }: { sun: SunState; children: ReactNode }) {
  const camera = useThree((s) => s.camera) as PerspectiveCamera;
  const scene = useThree((s) => s.scene);
  const size = useThree((s) => s.size);
  const sunRef = useRef(sun);
  sunRef.current = sun;

  // Created in an effect, not in useMemo: the CSM adds its lights to the scene, and a render
  // that React throws away (StrictMode) would leave a second set of lights behind.
  const [csm, setCsm] = useState<CSM | null>(null);
  useEffect(() => {
    const c = new CSM({
      camera,
      parent: scene,
      cascades: 3,
      maxFar: 160,
      mode: 'practical',
      shadowMapSize: 2048,
      shadowBias: -0.00025,
      lightIntensity: 1,
      lightNear: 1,
      lightFar: 900,
      // Casters far outside the view (neighbors, trees) still reach the house.
      lightMargin: 300,
    });
    c.fade = true;
    for (const light of c.lights) light.shadow.normalBias = 0.03;
    setCsm(c);
    return () => {
      c.remove();
      c.dispose();
      setCsm(null);
    };
  }, [camera, scene]);
  useEffect(() => csm?.updateFrustums(), [csm, size.width, size.height]);

  const background = useMemo(() => new Color(), []);
  const sunColor = useMemo(() => new Color(), []);
  useEffect(() => {
    scene.background = background;
    return () => {
      scene.background = null;
    };
  }, [scene, background]);

  useFrame(() => {
    if (!csm) return;
    const { direction, elevation_deg: el } = sunRef.current;
    const up = smoothstep(-1, 10, el);
    // Light travels from the sun into the scene. Below the horizon it is off; keep a valid direction.
    csm.lightDirection.set(-direction[0], -Math.max(0.02, direction[1]), -direction[2]).normalize();
    sunColor.copy(SUN_LOW).lerp(SUN_HIGH, smoothstep(2, 30, el));
    for (const light of csm.lights) {
      light.intensity = 3.0 * up;
      light.color.copy(sunColor);
      light.castShadow = up > 0;
    }
    background.copy(NIGHT).lerp(DUSK, smoothstep(-8, 1, el) * (1 - smoothstep(4, 18, el)) * 0.7).lerp(DAY, smoothstep(-2, 16, el));
    csm.update();
  });

  const day = smoothstep(-6, 14, sun.elevation_deg);
  if (!csm) return null;
  return (
    <CsmContext.Provider value={csm}>
      {/* Sky fill is kept low so that direct sun and shadow read clearly. */}
      <hemisphereLight args={['#dbe9ff', '#8a7f72', 0.12 + 0.42 * day]} />
      <ambientLight intensity={0.05 + 0.05 * day} />
      {children}
    </CsmContext.Provider>
  );
}

// ---------------------------------------------------------------------------------------------
// Meshes
// ---------------------------------------------------------------------------------------------

/** Clone a loaded glTF scene with lit, shadowed materials that are registered with the CSM. */
function useLitScene(source: Object3D, csm: CSM, opts: { portalUniforms?: PortalUniforms; roughness?: number } = {}): Object3D {
  return useMemo(() => {
    const root = source.clone(true);
    const depth = opts.portalUniforms ? createPortalDepthMaterial(opts.portalUniforms) : undefined;
    // One lit material per source material: a textured scan has one material per texture atlas,
    // shared by many meshes.
    const lit = new Map<string, MeshStandardMaterial>();
    root.traverse((node) => {
      const mesh = node as Mesh;
      if (!mesh.isMesh) return;
      const src = (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material) as MeshStandardMaterial | undefined;
      let material = src && lit.get(src.uuid);
      if (!material) {
        const map = src?.map ?? null;
        material = new MeshStandardMaterial({
          // A photo texture is the surface color: any base color factor on the scan would only
          // darken it. Untextured meshes keep their own color.
          color: map ? new Color('#ffffff') : (src?.color ?? new Color('#d8d4cc')),
          map,
          // Scan textures carry the light of the day of the scan. A little of it is kept as glow,
          // so rooms that the simulated sun does not reach stay readable.
          emissive: new Color(map ? '#ffffff' : '#000000'),
          emissiveMap: map,
          emissiveIntensity: map ? 0.22 : 0,
          roughness: opts.roughness ?? src?.roughness ?? 0.9,
          metalness: 0,
          side: src?.side ?? FrontSide,
          // A scanned shell is one-sided; it must still block the sun from both sides.
          shadowSide: DoubleSide,
        });
        csm.setupMaterial(material);
        if (src) lit.set(src.uuid, material);
      }
      mesh.material = material;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      if (depth) mesh.customDepthMaterial = depth;
    });
    return root;
  }, [source, csm, opts.portalUniforms, opts.roughness]);
}

function Interior({ url, portalUniforms, onWallClick, onReady }: { url: string; portalUniforms: PortalUniforms; onWallClick: SceneProps['onWallClick']; onReady(): void }) {
  const csm = useContext(CsmContext)!;
  const gltf = useGLTF(url);
  const scene = useLitScene(gltf.scene, csm, { portalUniforms, roughness: 0.95 });
  useEffect(() => onReady(), [onReady, scene]);

  const click = (e: ThreeEvent<MouseEvent>) => {
    if (!onWallClick || e.delta > 4 || !e.face) return; // a drag is a camera move, not a click
    e.stopPropagation();
    const n = e.face.normal.clone().transformDirection(e.object.matrixWorld);
    onWallClick([e.point.x, e.point.y, e.point.z], [n.x, n.y, n.z]);
  };
  return <primitive object={scene} onClick={onWallClick ? click : undefined} />;
}

/**
 * Stand-in for a twin that has no interior mesh yet (C3: a degraded twin is still valid):
 * each room as a floor and inward-facing walls, built from the room outlines.
 */
function RoomShells({ twin, portalUniforms, onWallClick, onReady }: { twin: PropertyTwin; portalUniforms: PortalUniforms; onWallClick: SceneProps['onWallClick']; onReady(): void }) {
  const csm = useContext(CsmContext)!;
  const { floors, walls, floorMaterial, wallMaterial, depth } = useMemo(() => {
    const make = (color: string) => {
      const m = new MeshStandardMaterial({ color, roughness: 0.95, metalness: 0, side: FrontSide, shadowSide: DoubleSide });
      csm.setupMaterial(m);
      return m;
    };
    return {
      floors: twin.rooms.map((r) => ({ id: r.room_id, geometry: floorGeometry(r) })),
      walls: twin.rooms.map((r) => ({ id: r.room_id, geometry: wallGeometry(r) })),
      floorMaterial: Object.assign(make('#cdbfa9'), { side: DoubleSide }),
      wallMaterial: make('#e9e4da'),
      depth: createPortalDepthMaterial(portalUniforms),
    };
  }, [twin, csm, portalUniforms]);
  useEffect(() => onReady(), [onReady]);
  useEffect(
    () => () => {
      floors.forEach((f) => f.geometry.dispose());
      walls.forEach((w) => w.geometry.dispose());
    },
    [floors, walls],
  );
  const click = (e: ThreeEvent<MouseEvent>) => {
    if (!onWallClick || e.delta > 4 || !e.face) return;
    e.stopPropagation();
    onWallClick([e.point.x, e.point.y, e.point.z], [e.face.normal.x, e.face.normal.y, e.face.normal.z]);
  };
  return (
    <>
      {floors.map((f) => (
        <mesh key={f.id} geometry={f.geometry} material={floorMaterial} receiveShadow />
      ))}
      {walls.map((w) => (
        <mesh key={w.id} geometry={w.geometry} material={wallMaterial} castShadow receiveShadow customDepthMaterial={depth} onClick={onWallClick ? click : undefined} />
      ))}
    </>
  );
}

/** Plain ground for a twin with no surroundings mesh, so the house does not float and shadows land somewhere. */
function Ground({ twin }: { twin: PropertyTwin }) {
  const csm = useContext(CsmContext)!;
  const material = useMemo(() => {
    const m = new MeshStandardMaterial({ color: '#7d8572', roughness: 1, metalness: 0 });
    csm.setupMaterial(m);
    return m;
  }, [csm]);
  const house = useMemo(() => houseBounds(twin), [twin]);
  // Below the lowest floor, and below scanned yard geometry that sits near grade.
  const y = Math.min(0, ...twin.rooms.map((r) => r.floor_y)) - 0.45;
  return (
    <mesh position={[house.center.x, y, house.center.z]} rotation={[-Math.PI / 2, 0, 0]} material={material} receiveShadow>
      <circleGeometry args={[160, 64]} />
    </mesh>
  );
}

function Context({ url }: { url: string }) {
  const csm = useContext(CsmContext)!;
  const gltf = useGLTF(url);
  const scene = useLitScene(gltf.scene, csm);
  return <primitive object={scene} />;
}

function Furniture({ item }: { item: PlacedItem }) {
  const csm = useContext(CsmContext)!;
  const gltf = useGLTF(item.url);
  const scene = useLitScene(gltf.scene, csm);
  // A5: the stored pose, as it is. Yaw is about +Y, counter-clockwise from above (I-G12).
  return <primitive object={scene} position={[item.position.x, item.position.y, item.position.z]} rotation={[0, item.yaw_deg * DEG, 0]} />;
}

function OpeningPanes({ openings }: { openings: Opening[] }) {
  const panes = useMemo(
    () =>
      openings
        .filter((o) => o.polygon.length >= 4)
        .map((o) => {
          const geometry = new BufferGeometry();
          geometry.setAttribute('position', new BufferAttribute(new Float32Array(o.polygon.slice(0, 4).flatMap((p) => [p.x, p.y, p.z])), 3));
          geometry.setIndex([0, 1, 2, 0, 2, 3]);
          return { id: o.opening_id, geometry, light: passesLight(o) };
        }),
    [openings],
  );
  useEffect(() => () => panes.forEach((p) => p.geometry.dispose()), [panes]);
  return (
    <>
      {panes.map((p) => (
        <mesh key={p.id} geometry={p.geometry} renderOrder={2}>
          <meshBasicMaterial color={p.light ? '#8fd0ff' : '#f2b84b'} transparent opacity={p.light ? 0.38 : 0.22} side={DoubleSide} depthWrite={false} polygonOffset polygonOffsetFactor={-4} />
        </mesh>
      ))}
    </>
  );
}

function Heatmap({ room, data }: { room: Room; data: SunHeatmap }) {
  const { geometry, texture } = useMemo(() => {
    const rows = data.hours.length;
    const cols = data.hours[0]?.length ?? 0;
    const max = Math.max(0.01, ...data.hours.flat());
    const pixels = new Uint8Array(Math.max(1, rows * cols) * 4);
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const x = data.origin_xz[0] + (c + 0.5) * data.cell_m;
        const z = data.origin_xz[1] + (r + 0.5) * data.cell_m;
        const inside = pointInPolygon([x, z], room.polygon);
        const t = (data.hours[r]![c] ?? 0) / max;
        const [cr, cg, cb] = rampColor(t);
        const i = (r * cols + c) * 4;
        pixels[i] = cr;
        pixels[i + 1] = cg;
        pixels[i + 2] = cb;
        // Cells that never see the sun stay clear, so the floor shows through.
        pixels[i + 3] = inside && t > 0 ? Math.round(45 + 200 * t) : 0;
      }
    }
    const tex = new DataTexture(pixels, Math.max(1, cols), Math.max(1, rows), RGBAFormat);
    tex.colorSpace = SRGBColorSpace;
    tex.magFilter = LinearFilter;
    tex.minFilter = LinearFilter;
    tex.needsUpdate = true;

    const [x0, z0] = data.origin_xz;
    const x1 = x0 + cols * data.cell_m;
    const z1 = z0 + rows * data.cell_m;
    // A scanned floor is not flat: sit a little above it, and bias toward the camera below.
    const y = room.floor_y + 0.12;
    const geo = new BufferGeometry();
    geo.setAttribute('position', new BufferAttribute(new Float32Array([x0, y, z0, x1, y, z0, x1, y, z1, x0, y, z1]), 3));
    // Texture row r covers z0 + r * cell: v grows with +z.
    geo.setAttribute('uv', new BufferAttribute(new Float32Array([0, 0, 1, 0, 1, 1, 0, 1]), 2));
    geo.setIndex([0, 2, 1, 0, 3, 2]);
    return { geometry: geo, texture: tex };
  }, [room, data]);
  useEffect(
    () => () => {
      geometry.dispose();
      texture.dispose();
    },
    [geometry, texture],
  );
  return (
    <mesh geometry={geometry} renderOrder={1}>
      <meshBasicMaterial map={texture} transparent side={DoubleSide} depthWrite={false} toneMapped={false} polygonOffset polygonOffsetFactor={-6} polygonOffsetUnits={-60} />
    </mesh>
  );
}

// ---------------------------------------------------------------------------------------------
// Camera
// ---------------------------------------------------------------------------------------------

function houseBounds(twin: PropertyTwin): { center: Vector3; radius: number } {
  const b = bounds(twin.rooms.flatMap((r) => r.polygon));
  const ys = twin.rooms.flatMap((r) => [r.floor_y, r.floor_y + r.height_m]);
  const yMin = Math.min(0, ...ys);
  const yMax = Math.max(3, ...ys);
  if (!Number.isFinite(b.minX)) return { center: new Vector3(0, 1.5, 0), radius: 15 };
  return {
    center: new Vector3((b.minX + b.maxX) / 2, (yMin + yMax) / 2, (b.minZ + b.maxZ) / 2),
    radius: Math.hypot(b.maxX - b.minX, yMax - yMin, b.maxZ - b.minZ) / 2,
  };
}

/** Where to stand in a room: across from its main window, looking at the middle. */
function roomCamera(room: Room, openings: Opening[]): { position: Vector3; target: Vector3; reach: number } {
  const [cx, cz] = centroid(room.polygon);
  const b = bounds(room.polygon);
  let dx = b.maxX - b.minX >= b.maxZ - b.minZ ? 1 : 0;
  let dz = 1 - dx;
  const win = openings.filter((o) => o.room_id === room.room_id && passesLight(o) && o.polygon.length >= 4)[0];
  if (win) {
    const wx = win.polygon.reduce((s, p) => s + p.x, 0) / win.polygon.length - cx;
    const wz = win.polygon.reduce((s, p) => s + p.z, 0) / win.polygon.length - cz;
    const l = Math.hypot(wx, wz);
    if (l > 0.2) {
      dx = -wx / l;
      dz = -wz / l;
    }
  }
  let reach = Math.min(4, Math.max(1.1, 0.42 * Math.max(b.maxX - b.minX, b.maxZ - b.minZ)));
  while (reach > 0.6 && !pointInPolygon([cx + dx * reach, cz + dz * reach], room.polygon)) reach *= 0.85;
  return {
    position: new Vector3(cx + dx * reach, room.floor_y + 1.7, cz + dz * reach),
    target: new Vector3(cx, room.floor_y + 1.0, cz),
    reach,
  };
}

function CameraRig({ twin, openings, view }: { twin: PropertyTwin; openings: Opening[]; view: SunLabView }) {
  const controls = useRef<CameraControls>(null);
  const first = useRef(true);
  const house = useMemo(() => houseBounds(twin), [twin]);
  const openingsRef = useRef(openings);
  openingsRef.current = openings;

  useEffect(() => {
    const c = controls.current;
    if (!c) return;
    const animate = !first.current;
    first.current = false;
    if (view.kind === 'room') {
      const cam = roomCamera(view.room, openingsRef.current);
      c.minDistance = 0.4;
      c.maxDistance = Math.max(1.5, cam.reach * 1.5);
      void c.setLookAt(cam.position.x, cam.position.y, cam.position.z, cam.target.x, cam.target.y, cam.target.z, animate);
    } else {
      const r = house.radius;
      c.minDistance = 3;
      c.maxDistance = 260;
      void c.setLookAt(house.center.x - r * 0.9, house.center.y + r * 1.25, house.center.z + r * 1.15, house.center.x, house.center.y, house.center.z, animate);
    }
  }, [view, house]);

  // viewer.look from the phone joystick / gyroscope.
  useEffect(
    () =>
      onSessionEvent((event) => {
        if (event.type === 'viewer.look' && useSession.getState().viewer.mode === 'sun') {
          void controls.current?.rotate(-event.payload.yaw_delta_deg * DEG, -event.payload.pitch_delta_deg * DEG, true);
        }
      }),
    [],
  );

  return <CameraControls ref={controls} makeDefault smoothTime={0.45} maxPolarAngle={Math.PI * 0.53} />;
}

// ---------------------------------------------------------------------------------------------
// Scene
// ---------------------------------------------------------------------------------------------

class AssetBoundary extends Component<{ label: string; onError(message: string): void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error) {
    this.props.onError(`${this.props.label} could not be loaded: ${error.message}`);
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

export function Scene({ twin, openings, sun, placements, heatmap, showOpenings, onWallClick, pendingCorner, view, onAssetError, onReady }: SceneProps) {
  const portalUniforms = useMemo(createPortalUniforms, []);
  useEffect(() => {
    setPortals(portalUniforms, openings);
  }, [portalUniforms, openings]);

  return (
    <SunRig sun={sun}>
      <CameraRig twin={twin} openings={openings} view={view} />
      {twin.assets.interior_glb ? (
        <AssetBoundary label="The interior mesh" onError={onAssetError}>
          <Suspense fallback={null}>
            <Interior url={twin.assets.interior_glb} portalUniforms={portalUniforms} onWallClick={onWallClick} onReady={onReady} />
          </Suspense>
        </AssetBoundary>
      ) : (
        <RoomShells twin={twin} portalUniforms={portalUniforms} onWallClick={onWallClick} onReady={onReady} />
      )}
      {!twin.assets.context_glb && <Ground twin={twin} />}
      {twin.assets.context_glb && (
        <AssetBoundary label="The surroundings mesh" onError={onAssetError}>
          <Suspense fallback={null}>
            <Context url={twin.assets.context_glb} />
          </Suspense>
        </AssetBoundary>
      )}
      {placements.map((item) => (
        <AssetBoundary key={item.item_id} label="A furniture model" onError={onAssetError}>
          <Suspense fallback={null}>
            <Furniture item={item} />
          </Suspense>
        </AssetBoundary>
      ))}
      {showOpenings && <OpeningPanes openings={openings} />}
      {heatmap && <Heatmap room={heatmap.room} data={heatmap.data} />}
      {pendingCorner && (
        <mesh position={pendingCorner as unknown as [number, number, number]}>
          <sphereGeometry args={[0.06, 16, 16]} />
          <meshBasicMaterial color="#f2b84b" depthTest={false} />
        </mesh>
      )}
    </SunRig>
  );
}
