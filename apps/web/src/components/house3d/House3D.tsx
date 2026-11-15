'use client';

import type { FloorOpening, Opening, PropertyTwin, Room, Vec3 } from '@ithaca/schema';
import { useGLTF } from '@react-three/drei';
import { useFrame, useThree, type ThreeEvent } from '@react-three/fiber';
import { Component, createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type MutableRefObject, type ReactNode } from 'react';
import { Box3, BufferAttribute, BufferGeometry, Color, DoubleSide, FrontSide, Group, Mesh as ThreeMesh, MeshBasicMaterial, Vector3, type Camera, type IUniform, type Material, type Mesh, type MeshStandardMaterial, type Object3D, type Scene as ThreeScene, type WebGLRenderer } from 'three';
import { compassToInterior, type V3 } from '@/lib/geometry/frames';
import { bounds, pointInPolygon } from '@/lib/geometry/polygon';
import { House, loadHouseInfo, plainLamps, plainLight, shadePlain, type HouseInfo } from '@/lib/house/house';
import { SunLight, sunToward, type SunOpening } from '@/lib/house/sun';
import { sunPosition } from '@/lib/sun/position';
import { zonedParts } from '@/lib/time';
import { floorGeometry, wallGeometry } from '@/lib/three/roomShell';

/**
 * The house as every 3D view draws it (the Tour, layout pictures): the house rebuilt
 * from the floor plan with its baked light (lib/house, ported from 3d_rendering_matterport), the
 * sun of the moment with one shadow map over the house, its lot and the block around it, and
 * furniture lit the same way. A twin without a rebuilt house shows its rooms drawn from their
 * outlines, in the same light.
 *
 * Inside an R3F <Canvas flat>: the light is worked out in the house's own shaders, with no tone
 * mapping on top.
 */

const DEG = Math.PI / 180;
const smoothstep = (a: number, b: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - a) / (b - a)));
  return t * t * (3 - 2 * t);
};

export interface SunState {
  azimuth_deg: number;
  elevation_deg: number;
  /** Unit vector toward the sun, interior frame. */
  direction: V3;
  /** The house's local time of day, in hours (0 to 24). */
  hour: number;
}

export interface PlacedItem {
  item_id: string;
  url: string;
  position: Vec3;
  yaw_deg: number;
  /** Times the model's own size (InventoryItem.scale); 1 if unset. */
  scale?: number;
}

export type RebuiltHouse = { url: string; info: HouseInfo };

// ---------------------------------------------------------------------------------------------
// Which house, and where the sun is
// ---------------------------------------------------------------------------------------------

/**
 * The twin's rebuilt house (its house.json read; it says which way north is). `known` turns true
 * once it is known what to draw: the house, or (without one, or if it fails) the rooms' outlines.
 */
export function useRebuiltHouse(twin: PropertyTwin, onError?: (message: string) => void): { house: RebuiltHouse | null; known: boolean; failed: boolean } {
  const url = twin.assets.house ?? null;
  const [house, setHouse] = useState<RebuiltHouse | null>(null);
  const [failed, setFailed] = useState(false);
  const errorRef = useRef(onError);
  errorRef.current = onError;
  useEffect(() => {
    setHouse(null);
    setFailed(false);
    if (!url) return;
    let alive = true;
    loadHouseInfo(url).then(
      (info) => alive && setHouse({ url, info }),
      (err) => {
        if (!alive) return;
        setFailed(true);
        errorRef.current?.(`The rebuilt house could not be loaded: ${err instanceof Error ? err.message : String(err)}`);
      },
    );
    return () => {
      alive = false;
    };
  }, [url]);
  return { house, known: !url || !!house || failed, failed: !url || failed };
}

/**
 * The sun at an instant: suncalc's position (A3), turned into the interior frame. North is the one
 * measured for the rebuilt house on Vexcel's aerial survey when there is one (Matterport's
 * georeference, which the twin's transform comes from, is several degrees off); else the twin's.
 */
