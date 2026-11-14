'use client';

import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import type { PropertyTwin } from '@ithaca/schema';
import { cn, GLASS, GLASS_PANEL, glassToggle, useScrollLock } from '@/components/ui';
import { centroid } from '@/lib/geometry/polygon';
import { roomArea, roomName } from '@/lib/rooms';
import { useSession } from '@/lib/store/session';

/**
 * The app's own floor plan, drawn from the twin: wall lines, room outlines and the corrected room
 * names. It replaces the Matterport viewer's floor-plan view, whose labels come from the model
 * and cannot be changed. Clicking a room is a `viewer.goto`, like the room list.
 *
 * Drawn the way the listing's plans are: interior -X is up the page and -Z is to the right.
 */
const sx = (z: number) => -z;
const sy = (x: number) => x;

/** The floor's own extent, so each floor fills the panel instead of sitting in a corner of the house's. */
function planBox(twin: PropertyTwin, floorId: string | undefined) {
  const rooms = twin.rooms.filter((r) => r.floor_id === floorId);
  const points = [
    ...(rooms.length ? rooms : twin.rooms).flatMap((r) => r.polygon),
    ...(twin.walls ?? []).filter((w) => w.floor_id === floorId).flatMap((w) => w.ends),
  ];
  const xs = points.map((p) => sx(p[1]));
  const ys = points.map((p) => sy(p[0]));
  const pad = 0.4;
  return { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + pad * 2, h: Math.max(...ys) - Math.min(...ys) + pad * 2 };
}

