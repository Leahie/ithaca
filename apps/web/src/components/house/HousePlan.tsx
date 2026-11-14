'use client';

import { useMemo, useState } from 'react';
import type { PropertyTwin } from '@ithaca/schema';
import { cn } from '@/components/ui';
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

export function HousePlan({ twin }: { twin: PropertyTwin }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  const [open, setOpen] = useState(false);
  const floors = useMemo(() => [...twin.floors].sort((a, b) => a.index - b.index), [twin]);
  const currentFloor = twin.rooms.find((r) => r.room_id === roomId)?.floor_id;
  const [picked, setPicked] = useState<string | null>(null);
  const floorId = picked ?? currentFloor ?? floors[0]?.floor_id;

  // One frame for every floor, so the floors line up when you switch between them.
  const box = useMemo(() => {
    const xs = twin.rooms.flatMap((r) => r.polygon.map((p) => sx(p[1])));
    const ys = twin.rooms.flatMap((r) => r.polygon.map((p) => sy(p[0])));
    const pad = 0.6;
    return { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + pad * 2, h: Math.max(...ys) - Math.min(...ys) + pad * 2 };
  }, [twin]);

  const button = 'pointer-events-auto rounded-full border border-line bg-surface/90 px-3.5 py-1.5 text-sm font-semibold text-text shadow-sm backdrop-blur hover:bg-raised';
  if (!open) {
    return (
      <button type="button" data-testid="plan-open" className={button} onClick={() => { setPicked(null); setOpen(true); }}>
        Floor plan
      </button>
    );
  }
  const rooms = twin.rooms.filter((r) => r.floor_id === floorId);
  const walls = (twin.walls ?? []).filter((w) => w.floor_id === floorId);
  return (
    <div data-testid="plan" className="pointer-events-auto flex max-h-[calc(100vh-9rem)] w-[min(720px,calc(100vw-280px))] flex-col gap-2 rounded-2xl border border-line bg-surface/90 p-3 shadow-lg backdrop-blur-md">
      <div className="flex items-center gap-1.5">
        {floors.map((f) => (
          <button key={f.floor_id} type="button" aria-pressed={f.floor_id === floorId} onClick={() => setPicked(f.floor_id)}
            className={cn('rounded-full px-3 py-1 text-sm', f.floor_id === floorId ? 'bg-main font-semibold text-on-main' : 'text-text hover:bg-ink')}>
            {f.label ?? `Floor ${f.index + 1}`}
          </button>
        ))}
        <span className="ml-auto text-xs text-muted">Click a room to go there</span>
        <button type="button" aria-label="Close the floor plan" onClick={() => setOpen(false)} className="rounded-full px-2.5 py-1 text-sm text-text hover:bg-ink">
          ✕
        </button>
      </div>
      <svg viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`} className="min-h-0 w-full flex-1" role="img" aria-label="Floor plan" preserveAspectRatio="xMidYMid meet">
        {rooms.map((room) => {
          const here = room.room_id === roomId;
          return (
            <polygon key={room.room_id} data-room-id={room.room_id} points={room.polygon.map(([x, z]) => `${sx(z)},${sy(x)}`).join(' ')}
              className={cn('cursor-pointer transition-colors', here ? 'fill-main' : 'fill-raised hover:fill-ink')} stroke="var(--color-line)" strokeWidth={0.03}
              onClick={() => { dispatch('viewer.goto', { room_id: room.room_id }); setOpen(false); }}>
              <title>{`${roomName(twin, room.room_id)} · ${roomArea(room).toFixed(0)} m²`}</title>
            </polygon>
          );
        })}
        {walls.map((w) => (
          <line key={w.wall_id} x1={sx(w.ends[0][1])} y1={sy(w.ends[0][0])} x2={sx(w.ends[1][1])} y2={sy(w.ends[1][0])}
            stroke="var(--color-text)" strokeWidth={Math.max(0.1, w.thickness_m)} strokeLinecap="square" pointerEvents="none" />
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
              pointerEvents="none" className={room.room_id === roomId ? 'fill-on-main' : 'fill-text'}>
              {lines.map((line, i) => (
                <tspan key={i} x={sx(cz)} dy={i ? size * 1.1 : 0}>{line}</tspan>
              ))}
            </text>
          );
        })}
      </svg>
    </div>
  );
}