export function sunAt(twin: PropertyTwin, house: RebuiltHouse | null, instant: Date): SunState {
  const pos = sunPosition(instant, twin.lat, twin.lon);
  const north = house?.info.orientation?.north;
  const direction = north ? sunToward(pos.azimuth_deg, pos.elevation_deg, north) : compassToInterior(twin.interior_to_world, pos.azimuth_deg, pos.elevation_deg);
  const local = zonedParts(instant, twin.timezone);
  return { ...pos, direction: direction as V3, hour: local.hour + local.minute / 60 };
}

export function useSun(twin: PropertyTwin, house: RebuiltHouse | null, datetime: string): SunState {
  return useMemo(() => sunAt(twin, house, new Date(datetime)), [twin, house, datetime]);
}

// ---------------------------------------------------------------------------------------------
// Light: the sun's shadow map, and the light on things without a lightmap
// ---------------------------------------------------------------------------------------------

interface Lighting {
  sun: SunLight;
  /** Around furniture and room outlines: a room's usual daylight. */
  indoor: IUniform<Color>;
  /** Around furniture and room outlines: the lamps, and the glow of a room the sun shines into. */
  indoorWarm: IUniform<Color>;
  /** On plain open ground. */
  outdoor: IUniform<Color>;
  house: MutableRefObject<House | null>;
  /** Something that casts a shadow appeared, moved or went: redraw the shadow map. */
  invalidate(): void;
}

const LightContext = createContext<Lighting | null>(null);

/** The light the house is drawn in; for a view that moves furniture itself (its shadows must follow). */
export function useHouseLighting(): Lighting {
  const lighting = useContext(LightContext);
  if (!lighting) throw new Error('useHouseLighting outside <HouseLight>');
  return lighting;
}

const BLACK = new Color(0, 0, 0);

/** The rooms' lights, by view (HouseLight's `lamps`). */
export type LampsMode = 'work' | 'sun';
/** How long the rooms' lights take to come on or go down when the view changes (s). */
const LAMPS_EASE_S = 0.4;
/** The rooms' lights are switched on from 8 am to 5 pm; outside those hours only daylight (or the night) lights the house. */
const LAMPS_ON_HOURS = [8, 17] as const;
const lampsSwitchedOn = (hour: number) => (hour >= LAMPS_ON_HOURS[0] && hour < LAMPS_ON_HOURS[1] ? 1 : 0);
const NIGHT = new Color('#0a0e18');
const DUSK = new Color('#d98a5a');
const DAY = new Color('#a9c9e6');

/**
 * Draw only what casts a shadow (meshes marked `userData.caster`), with the shadow map's
 * material. The house's ceilings, roof and upper storeys cast shadows whether or not they are drawn.
 */
function renderOccluders(gl: WebGLRenderer, scene: ThreeScene, material: Material, camera: Camera, house: House | null, seal: Object3D | null): void {
  const hidden: Object3D[] = [];
  house?.setEverything(true);
  scene.traverse((o) => {
    const drawn = (o as Mesh).isMesh || (o as { isPoints?: boolean }).isPoints || (o as { isLine?: boolean }).isLine || (o as { isSprite?: boolean }).isSprite;
    if (drawn && o.visible && !o.userData.caster) hidden.push(o);
  });
  for (const o of hidden) o.visible = false;
  // The rebuilt house's walls, ceilings and roof are separate sheets that do not quite meet:
  // the sun would get in along a wall's top. A lid over each room, a little wider than it, closes
  // that for the sun only (it is never drawn on screen).
  if (seal && house) seal.visible = true;
  const background = scene.background;
  scene.background = null;
  scene.overrideMaterial = material;
  gl.render(scene, camera);
  scene.overrideMaterial = null;
  scene.background = background;
  for (const o of hidden) o.visible = true;
  if (seal) seal.visible = false;
  house?.setEverything(false);
}

