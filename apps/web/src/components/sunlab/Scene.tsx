'use client';

import type { Opening, PropertyTwin, Room, SunHeatmap, Vec3 } from '@ithaca/schema';
import { CameraControls, useGLTF } from '@react-three/drei';
import { useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { Component, Suspense, createContext, useContext, useEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from 'react';
import {
  Box3,
  BufferAttribute,
  BufferGeometry,
  Color,
  DataTexture,
  DoubleSide,
  FrontSide,
  LinearFilter,
  MeshBasicMaterial,
  RGBAFormat,
  SRGBColorSpace,
  Vector3,
  type Camera,
  type IUniform,
  type Material,
  type Mesh,
  type MeshStandardMaterial,
  type Object3D,
  type Scene as ThreeScene,
  type WebGLRenderer,
} from 'three';
import { rampColor } from '@/components/charts/kit';
import type { V3 } from '@/lib/geometry/frames';
import { bounds, centroid, pointInPolygon } from '@/lib/geometry/polygon';
import { onHandNav } from '@/lib/hands/engine';
import { House, plainLight, shadePlain, type HouseInfo } from '@/lib/house/house';
import { SunLight, type SunOpening } from '@/lib/house/sun';
import { onSessionEvent, useSession } from '@/lib/store/session';
import { floorGeometry, wallGeometry } from '@/lib/three/roomShell';

/**
 * The Sun Lab's picture: the house rebuilt from the floor plan, with its baked light, under the
 * sun of the chosen moment (lib/house, ported from 3d_rendering_matterport). The sun is one
 * shadow map of the house, its lot and the block around it; sunlight a sunlit room throws back
 * comes from the bake. Furniture is lit the same way, and casts its own shadow.
 *
 * A twin without a rebuilt house shows its rooms drawn from their outlines, in the same light.
 */

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
  /** The rebuilt house (its house.json, read), or null to draw the rooms from their outlines. */
  house: { url: string; info: HouseInfo } | null;
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
// Light: the sun's shadow map, and the light on things without a lightmap
// ---------------------------------------------------------------------------------------------

interface Lighting {
  sun: SunLight;
  /** Around furniture and room outlines: a room's usual light. */
  indoor: IUniform<Color>;
  /** On plain open ground. */
  outdoor: IUniform<Color>;
  house: MutableRefObject<House | null>;
  /** Something that casts a shadow appeared, moved or went: redraw the shadow map. */
  invalidate(): void;
}

const LightContext = createContext<Lighting | null>(null);

const NIGHT = new Color('#0a0e18');
const DUSK = new Color('#d98a5a');
const DAY = new Color('#a9c9e6');

/**
 * Draw only what casts a shadow (meshes marked `userData.caster`), with the shadow map's
 * material. The house's ceilings and roof cast shadows whether or not they are drawn.
 */
function renderOccluders(gl: WebGLRenderer, scene: ThreeScene, material: Material, camera: Camera, house: House | null): void {
  const hidden: Object3D[] = [];
  scene.traverse((o) => {
    const drawn = (o as Mesh).isMesh || (o as { isPoints?: boolean }).isPoints || (o as { isLine?: boolean }).isLine || (o as { isSprite?: boolean }).isSprite;
    if (drawn && o.visible && !o.userData.caster) hidden.push(o);
  });
  const lid = house?.lidOn ?? true;
  house?.setLid(true);
  for (const o of hidden) o.visible = false;
  const background = scene.background;
  scene.background = null;
  scene.overrideMaterial = material;
  gl.render(scene, camera);
  scene.overrideMaterial = null;
  scene.background = background;
  for (const o of hidden) o.visible = true;
  house?.setLid(lid);
}

/** The windows and glass doors of a twin, for a shadow map of walls that have no holes. */
function sunOpenings(openings: Opening[]): SunOpening[] {
  return openings.flatMap((o) => {
    if (!passesLight(o) || o.polygon.length < 4) return [];
    const [p0, p1] = o.polygon as [Vec3, Vec3];
    const along = new Vector3(p1.x - p0.x, 0, p1.z - p0.z);
    const width = along.length();
    if (width < 0.05) return [];
    const ys = o.polygon.map((p) => p.y);
    const center = o.polygon.reduce<Vector3>((acc, p) => acc.add(new Vector3(p.x, p.y, p.z)), new Vector3()).multiplyScalar(1 / o.polygon.length);
    return [{ center, along: along.normalize(), half: width / 2, bottom: Math.min(...ys), top: Math.max(...ys) }];
  });
}

function LightRig({ twin, openings, sun, cutOpenings, children }: { twin: PropertyTwin; openings: Opening[]; sun: SunState; cutOpenings: boolean; children: ReactNode }) {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const houseRef = useRef<House | null>(null);
  const dirty = useRef(true);
  const sunRef = useRef(sun);
  sunRef.current = sun;
  // Without a rebuilt house: the shadow map covers the rooms' outlines and a margin.
  const outline = useMemo(() => {
    const h = houseBounds(twin);
    return new Box3().setFromCenterAndSize(h.center, new Vector3(h.radius * 2 + 6, h.radius * 2 + 2, h.radius * 2 + 6));
  }, [twin]);
  const outlineRef = useRef(outline);
  outlineRef.current = outline;

  // Made in an effect, not in useMemo: a render React throws away would leave a render target behind.
  const [lighting, setLighting] = useState<Lighting | null>(null);
  useEffect(() => {
    const light = new SunLight(gl, {
      renderOccluders: (material, camera) => renderOccluders(gl, scene, material, camera, houseRef.current),
      bounds: () => houseRef.current?.bounds ?? outlineRef.current,
    });
    setLighting({ sun: light, indoor: { value: new Color(1, 1, 1) }, outdoor: { value: new Color(1, 1, 1) }, house: houseRef, invalidate: () => void (dirty.current = true) });
    return () => {
      light.dispose();
      setLighting(null);
    };
  }, [gl, scene]);

  useEffect(() => {
    if (!lighting) return;
    lighting.sun.cutOpenings = cutOpenings;
    lighting.sun.setOpenings(sunOpenings(openings));
    dirty.current = true;
  }, [lighting, openings, cutOpenings]);

  const background = useMemo(() => new Color(), []);
  useEffect(() => {
    scene.background = background;
    return () => {
      scene.background = null;
    };
  }, [scene, background]);

  // The sun is worked out again (and its shadow map redrawn) only when it moved or a caster changed.
  const last = useRef('');
  useFrame(() => {
    if (!lighting) return;
    const { direction, elevation_deg: el } = sunRef.current;
    const key = `${direction.join(',')}|${el}`;
    if (!dirty.current && key === last.current) return;
    dirty.current = false;
    last.current = key;
    const toSun = new Vector3(direction[0], direction[1], direction[2]);
    lighting.sun.update(toSun, el);
    houseRef.current?.setLight(lighting.sun.sky, toSun, lighting.sun.uniforms.sunColor!.value as Color);
    plainLight(lighting.sun.sky, true, true, lighting.indoor.value);
    plainLight(lighting.sun.sky, true, false, lighting.outdoor.value);
    // Seen only where the house's own sky is not there (a twin without a rebuilt house).
    background.copy(NIGHT).lerp(DUSK, smoothstep(-8, 1, el) * (1 - smoothstep(4, 18, el)) * 0.7).lerp(DAY, smoothstep(-2, 16, el));
  });

  if (!lighting) return null;
  return <LightContext.Provider value={lighting}>{children}</LightContext.Provider>;
}

// ---------------------------------------------------------------------------------------------
// The house, furniture, and the stand-ins for a twin without a rebuilt house
// ---------------------------------------------------------------------------------------------

/** Whether an object and everything above it is shown. */
function shown(o: Object3D | null): boolean {
  for (; o; o = o.parent) if (!o.visible) return false;
  return true;
}

/** A click on a wall, for annotating openings: the first surface that is drawn, not one hidden (the roof, from outside). */
function wallClick(onWallClick: NonNullable<SceneProps['onWallClick']>) {
  return (e: ThreeEvent<MouseEvent>) => {
    if (e.delta > 4) return; // a drag is a camera move, not a click
    const hit = e.intersections.find((i) => i.face && shown(i.object));
    if (!hit?.face) return;
    e.stopPropagation();
    const n = hit.face.normal.clone().transformDirection(hit.object.matrixWorld);
    onWallClick([hit.point.x, hit.point.y, hit.point.z], [n.x, n.y, n.z]);
  };
}

function HouseModel({ url, info, inside, onWallClick, onAssetError, onReady }: { url: string; info: HouseInfo; inside: boolean; onWallClick: SceneProps['onWallClick']; onAssetError(message: string): void; onReady(): void }) {
  const lighting = useContext(LightContext)!;
  const gl = useThree((s) => s.gl);
  const [house, setHouse] = useState<House | null>(null);
  const errorRef = useRef(onAssetError);
  errorRef.current = onAssetError;

  useEffect(() => {
    let alive = true;
    let made: House | null = null;
    House.load(gl, url, info, lighting.sun.uniforms, lighting.invalidate).then(
      (h) => {
        if (!alive) return h.dispose();
        made = h;
        lighting.house.current = h;
        lighting.invalidate();
        setHouse(h);
      },
      (err) => alive && errorRef.current(`The rebuilt house could not be loaded: ${err instanceof Error ? err.message : String(err)}`),
    );
    return () => {
      alive = false;
      if (made) {
        if (lighting.house.current === made) lighting.house.current = null;
        made.dispose();
        lighting.invalidate();
      }
      setHouse(null);
    };
  }, [gl, url, info, lighting]);

  // From outside the house is seen without its ceilings and roof.
  useEffect(() => house?.setLid(inside), [house, inside]);
  useEffect(() => {
    if (house) onReady();
  }, [house, onReady]);
  useFrame(({ camera }) => house?.update(camera));

  if (!house) return null;
  return <primitive object={house.group} onClick={onWallClick ? wallClick(onWallClick) : undefined} />;
}

/**
 * Stand-in for a twin that has no rebuilt house (C3: a degraded twin is still valid): each room
 * as a floor and inward-facing walls, built from the room outlines. The walls have no holes, so
 * the sun's shadow map lets light through where the twin's openings are.
 */
function RoomShells({ twin, onWallClick, onReady }: { twin: PropertyTwin; onWallClick: SceneProps['onWallClick']; onReady(): void }) {
  const lighting = useContext(LightContext)!;
  const { floors, walls, floorMaterial, wallMaterial } = useMemo(() => {
    const make = (color: string, side: typeof FrontSide | typeof DoubleSide) => {
      const m = new MeshBasicMaterial({ color, side });
      shadePlain(m, lighting.indoor, lighting.sun.uniforms);
      return m;
    };
    return {
      floors: twin.rooms.map((r) => ({ id: r.room_id, geometry: floorGeometry(r) })),
      walls: twin.rooms.map((r) => ({ id: r.room_id, geometry: wallGeometry(r) })),
      floorMaterial: make('#cdbfa9', DoubleSide),
      wallMaterial: make('#e9e4da', FrontSide),
    };
  }, [twin, lighting]);
  useEffect(() => {
    lighting.invalidate();
    onReady();
  }, [lighting, onReady, floors]);
  useEffect(
    () => () => {
      floors.forEach((f) => f.geometry.dispose());
      walls.forEach((w) => w.geometry.dispose());
      floorMaterial.dispose();
      wallMaterial.dispose();
    },
    [floors, walls, floorMaterial, wallMaterial],
  );
  const click = onWallClick ? wallClick(onWallClick) : undefined;
  return (
    <>
      {floors.map((f) => (
        <mesh key={f.id} geometry={f.geometry} material={floorMaterial} userData={{ caster: true }} />
      ))}
      {walls.map((w) => (
        <mesh key={w.id} geometry={w.geometry} material={wallMaterial} userData={{ caster: true }} onClick={click} />
      ))}
    </>
  );
}

/** Plain ground for a twin without a rebuilt house, so the rooms do not float and shadows land somewhere. */
function Ground({ twin }: { twin: PropertyTwin }) {
  const lighting = useContext(LightContext)!;
  const material = useMemo(() => {
    const m = new MeshBasicMaterial({ color: '#7d8572' });
    shadePlain(m, lighting.outdoor, lighting.sun.uniforms);
    return m;
  }, [lighting]);
  useEffect(() => () => material.dispose(), [material]);
  const house = useMemo(() => houseBounds(twin), [twin]);
  const y = Math.min(0, ...twin.rooms.map((r) => r.floor_y)) - 0.45;
  return (
    <mesh position={[house.center.x, y, house.center.z]} rotation={[-Math.PI / 2, 0, 0]} material={material} userData={{ caster: true }}>
      <circleGeometry args={[160, 64]} />
    </mesh>
  );
}

function Furniture({ item }: { item: PlacedItem }) {
  const lighting = useContext(LightContext)!;
  const gltf = useGLTF(item.url);
  const { scene, materials } = useMemo(() => {
    const root = gltf.scene.clone(true);
    // One material per source material, lit like the rooms around it.
    const made = new Map<string, MeshBasicMaterial>();
    root.traverse((node) => {
      const mesh = node as Mesh;
      if (!mesh.isMesh) return;
      const src = (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material) as MeshStandardMaterial | undefined;
      let material = src && made.get(src.uuid);
      if (!material) {
        const map = src?.map ?? null;
        material = new MeshBasicMaterial({
          // A photo texture is the surface color; untextured meshes keep their own color.
          color: map ? new Color('#ffffff') : (src?.color ?? new Color('#d8d4cc')),
          map,
          side: src?.side ?? FrontSide,
          transparent: src?.transparent ?? false,
          opacity: src?.opacity ?? 1,
          alphaTest: src?.alphaTest ?? 0,
        });
        shadePlain(material, lighting.indoor, lighting.sun.uniforms);
        if (src) made.set(src.uuid, material);
      }
      if (!mesh.geometry.getAttribute('normal')) {
        mesh.geometry = mesh.geometry.clone();
        mesh.geometry.computeVertexNormals();
      }
      mesh.material = material;
      mesh.userData = { ...mesh.userData, caster: true };
    });
    return { scene: root, materials: [...made.values()] };
  }, [gltf.scene, lighting]);
  useEffect(() => () => materials.forEach((m) => m.dispose()), [materials]);
  // A piece that appears, moves or goes changes the shadows.
  const { x, y, z } = item.position;
  useEffect(() => {
    lighting.invalidate();
    return () => lighting.invalidate();
  }, [lighting, scene, x, y, z, item.yaw_deg]);
  // A5: the stored pose, as it is. Yaw is about +Y, counter-clockwise from above (I-G12).
  return <primitive object={scene} position={[x, y, z]} rotation={[0, item.yaw_deg * DEG, 0]} />;
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
        <mesh key={p.id} geometry={p.geometry} renderOrder={3}>
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
    // Just above the floor (the rebuilt floor and the twin's floor height differ by a few cm).
    const y = room.floor_y + 0.05;
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
      <meshBasicMaterial map={texture} transparent side={DoubleSide} depthWrite={false} polygonOffset polygonOffsetFactor={-6} polygonOffsetUnits={-60} />
    </mesh>
  );
}

// ---------------------------------------------------------------------------------------------
// Camera
// ---------------------------------------------------------------------------------------------

/** Windows and sliders pass light. A door passes light only if it is glazed. */
export function passesLight(o: Opening): boolean {
  return o.kind === 'window' || o.kind === 'slider' || o.glazing !== 'none';
}

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

const centerOf = (o: Opening) => o.polygon.reduce<Vector3>((acc, p) => acc.add(new Vector3(p.x, p.y, p.z)), new Vector3()).multiplyScalar(1 / o.polygon.length);
const openingArea = (o: Opening) => {
  const [p0, p1, , p3] = o.polygon;
  return p0 && p1 && p3 ? Math.hypot(p1.x - p0.x, p1.z - p0.z) * Math.abs(p3.y - p0.y) : 0;
};

/**
 * Where to stand in a room, and where to look.
 *
 * Eye: a Matterport scan position in the room. Those are known to be open floor at eye height,
 * which a point computed from the outline is not (columns, L-shapes). Of the scan positions that
 * see the room's largest window across open floor, the one nearest 5 m from it: the view takes in
 * the room and the floor in front of the window, where the sun lands. A room without windows
 * uses the scan position nearest its middle and looks at the middle.
 */
function roomCamera(room: Room, openings: Opening[], sweeps: PropertyTwin['sweeps']): { position: Vector3; target: Vector3; reach: number } {
  const [cx, cz] = centroid(room.polygon);
  const middle = new Vector3(cx, room.floor_y + 0.9, cz);
  const windows = openings.filter((o) => o.room_id === room.room_id && passesLight(o) && o.polygon.length >= 4).sort((a, b) => openingArea(b) - openingArea(a));
  const win = windows[0] && centerOf(windows[0]);
  const inRoom = sweeps.filter((s) => s.room_id === room.room_id);

  // Open floor all the way: sample the line, stopping short of the wall the window is in.
  const seesAcrossFloor = (x: number, z: number, tx: number, tz: number) => {
    for (let t = 0.05; t <= 0.9; t += 0.05) if (!pointInPolygon([x + (tx - x) * t, z + (tz - z) * t], room.polygon)) return false;
    return true;
  };
  const byDistance = (x: number, z: number) => (a: (typeof inRoom)[number], b: (typeof inRoom)[number]) => Math.hypot(a.position.x - x, a.position.z - z) - Math.hypot(b.position.x - x, b.position.z - z);
  // About 5 m back: far enough to take in the room, near enough that a big room's far end
  // (behind stairs or furniture) is not chosen.
  const BACK_M = 5;
  const back = (x: number, z: number) => Math.abs(Math.hypot(x - win!.x, z - win!.z) - BACK_M);
  const spot = win
    ? inRoom.filter((s) => seesAcrossFloor(s.position.x, s.position.z, win.x, win.z)).sort((a, b) => back(a.position.x, a.position.z) - back(b.position.x, b.position.z))[0]
    : undefined;
  const chosen = spot ?? [...inRoom].sort(byDistance(cx, cz))[0];
  const eye = chosen ? new Vector3(chosen.position.x, room.floor_y + 1.6, chosen.position.z) : null;

  let target = middle;
  if (win && (!eye || Math.hypot(win.x - eye.x, win.z - eye.z) > 1.2)) target = new Vector3(win.x, room.floor_y + 0.9, win.z);

  if (eye) {
    // Too close to the target to see anything: look across the room instead.
    if (Math.hypot(target.x - eye.x, target.z - eye.z) < 1) target = middle;
    return { position: eye, target, reach: Math.max(1.5, eye.distanceTo(target)) };
  }

  // No scan position in this room: stand back from the middle, away from the window.
  const b = bounds(room.polygon);
  let dx = 1;
  let dz = 0;
  if (win) {
    const l = Math.hypot(win.x - cx, win.z - cz);
    if (l > 0.2) {
      dx = -(win.x - cx) / l;
      dz = -(win.z - cz) / l;
    }
  }
  let reach = Math.min(4, Math.max(1.1, 0.42 * Math.max(b.maxX - b.minX, b.maxZ - b.minZ)));
  while (reach > 0.6 && !pointInPolygon([cx + dx * reach, cz + dz * reach], room.polygon)) reach *= 0.85;
  return { position: new Vector3(cx + dx * reach, room.floor_y + 1.6, cz + dz * reach), target, reach };
}

/** Radians a hand drag across the whole view orbits the camera. */
const HAND_ORBIT_PER_VIEW = 200 * DEG;

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
    c.maxPolarAngle = Math.PI * 0.53;
    if (view.kind === 'room') {
      const cam = roomCamera(view.room, openingsRef.current, twin.sweeps);
      c.minDistance = 0.4;
      c.maxDistance = Math.max(1.5, cam.reach * 1.5);
      void c.setLookAt(cam.position.x, cam.position.y, cam.position.z, cam.target.x, cam.target.y, cam.target.z, animate);
    } else {
      const r = house.radius;
      c.minDistance = 3;
      c.maxDistance = 260;
      void c.setLookAt(house.center.x - r * 0.9, house.center.y + r * 1.25, house.center.z + r * 1.15, house.center.x, house.center.y, house.center.z, animate);
    }
  }, [view, house, twin.sweeps]);

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

  // Hand control: a pinch-drag orbits as a mouse drag does, both hands pinching dolly in and out.
  // The engine sends small steps every display frame, so they are applied at once, not eased.
  useEffect(
    () =>
      onHandNav((nav) => {
        const c = controls.current;
        if (!c || useSession.getState().viewer.mode !== 'sun') return;
        if (nav.type === 'drag') void c.rotate(-nav.du * HAND_ORBIT_PER_VIEW, -nav.dv * HAND_ORBIT_PER_VIEW * 0.6, false);
        else if (nav.type === 'zoom') void c.dollyTo(c.distance / nav.factor, false);
        // open-hand turning and stepping belong to the tour
      }),
    [],
  );

  return <CameraControls ref={controls} makeDefault smoothTime={0.45} />;
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

export function Scene({ twin, house, openings, sun, placements, heatmap, showOpenings, onWallClick, pendingCorner, view, onAssetError, onReady }: SceneProps) {
  return (
    <LightRig twin={twin} openings={openings} sun={sun} cutOpenings={!house}>
      <CameraRig twin={twin} openings={openings} view={view} />
      {house ? (
        <HouseModel url={house.url} info={house.info} inside={view.kind === 'room'} onWallClick={onWallClick} onAssetError={onAssetError} onReady={onReady} />
      ) : (
        <>
          <RoomShells twin={twin} onWallClick={onWallClick} onReady={onReady} />
          <Ground twin={twin} />
        </>
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
        <mesh position={pendingCorner as unknown as [number, number, number]} renderOrder={4}>
          <sphereGeometry args={[0.06, 16, 16]} />
          <meshBasicMaterial color="#f2b84b" depthTest={false} />
        </mesh>
      )}
    </LightRig>
  );
}
