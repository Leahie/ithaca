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
 * `PlanSvg` is the drawing alone, which the phone remote uses too.
 *
 * Drawn the way the listing's plans are: interior -X is up the page and -Z is to the right.
 */
const sx = (z: number) => -z;
const sy = (x: number) => x;

function planBox(twin: PropertyTwin) {
  const xs = twin.rooms.flatMap((r) => r.polygon.map((p) => sx(p[1])));
  const ys = twin.rooms.flatMap((r) => r.polygon.map((p) => sy(p[0])));
  const pad = 0.6;
  return { x: Math.min(...xs) - pad, y: Math.min(...ys) - pad, w: Math.max(...xs) - Math.min(...xs) + pad * 2, h: Math.max(...ys) - Math.min(...ys) + pad * 2 };
}

/** One floor: rooms to click, wall lines, names. Every floor shares a frame, so floors line up. */
export function PlanSvg({ twin, floorId, roomId, onPick, className }: { twin: PropertyTwin; floorId: string | undefined; roomId: string | null; onPick(roomId: string): void; className?: string }) {
  const box = useMemo(() => planBox(twin), [twin]);
  const rooms = twin.rooms.filter((r) => r.floor_id === floorId);
  const walls = (twin.walls ?? []).filter((w) => w.floor_id === floorId);
  return (
    <svg viewBox={`${box.x} ${box.y} ${box.w} ${box.h}`} className={className} role="img" aria-label="Floor plan" preserveAspectRatio="xMidYMid meet">
      {rooms.map((room) => (
        <polygon key={room.room_id} data-room-id={room.room_id} points={room.polygon.map(([x, z]) => `${sx(z)},${sy(x)}`).join(' ')}
          className={cn('cursor-pointer transition-colors', room.room_id === roomId ? 'fill-main' : 'fill-raised hover:fill-ink')} stroke="var(--color-line)" strokeWidth={0.03}
          onClick={() => onPick(room.room_id)}>
          <title>{`${roomName(twin, room.room_id)} · ${roomArea(room).toFixed(0)} m²`}</title>
        </polygon>
      ))}
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
  );
}

/** Floor switch + plan. `picked` is the floor chosen here; without one, the floor you are on. */
export function FloorTabs({ twin, floorId, onPick }: { twin: PropertyTwin; floorId: string | undefined; onPick(floorId: string): void }) {
  const floors = [...twin.floors].sort((a, b) => a.index - b.index);
  return (
    <>
      {floors.map((f) => (
        <button key={f.floor_id} type="button" aria-pressed={f.floor_id === floorId} onClick={() => onPick(f.floor_id)}
          className={cn('rounded-full px-3 py-1 text-sm', f.floor_id === floorId ? 'bg-main font-semibold text-on-main' : 'text-text hover:bg-ink')}>
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

export function HousePlan({ twin }: { twin: PropertyTwin }) {
  const roomId = useSession((s) => s.viewer.room_id);
  const dispatch = useSession((s) => s.dispatch);
  // While the phone is choosing a room, the big screen shows the plan.
  const phoneChoosing = useSession((s) => s.peers.phone && s.remoteView === 'rooms');
  const [opened, setOpen] = useState(false);
  const [floorId, setPicked] = useFloor(twin, roomId);
  const open = opened || phoneChoosing;

  if (!open) {
    return (
      <button type="button" data-testid="plan-open" onClick={() => { setPicked(null); setOpen(true); }}
        className="pointer-events-auto rounded-full border border-line bg-surface/90 px-3.5 py-1.5 text-sm font-semibold text-text shadow-sm backdrop-blur hover:bg-raised">
        Floor plan
      </button>
    );
  }
  return (
    <div data-testid="plan" className="pointer-events-auto flex max-h-[calc(100vh-9rem)] w-[min(720px,calc(100vw-280px))] flex-col gap-2 rounded-2xl border border-line bg-surface/90 p-3 shadow-lg backdrop-blur-md">
      <div className="flex items-center gap-1.5">
        <FloorTabs twin={twin} floorId={floorId} onPick={setPicked} />
        <span className="ml-auto text-xs text-muted">{phoneChoosing ? 'Choose a room on your phone' : 'Click a room to go there'}</span>
        {!phoneChoosing && (
          <button type="button" aria-label="Close the floor plan" onClick={() => setOpen(false)} className="rounded-full px-2.5 py-1 text-sm text-text hover:bg-ink">
            ✕
          </button>
        )}
      </div>
      <PlanSvg twin={twin} floorId={floorId} roomId={roomId} className="min-h-0 w-full flex-1"
        onPick={(id) => { dispatch('viewer.goto', { room_id: id }); setOpen(false); }} />
    </div>
  );
}