/** How far past a room's outline its lid for the sun reaches (m): about a wall's thickness, no eave. */
const SEAL_OVERHANG_M = 0.15;
/** The lid is built of square cells this wide (m), so openings of any shape can be left out of it. */
const SEAL_CELL_M = 0.1;

/** A room's outline pushed out by `d` metres (mitred corners, a long miter cut short). */
function grow(polygon: Room['polygon'], d: number): Room['polygon'] {
  const n = polygon.length;
  let area = 0;
  for (let i = 0; i < n; i++) {
    const [x0, z0] = polygon[i]!;
    const [x1, z1] = polygon[(i + 1) % n]!;
    area += x0 * z1 - x1 * z0;
  }
  const out = area >= 0 ? 1 : -1;
  const normal = (a: readonly [number, number], b: readonly [number, number]) => {
    const dx = b[0] - a[0];
    const dz = b[1] - a[1];
    const len = Math.hypot(dx, dz) || 1;
    return [(dz / len) * out, (-dx / len) * out] as const;
  };
  return polygon.map((p, i) => {
    const n1 = normal(polygon[(i + n - 1) % n]!, p);
    const n2 = normal(p, polygon[(i + 1) % n]!);
    let mx = n1[0] + n2[0];
    let mz = n1[1] + n2[1];
    const ml = Math.hypot(mx, mz) || 1;
    mx /= ml;
    mz /= ml;
    const k = d / Math.max(mx * n1[0] + mz * n1[1], 0.35);
    return [p[0] + mx * k, p[1] + mz * k] as [number, number];
  }) as Room['polygon'];
}

/**
 * The lids over the rooms, for the sun's shadow map only. A lid sits at the room's ceiling, or
 * just above its highest window or glass door if one reaches higher (the sun must still get in
 * through the top of it), and leaves out the holes between storeys (a stairwell, a double-height
 * space), through which the sun comes down from the upper windows.
 */
function makeSeal(rooms: Room[], openings: Opening[], floorOpenings: FloorOpening[]): Group {
  const group = new Group();
  const material = new MeshBasicMaterial();
  const floorOf = new Map(rooms.map((r) => [r.room_id, r.floor_y]));
  for (const room of rooms) {
    if (room.polygon.length < 3) continue;
    const glassTop = Math.max(
      -Infinity,
      ...openings.filter((o) => passesLight(o) && (o.room_id === room.room_id || o.other_room_id === room.room_id)).flatMap((o) => o.polygon.map((q) => q.y)),
    );
    const top = Math.max(room.floor_y + room.height_m, glassTop + 0.05) - 0.03;
    // Holes on this room's level: an opening belongs to the level of the rooms it opens out of.
    const holes = floorOpenings
      .filter((f) => f.room_ids.some((id) => Math.abs((floorOf.get(id) ?? Infinity) - room.floor_y) < 0.5))
      .map((f) => f.polygon);
    const outline = grow(room.polygon, SEAL_OVERHANG_M);
    const b = bounds(outline);
    const cells: number[] = [];
    const h = SEAL_CELL_M / 2;
    for (let x = b.minX + h; x < b.maxX; x += SEAL_CELL_M) {
      for (let z = b.minZ + h; z < b.maxZ; z += SEAL_CELL_M) {
        if (!pointInPolygon([x, z], outline) || holes.some((hole) => pointInPolygon([x, z], hole))) continue;
        cells.push(x - h, top, z - h, x + h, top, z - h, x + h, top, z + h, x - h, top, z - h, x + h, top, z + h, x - h, top, z + h);
      }
    }
    if (!cells.length) continue;
    const geometry = new BufferGeometry();
    geometry.setAttribute('position', new BufferAttribute(new Float32Array(cells), 3));
    geometry.computeBoundingSphere();
    const mesh = new ThreeMesh(geometry, material);
    mesh.userData.caster = true;
    mesh.frustumCulled = false;
    mesh.raycast = () => {};
    group.add(mesh);
  }
  group.visible = false;
  group.userData.caster = true;
  return group;
}

