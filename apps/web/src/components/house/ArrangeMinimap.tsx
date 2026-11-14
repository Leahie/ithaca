'use client';

import type { InventoryItem, Opening, PropertyTwin } from '@ithaca/schema';
import { useEffect, useMemo, useState } from 'react';
import { cn, GLASS_PANEL } from '@/components/ui';
import { bounds, footprint } from '@/lib/geometry/polygon';
import { roomName } from '@/lib/rooms';
import type { MpSdk } from '@/lib/showcase/sdk-types';
import { onSessionEvent } from '@/lib/store/session';

/** The camera cone redraws at most this often. */
const CAMERA_MS = 120;

export interface MinimapPiece {
  item_id: string;
  position: { x: number; z: number };
  yaw_deg: number;
}

/** Where the big screen's camera stands and which way it looks: Showcase yaw, 0 along -Z, positive turns left. */
export interface MinimapCamera {
  x: number;
  z: number;
  yaw_deg: number;
}

const CONE_M = 1.6;
const CONE_HALF_DEG = 32;

/** The minimap on the tour: follows the Showcase camera and the piece being dragged on the phone. */
export function LiveArrangeMinimap({ sdk, pieces, ...rest }: Omit<Parameters<typeof ArrangeMinimap>[0], 'camera'> & { sdk: MpSdk }) {
  const [camera, setCamera] = useState<MinimapCamera | null>(null);
  const [drag, setDrag] = useState<MinimapPiece | null>(null);
  useEffect(() => {
    let last = 0;
    const sub = sdk.Camera.pose.subscribe((pose) => {
      const now = performance.now();
      if (now - last < CAMERA_MS) return;
      last = now;
      setCamera({ x: pose.position.x, z: pose.position.z, yaw_deg: pose.rotation.y });
    });
    return () => sub.cancel();
  }, [sdk]);
  // A finished drag shows until the saved furniture comes back, or a while if the move was refused.
  useEffect(() => {
    let settle: ReturnType<typeof setTimeout> | null = null;
    const off = onSessionEvent((event) => {
      if (event.type !== 'furniture.drag') return;
      if (settle) clearTimeout(settle);
      settle = null;
      const { item_id, x = 0, z = 0, yaw_deg = 0 } = event.payload;
      if (item_id) setDrag({ item_id, position: { x, z }, yaw_deg });
      else settle = setTimeout(() => setDrag(null), 2500);
    });
    return () => {
      if (settle) clearTimeout(settle);
      off();
    };
  }, []);
  useEffect(() => setDrag(null), [pieces]);
  const shown = useMemo(() => (drag ? pieces.map((p) => (p.item_id === drag.item_id ? drag : p)) : pieces), [pieces, drag]);
  return <ArrangeMinimap {...rest} pieces={shown} camera={camera} />;
}

/**
 * The room being arranged from the phone, small and on glass in a corner of the big screen: its
 * furniture, the piece in hand in green, and a cone for where the camera stands and looks. Seen
 * from above as on the phone's plan: +X is right and +Z is down the page.
 */
export function ArrangeMinimap({ twin, roomId, pieces, items, selected, camera, className }: {
  twin: PropertyTwin;
  roomId: string;
  pieces: MinimapPiece[];
  items: Map<string, InventoryItem>;
  selected: string | null;
  camera: MinimapCamera | null;
  className?: string;
}) {
  const room = twin.rooms.find((r) => r.room_id === roomId);
  if (!room) return null;
  const openings: Opening[] = twin.openings.filter((o) => o.room_id === roomId || o.other_room_id === roomId);
  const b = bounds(room.polygon);
  const pad = 0.5;
  const box = { x: b.minX - pad, y: b.minZ - pad, w: b.maxX - b.minX + pad * 2, h: b.maxZ - b.minZ + pad * 2 };
  const pts = (poly: readonly (readonly [number, number])[]) => poly.map(([x, z]) => `${x.toFixed(3)},${z.toFixed(3)}`).join(' ');

  // The camera may stand outside the room (above it, or in the doorway): pinned to the edge.
  const cam = camera && {
    x: Math.min(box.x + box.w - 0.15, Math.max(box.x + 0.15, camera.x)),
    z: Math.min(box.y + box.h - 0.15, Math.max(box.y + 0.15, camera.z)),
    yaw: (camera.yaw_deg * Math.PI) / 180,
  };
  const ray = (deg: number) => {
    const a = cam!.yaw + (deg * Math.PI) / 180;
    return `${(cam!.x - Math.sin(a) * CONE_M).toFixed(3)},${(cam!.z - Math.cos(a) * CONE_M).toFixed(3)}`;
  };

  return (
    <section className={cn(GLASS_PANEL, 'w-64 rounded-2xl p-2.5')} aria-label={`Plan of the ${roomName(twin, roomId).toLowerCase()} while arranging`} data-testid="arrange-minimap">
      <p className="mb-1.5 text-[11px] font-semibold tracking-wider text-white/70 uppercase">{roomName(twin, roomId)} · arranging</p>
      <svg viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`} className="block max-h-56 w-full" preserveAspectRatio="xMidYMid meet" role="img" aria-hidden>
        <polygon points={pts(room.polygon)} className="fill-white/10 stroke-white/70" strokeWidth={0.07} strokeLinejoin="round" />
        {openings.map((o) => {
          const [p0, p1] = o.polygon;
          if (!p0 || !p1) return null;
          return <line key={o.opening_id} x1={p0.x} y1={p0.z} x2={p1.x} y2={p1.z} stroke={o.kind === 'door' ? '#f1c84b' : '#4aa8ff'} strokeOpacity={0.8} strokeWidth={0.12} strokeLinecap="round" />;
        })}
        {pieces.map((p) => {
          const item = items.get(p.item_id);
          if (!item) return null;
          const on = p.item_id === selected;
          return (
            <polygon key={p.item_id} points={pts(footprint(p.position.x, p.position.z, item.dims_m.w, item.dims_m.d, p.yaw_deg))}
              className={on ? 'fill-leaf/60 stroke-leaf' : 'fill-white/25 stroke-white/50'} strokeWidth={on ? 0.07 : 0.03} strokeLinejoin="round" />
          );
        })}
        {cam && (
          <g>
            <defs>
              <radialGradient id="minimap-cone" gradientUnits="userSpaceOnUse" cx={cam.x} cy={cam.z} r={CONE_M}>
                <stop offset="0" stopColor="#2bdc86" stopOpacity={0.55} />
                <stop offset="1" stopColor="#2bdc86" stopOpacity={0} />
              </radialGradient>
            </defs>
            <polygon points={`${cam.x},${cam.z} ${ray(CONE_HALF_DEG)} ${ray(0)} ${ray(-CONE_HALF_DEG)}`} fill="url(#minimap-cone)" />
            <circle cx={cam.x} cy={cam.z} r={0.13} fill="#2bdc86" stroke="#06261f" strokeWidth={0.04} />
          </g>
        )}
      </svg>
    </section>
  );
}