/** One floor on glass: rooms to click, wall lines, names. */
export function PlanSvg({ twin, floorId, roomId, onPick, className, style }: { twin: PropertyTwin; floorId: string | undefined; roomId: string | null; onPick(roomId: string): void; className?: string; style?: CSSProperties }) {
  const box = useMemo(() => planBox(twin, floorId), [twin, floorId]);
  const rooms = twin.rooms.filter((r) => r.floor_id === floorId);
  const walls = (twin.walls ?? []).filter((w) => w.floor_id === floorId);
  return (
    <svg viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`} className={className} style={style} role="img" aria-label="Floor plan" preserveAspectRatio="xMidYMid meet">
      {rooms.map((room) => (
        <polygon key={room.room_id} data-room-id={room.room_id} points={room.polygon.map(([x, z]) => `${sx(z)},${sy(x)}`).join(' ')}
          className={cn('cursor-pointer transition-colors', room.room_id === roomId ? 'fill-leaf/30 stroke-leaf/80' : 'fill-white/5 stroke-white/15 hover:fill-leaf/15')} strokeWidth={room.room_id === roomId ? 0.08 : 0.03}
          onClick={() => onPick(room.room_id)}>
          <title>{`${roomName(twin, room.room_id)} · ${roomArea(room).toFixed(0)} m²`}</title>
        </polygon>
      ))}
      {walls.map((w) => (
        <line key={w.wall_id} x1={sx(w.ends[0][1])} y1={sy(w.ends[0][0])} x2={sx(w.ends[1][1])} y2={sy(w.ends[1][0])}
          className="stroke-white/70" strokeWidth={Math.max(0.1, w.thickness_m)} strokeLinecap="square" pointerEvents="none" />
      ))}
      {rooms.map((room) => {
        const a = roomArea(room);
        if (a < 2.5) return null; // too small for a name; the tooltip has it
        const [cx, cz] = centroid(room.polygon);
        const size = Math.min(0.5, Math.sqrt(a) * 0.14);
        const words = roomName(twin, room.room_id).split(' ');
        const lines = words.length > 2 ? [words.slice(0, -1).join(' '), words[words.length - 1]!] : a < 9 && words.length === 2 ? words : [words.join(' ')];
        return (
          <text key={room.room_id} x={sx(cz)} y={sy(cx) - ((lines.length - 1) * size * 1.1) / 2} textAnchor="middle" dominantBaseline="central" fontSize={size} fontWeight={600}
            pointerEvents="none" className="fill-white">
            {lines.map((line, i) => (
              <tspan key={i} x={sx(cz)} dy={i ? size * 1.1 : 0}>{line}</tspan>
            ))}
          </text>
        );
      })}
    </svg>
  );
}

/** Floor switch + plan. `picked` is the floor chosen here; without one, the floor you are on. */
export function FloorTabs({ twin, floorId, onPick }: { twin: PropertyTwin; floorId: string | undefined; onPick(floorId: string): void }) {
  const floors = [...twin.floors].sort((a, b) => a.index - b.index);
  return (
    <>
      {floors.map((f) => (
        <button key={f.floor_id} type="button" aria-pressed={f.floor_id === floorId} onClick={() => onPick(f.floor_id)}
          className={cn('rounded-full px-3 py-1 text-sm transition-colors', glassToggle(f.floor_id === floorId), f.floor_id === floorId && 'font-semibold')}>
          {f.label ?? `Floor ${f.index + 1}`}
        </button>
      ))}
    </>
  );
}

export function useFloor(twin: PropertyTwin, roomId: string | null): [string | undefined, (id: string | null) => void] {
  const [picked, setPicked] = useState<string | null>(null);
  const current = twin.rooms.find((r) => r.room_id === roomId)?.floor_id;
  const lowest = [...twin.floors].sort((a, b) => a.index - b.index)[0]?.floor_id;
  return [picked ?? current ?? lowest, setPicked];
}

/** The "Floor plan" button sits top-left of its positioned parent; the plan opens as a modal over everything. */
export function HousePlan({ twin }: { twin: PropertyTwin }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  // While the phone is choosing a room, the big screen shows the plan, and only the phone closes it.
  const phoneChoosing = useSession((s) => s.peers.phone && s.remoteView === 'rooms');
  const remoteFloor = useSession((s) => s.remoteFloor);
  const [opened, setOpen] = useState(false);
  const [localFloor, setPicked] = useFloor(twin, roomId);
  // The phone's floor switch drives the plan while it is choosing.
  const floorId = (phoneChoosing && remoteFloor) || localFloor;
  const box = useMemo(() => planBox(twin, floorId), [twin, floorId]);
  const open = opened || phoneChoosing;

  useEffect(() => {
    if (!open || phoneChoosing) return;
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, phoneChoosing]);
  useScrollLock(open);

  if (!open) {
    return (
      <div className="pointer-events-none absolute top-3 left-3 z-10">
        <button type="button" data-testid="plan-open" onClick={() => { setPicked(null); setOpen(true); }}
          className={`pointer-events-auto rounded-full px-3.5 py-2 text-sm font-semibold hover:bg-black/50 ${GLASS}`}>
          Floor plan
        </button>
      </div>
    );
  }
  // As large as the plan's shape allows in the viewport, less the panel's padding (2 × 16px)
  // and header row (32px + 8px gap).
  const size = `min(100cqw - 34px, (100cqh - 74px) * ${box.w / box.h})`;
  return createPortal(
    <div role="dialog" aria-modal aria-label="Floor plan" className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-8 backdrop-blur-sm touch-none overscroll-none [container-type:size]"
      onClick={(e) => { if (e.target === e.currentTarget && !phoneChoosing) setOpen(false); }}>
      <div data-testid="plan" className={`flex min-w-80 flex-col items-center gap-2 rounded-3xl p-4 ${GLASS_PANEL}`}>
        <div className="flex w-full items-center gap-1.5">
          <FloorTabs twin={twin} floorId={floorId} onPick={setPicked} />
          <span className="ml-auto pl-3 text-xs text-white/75">{phoneChoosing ? 'Choose a room on your phone' : 'Click a room to go there'}</span>
          {!phoneChoosing && (
            <button type="button" aria-label="Close the floor plan" onClick={() => setOpen(false)} className={`rounded-full px-2.5 py-1 text-sm ${glassToggle(false)}`}>
              ✕
            </button>
          )}
        </div>
        <PlanSvg twin={twin} floorId={floorId} roomId={roomId} style={{ width: size, aspectRatio: `${box.w} / ${box.h}` }} className="block h-auto"
          onPick={(id) => { dispatch('viewer.goto', { room_id: id }); setOpen(false); }} />
      </div>
    </div>,
    document.body,
  );
}