/** Windows and sliders pass light. A door passes light only if it is glazed. */
export function passesLight(o: Opening): boolean {
  return o.kind === 'window' || o.kind === 'slider' || o.glazing !== 'none';
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

export function houseBounds(twin: PropertyTwin): { center: Vector3; radius: number } {
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

/**
 * The light the house is drawn in. `openings` are cut into the shadow map when the walls have no
 * holes of their own (`cutOpenings`: rooms drawn from their outlines).
 */
export function HouseLight({
  twin,
  openings,
  sun,
  cutOpenings,
  lamps,
  children,
}: {
  twin: PropertyTwin;
  openings: Opening[];
  sun: SunState;
  cutOpenings: boolean;
  /**
   * The rooms' lights. 'work': all on, bright and even, at any hour (walking through and placing
   * furniture). 'sun': the dimmer lamps of the time of day (watching the sun move).
   */
  lamps: LampsMode;
  children: ReactNode;
}) {
  const gl = useThree((s) => s.gl);
  const scene = useThree((s) => s.scene);
  const frame = useThree((s) => s.invalidate);
  const houseRef = useRef<House | null>(null);
  const dirty = useRef(true);
  const sunRef = useRef(sun);
  sunRef.current = sun;
  // The rooms' lights (docs/specs/lighting.md, 9): eased toward the view's choice over LAMPS_EASE_S.
  const work = useRef(lamps === 'work' ? 1 : 0);
  const lampsRef = useRef(lamps);
  lampsRef.current = lamps;
  // Switched on or off by the hour, eased the same way.
  const lit = useRef(lampsSwitchedOn(sun.hour));
  // Without a rebuilt house: the shadow map covers the rooms' outlines and a margin.
  const outline = useMemo(() => {
    const h = houseBounds(twin);
    return new Box3().setFromCenterAndSize(h.center, new Vector3(h.radius * 2 + 6, h.radius * 2 + 2, h.radius * 2 + 6));
  }, [twin]);
  const outlineRef = useRef(outline);
  outlineRef.current = outline;
  const sealRef = useRef<Group | null>(null);
  useEffect(() => {
    const seal = makeSeal(twin.rooms, twin.openings, twin.floor_openings ?? []);
    scene.add(seal);
    sealRef.current = seal;
    dirty.current = true;
    return () => {
      scene.remove(seal);
      for (const m of seal.children as Mesh[]) m.geometry.dispose();
      ((seal.children[0] as Mesh | undefined)?.material as Material | undefined)?.dispose();
      sealRef.current = null;
    };
  }, [scene, twin.rooms, twin.openings, twin.floor_openings]);

  // Made in an effect, not in useMemo: a render React throws away would leave a render target behind.
  const [lighting, setLighting] = useState<Lighting | null>(null);
  useEffect(() => {
    const light = new SunLight(gl, {
      renderOccluders: (material, camera) => renderOccluders(gl, scene, material, camera, houseRef.current, sealRef.current),
      bounds: () => houseRef.current?.bounds ?? outlineRef.current,
    });
    setLighting({
      sun: light,
      indoor: { value: new Color(1, 1, 1) },
      indoorWarm: { value: new Color(0, 0, 0) },
      outdoor: { value: new Color(1, 1, 1) },
      house: houseRef,
      invalidate: () => {
        dirty.current = true;
        frame(); // a view drawn on demand (layout pictures) must draw again
      },
    });
    return () => {
      light.dispose();
      setLighting(null);
    };
  }, [gl, scene, frame]);

  useEffect(() => {
    if (!lighting) return;
    lighting.sun.cutOpenings = cutOpenings;
    lighting.sun.setOpenings(sunOpenings(openings));
    lighting.invalidate();
  }, [lighting, openings, cutOpenings]);
  useEffect(() => frame(), [frame, sun]);

  const background = useMemo(() => new Color(), []);
  useEffect(() => {
    scene.background = background;
    return () => {
      scene.background = null;
    };
  }, [scene, background]);

  // The sun is worked out again (and its shadow map redrawn) only when it moved or a caster changed.
  const last = useRef('');
  useFrame((_, delta) => {
    if (!lighting) return;
    const { direction, elevation_deg: el, hour } = sunRef.current;
    const step = Math.min(delta, 0.1) / LAMPS_EASE_S;
    const ease = (value: MutableRefObject<number>, target: number) => {
      if (value.current === target) return;
      value.current = target > value.current ? Math.min(target, value.current + step) : Math.max(target, value.current - step);
      frame(); // a view drawn on demand keeps drawing until the lights have settled
    };
    ease(work, lampsRef.current === 'work' ? 1 : 0);
    ease(lit, lampsSwitchedOn(hour));
    const key = `${direction.join(',')}|${el}|${work.current.toFixed(3)}|${lit.current.toFixed(3)}`;
    if (!dirty.current && key === last.current) return;
    dirty.current = false;
    last.current = key;
    const toSun = new Vector3(direction[0], direction[1], direction[2]);
    lighting.sun.update(toSun, el);
    const sunWork = lighting.sun.uniforms.sunWork; // sun.ts declares it (docs/specs/lighting.md, 9)
    const on = lit.current * lit.current * (3 - 2 * lit.current);
    if (sunWork) sunWork.value = work.current * work.current * (3 - 2 * work.current) * on;
    houseRef.current?.setLight(lighting.sun.sky, toSun, lighting.sun.uniforms.sunColor!.value as Color, on);
    plainLight(lighting.sun.sky, true, true, lighting.indoor.value);
    plainLight(lighting.sun.sky, true, false, lighting.outdoor.value);
    plainLamps(lighting.sun.uniforms.sunNight!.value as number, on, lighting.indoorWarm.value).add(houseRef.current?.glow ?? BLACK);
    // Seen only where the house's own sky is not there (a twin without a rebuilt house).
    background.copy(NIGHT).lerp(DUSK, smoothstep(-8, 1, el) * (1 - smoothstep(4, 18, el)) * 0.7).lerp(DAY, smoothstep(-2, 16, el));
  });

  if (!lighting) return null;
  return <LightContext.Provider value={lighting}>{children}</LightContext.Provider>;
}

// ---------------------------------------------------------------------------------------------
// The house, and the stand-ins for a twin without one
// ---------------------------------------------------------------------------------------------

/** Whether an object and everything above it is shown. */
function shown(o: Object3D | null): boolean {
  for (; o; o = o.parent) if (!o.visible) return false;
  return true;
}

/** A click on a wall (annotating openings): the first surface that is drawn, not one hidden (the roof, from outside). */
export function wallClick(onWallClick: (point: V3, normal: V3) => void) {
  return (e: ThreeEvent<MouseEvent>) => {
    if (e.delta > 4) return; // a drag is a camera move, not a click
    const hit = e.intersections.find((i) => i.face && shown(i.object));
    if (!hit?.face) return;
    e.stopPropagation();
    const n = hit.face.normal.clone().transformDirection(hit.object.matrixWorld);
    onWallClick([hit.point.x, hit.point.y, hit.point.z], [n.x, n.y, n.z]);
  };
}

export function HouseModel({
  url,
  info,
  inside,
  topStorey = null,
  onWallClick,
  onAssetError,
  onReady,
  onComplete,
  onHouse,
}: {
  url: string;
  info: HouseInfo;
  /** Ceilings and roofs are drawn (from inside); off from outside, a "dollhouse". */
  inside: boolean;
  /** Draw only the storeys up to this one (looking down into a room); null for all. */
  topStorey?: number | null;
  onWallClick?: ((point: V3, normal: V3) => void) | null;
  onAssetError(message: string): void;
  /** The house is on screen. */
  onReady?(): void;
  /** Every picture and the surroundings have arrived too. */
  onComplete?(): void;
  onHouse?(house: House | null): void;
}) {
  const lighting = useHouseLighting();
  const gl = useThree((s) => s.gl);
  const [house, setHouse] = useState<House | null>(null);
  const errorRef = useRef(onAssetError);
  errorRef.current = onAssetError;
  const completeRef = useRef(onComplete);
  completeRef.current = onComplete;

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
        void h.ready.then(() => {
          if (!alive) return;
          lighting.invalidate();
          completeRef.current?.();
        });
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

  const frame = useThree((s) => s.invalidate);
  useEffect(() => {
    if (!house) return;
    house.setLid(inside);
    house.setTopStorey(topStorey);
    frame();
  }, [house, inside, topStorey, frame]);
  useEffect(() => {
    if (house) onReady?.();
    onHouse?.(house);
  }, [house]); // eslint-disable-line react-hooks/exhaustive-deps
  useFrame(({ camera }) => house?.update(camera));

  if (!house) return null;
  return <primitive object={house.group} onClick={onWallClick ? wallClick(onWallClick) : undefined} />;
}

/**
 * Stand-in for a twin that has no rebuilt house (C3: a degraded twin is still valid): each room
 * as a floor and inward-facing walls, built from the room outlines. The walls have no holes, so
 * the sun's shadow map lets light through where the twin's openings are.
 */
export function RoomShells({ rooms, onWallClick, onReady }: { rooms: Room[]; onWallClick?: ((point: V3, normal: V3) => void) | null; onReady?(): void }) {
  const lighting = useHouseLighting();
  const { floors, walls, floorMaterial, wallMaterial } = useMemo(() => {
    const make = (color: string, side: typeof FrontSide | typeof DoubleSide) => {
      const m = new MeshBasicMaterial({ color, side });
      shadePlain(m, lighting.indoor, lighting.sun.uniforms, lighting.indoorWarm);
      return m;
    };
    return {
      floors: rooms.map((r) => ({ id: r.room_id, geometry: floorGeometry(r) })),
      walls: rooms.map((r) => ({ id: r.room_id, geometry: wallGeometry(r) })),
      floorMaterial: make('#cdbfa9', DoubleSide),
      wallMaterial: make('#e9e4da', FrontSide),
    };
  }, [rooms, lighting]);
  useEffect(() => {
    lighting.invalidate();
    onReady?.();
  }, [lighting, floors]); // eslint-disable-line react-hooks/exhaustive-deps
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
        <mesh key={f.id} geometry={f.geometry} material={floorMaterial} userData={{ caster: true, kind: 'floor' }} />
      ))}
      {walls.map((w) => (
        <mesh key={w.id} geometry={w.geometry} material={wallMaterial} userData={{ caster: true }} onClick={click} />
      ))}
    </>
  );
}

