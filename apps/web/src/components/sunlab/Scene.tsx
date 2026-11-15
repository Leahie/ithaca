'use client';

import type { Opening, PropertyTwin, Room, SunHeatmap } from '@ithaca/schema';
import { CameraControls } from '@react-three/drei';
import { Suspense, useEffect, useMemo, useRef } from 'react';
import { BufferAttribute, BufferGeometry, DataTexture, DoubleSide, LinearFilter, RGBAFormat, SRGBColorSpace, Vector3 } from 'three';
import { rampColor } from '@/components/charts/kit';
import { AssetBoundary, Furniture, Ground, HouseLight, HouseModel, RoomShells, houseBounds, passesLight, type PlacedItem, type RebuiltHouse, type SunState } from '@/components/house3d/House3D';
import type { V3 } from '@/lib/geometry/frames';
import { bounds, centroid, pointInPolygon } from '@/lib/geometry/polygon';
import { onHandNav } from '@/lib/hands/engine';
import { onSessionEvent, useSession } from '@/lib/store/session';

/**
 * The Sun Lab's picture: the house (components/house3d) under the sun of the chosen moment, with
 * what only the Sun Lab shows: the sun-hours heatmap, the windows and doors, and annotation clicks.
 */

export type { PlacedItem, SunState };

const DEG = Math.PI / 180;

export type SunLabView = { kind: 'overview'; seq: number } | { kind: 'room'; room: Room; seq: number };

export interface SceneProps {
  twin: PropertyTwin;
  /** The rebuilt house (its house.json, read), or null to draw the rooms from their outlines. */
  house: RebuiltHouse | null;
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

export function Scene({ twin, house, openings, sun, placements, heatmap, showOpenings, onWallClick, pendingCorner, view, onAssetError, onReady }: SceneProps) {
  return (
    <HouseLight twin={twin} openings={openings} sun={sun} cutOpenings={!house}>
      <CameraRig twin={twin} openings={openings} view={view} />
      {house ? (
        <HouseModel url={house.url} info={house.info} inside={view.kind === 'room'} onWallClick={onWallClick} onAssetError={onAssetError} onReady={onReady} />
      ) : (
        <>
          <RoomShells rooms={twin.rooms} onWallClick={onWallClick} onReady={onReady} />
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
    </HouseLight>
  );
}