/** Plain ground for a twin without a rebuilt house, so the rooms do not float and shadows land somewhere. */
export function Ground({ twin }: { twin: PropertyTwin }) {
  const lighting = useHouseLighting();
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

// ---------------------------------------------------------------------------------------------
// Furniture
// ---------------------------------------------------------------------------------------------

/** The selected piece: each color this far toward this blue. */
const SELECTED = { color: new Color(0.23, 0.51, 0.96), mix: 0.45 };

/** A furniture root at a stored pose, as it is (A5). Yaw is about +Y, counter-clockwise from above (I-G12). */
export function poseObject(object: Object3D, x: number, y: number, z: number, yawDeg: number): void {
  object.position.set(x, y, z);
  object.rotation.set(0, yawDeg * DEG, 0);
}

/** A furniture GLB, lit like the rooms around it, casting its own shadow. */
export function Furniture({ item, selected = false, onObject, onLoaded }: { item: PlacedItem; selected?: boolean; onObject?(id: string, object: Object3D | null): void; onLoaded?(): void }) {
  const lighting = useHouseLighting();
  const gltf = useGLTF(item.url);
  const { scene, materials } = useMemo(() => {
    const root = gltf.scene.clone(true);
    // One material per source material.
    const made = new Map<string, { material: MeshBasicMaterial; own: Color }>();
    root.traverse((node) => {
      const mesh = node as Mesh;
      if (!mesh.isMesh) return;
      const src = (Array.isArray(mesh.material) ? mesh.material[0] : mesh.material) as MeshStandardMaterial | undefined;
      let entry = src && made.get(src.uuid);
      if (!entry) {
        const map = src?.map ?? null;
        // A photo texture is the surface color; untextured meshes keep their own color.
        const own = map ? new Color('#ffffff') : (src?.color?.clone() ?? new Color('#d8d4cc'));
        const material = new MeshBasicMaterial({ color: own.clone(), map, side: src?.side ?? FrontSide, transparent: src?.transparent ?? false, opacity: src?.opacity ?? 1, alphaTest: src?.alphaTest ?? 0 });
        shadePlain(material, lighting.indoor, lighting.sun.uniforms, lighting.indoorWarm);
        entry = { material, own };
        if (src) made.set(src.uuid, entry);
      }
      if (!mesh.geometry.getAttribute('normal')) {
        mesh.geometry = mesh.geometry.clone();
        mesh.geometry.computeVertexNormals();
      }
      mesh.material = entry.material;
      mesh.userData = { ...mesh.userData, caster: true };
    });
    return { scene: root, materials: [...made.values()] };
  }, [gltf.scene, lighting]);
  useEffect(() => () => materials.forEach((m) => m.material.dispose()), [materials]);

  // A5: the stored pose, as it is. A view may move the piece itself in between (a drag).
  const { x, y, z } = item.position;
  const scale = item.scale ?? 1;
  useLayoutEffect(() => {
    poseObject(scene, x, y, z, item.yaw_deg);
    scene.scale.setScalar(scale); // resized by hand; a drag only poses it, so this stays
    lighting.invalidate(); // a piece that appears or moves changes the shadows
  }, [scene, x, y, z, item.yaw_deg, scale, lighting]);
  useEffect(() => () => lighting.invalidate(), [lighting, scene]);

  const frame = useThree((s) => s.invalidate);
  useEffect(() => {
    for (const { material, own } of materials) material.color.copy(own).lerp(SELECTED.color, selected ? SELECTED.mix : 0);
    frame();
  }, [materials, selected, frame]);

  const id = item.item_id;
  useEffect(() => {
    onObject?.(id, scene);
    onLoaded?.();
    return () => onObject?.(id, null);
  }, [id, scene]); // eslint-disable-line react-hooks/exhaustive-deps
  return <primitive object={scene} />;
}

/** A piece drawn as a plain box from its dimensions (layout pictures, when its GLB cannot be loaded). */
export function FurnitureBox({ position, yaw_deg, dims }: { position: Vec3; yaw_deg: number; dims: { w: number; d: number; h: number } }) {
  const lighting = useHouseLighting();
  const material = useMemo(() => {
    const m = new MeshBasicMaterial({ color: '#9aa3ad' });
    shadePlain(m, lighting.indoor, lighting.sun.uniforms, lighting.indoorWarm);
    return m;
  }, [lighting]);
  useEffect(() => () => material.dispose(), [material]);
  useEffect(() => {
    lighting.invalidate();
    return () => lighting.invalidate();
  }, [lighting]);
  return (
    <group position={[position.x, position.y, position.z]} rotation={[0, yaw_deg * DEG, 0]}>
      <mesh position={[0, dims.h / 2, 0]} material={material} userData={{ caster: true }}>
        <boxGeometry args={[dims.w, dims.h, dims.d]} />
      </mesh>
    </group>
  );
}

export class AssetBoundary extends Component<{ label: string; onError(message: string): void; fallback?: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: Error) {
    this.props.onError(`${this.props.label} could not be loaded: ${error.message}`);
  }
  render() {
    return this.state.failed ? (this.props.fallback ?? null) : this.props.children;
  }
}
