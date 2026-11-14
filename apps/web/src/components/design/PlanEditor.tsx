'use client';

import type { InventoryItem, LayoutVariant, Opening, Placement, Room } from '@ithaca/schema';
import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { invalidate } from '@/lib/data/resource';
import { bounds, footprint, frontDirection } from '@/lib/geometry/polygon';

const ITEM_COLORS = ['#8e98a6', '#b98d5c', '#c79a33', '#7fa58a', '#a58ab8', '#b07a7a'];
const NUDGE_M = 0.05;
const TURN_DEG = 15;

interface Pose {
  x: number;
  z: number;
  yaw: number;
}

interface Props {
  room: Room;
  openings: Opening[];
  layout: LayoutVariant;
  items: Map<string, InventoryItem>;
  className?: string;
}

type Drag = { kind: 'move'; id: string; dx: number; dz: number } | { kind: 'turn'; id: string };

const norm = (deg: number) => ((deg % 360) + 360) % 360;

/**
 * Top-down plan of one layout that the user edits by hand: drag a piece to move it anywhere in
 * the room, drag its round handle (or use the buttons, or Q / E) to turn it to any angle. Arrow
 * keys nudge the selected piece 5 cm; Remove (or Delete) takes it out of the room. A change is sent
 * when the pointer is released; the server
 * keeps it and may answer with warnings (an overlap, a blocked door), which are shown but never
 * block. Seen from above: +X is right and +Z is down the page, as in PlanPreview.
 */
export function PlanEditor({ room, openings, layout, items, className }: Props) {
  const svg = useRef<SVGSVGElement>(null);
  const [poses, setPoses] = useState<Record<string, Pose>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [note, setNote] = useState<{ tone: 'warn' | 'error'; text: string } | null>(null);
  const saved = useRef<Record<string, Pose>>({});

  // The stored layout is the truth when nothing is being dragged and no change is on its way to
  // the server. (A reply to an earlier turn must not undo the turns made since.)
  const pending = useRef(0);
  useEffect(() => {
    if (drag || pending.current > 0) return;
    const next: Record<string, Pose> = {};
    for (const p of layout.placements) next[p.item_id] = { x: p.position.x, z: p.position.z, yaw: p.yaw_deg };
    saved.current = next;
    setPoses(next);
  }, [layout, drag]);

  const b = bounds(room.polygon);
  const pad = 0.5;
  const w = b.maxX - b.minX + pad * 2;
  const h = b.maxZ - b.minZ + pad * 2;
  const sx = (x: number) => x - b.minX + pad;
  const sz = (z: number) => z - b.minZ + pad;
  const pts = (poly: readonly (readonly [number, number])[]) => poly.map(([x, z]) => `${sx(x).toFixed(3)},${sz(z).toFixed(3)}`).join(' ');

  /** Pointer position in the interior frame (meters). */
  const toRoom = useCallback(
    (e: { clientX: number; clientY: number }): [number, number] | null => {
      const el = svg.current;
      const ctm = el?.getScreenCTM();
      if (!el || !ctm) return null;
      const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse());
      return [p.x + b.minX - pad, p.y + b.minZ - pad];
    },
    [b.minX, b.minZ],
  );

  const commit = useCallback(
    async (id: string, pose: Pose) => {
      const before = saved.current[id];
      if (before && Math.abs(before.x - pose.x) < 1e-3 && Math.abs(before.z - pose.z) < 1e-3 && Math.abs(before.yaw - pose.yaw) < 0.05) return;
      pending.current += 1;
      try {
        const result = await api().movePlacement(layout.layout_id, id, { x: pose.x, z: pose.z, yaw_deg: norm(pose.yaw) });
        saved.current = { ...saved.current, [id]: pose };
        setNote(result.warnings?.length ? { tone: 'warn', text: result.warnings[0]! } : null);
        pending.current -= 1;
        if (pending.current === 0) {
          invalidate('layouts:'); // the 3D views redraw from the stored layout
          invalidate('layout:');
        }
      } catch (err) {
        pending.current -= 1;
        // The only refusal is leaving the room: put the piece back where it was.
        if (before) setPoses((all) => ({ ...all, [id]: before }));
        const text = err instanceof Error ? err.message : String(err);
        const match = /"violations":\s*\["([^"]+)"/.exec(text);
        setNote({ tone: 'error', text: match?.[1] ?? text });
      }
    },
    [layout.layout_id],
  );

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const at = toRoom(e);
    const pose = poses[drag.id];
    if (!at || !pose) return;
    if (drag.kind === 'move') {
      setPoses((all) => ({ ...all, [drag.id]: { ...pose, x: at[0] - drag.dx, z: at[1] - drag.dz } }));
    } else {
      // The handle sits on the piece's front, and front = (sin yaw, cos yaw).
      let yaw = (Math.atan2(at[0] - pose.x, at[1] - pose.z) * 180) / Math.PI;
      if (e.shiftKey) yaw = Math.round(yaw / TURN_DEG) * TURN_DEG; // Shift snaps to 15 degrees
      setPoses((all) => ({ ...all, [drag.id]: { ...pose, yaw: norm(yaw) } }));
    }
  };

  const endDrag = () => {
    if (!drag) return;
    const pose = poses[drag.id];
    setDrag(null);
    if (pose) void commit(drag.id, pose);
  };

  const change = (id: string, f: (p: Pose) => Pose) => {
    const pose = poses[id];
    if (!pose) return;
    const next = f(pose);
    setPoses((all) => ({ ...all, [id]: next }));
    svg.current?.focus({ preventScroll: true }); // keep the keys on the plan for the next turn
    void commit(id, next);
  };

  /** Take a piece out of the room. It stays in the user's furniture. */
  const remove = async (id: string) => {
    try {
      await api().removePlacement(layout.layout_id, id);
      setSelected(null);
      setNote(null);
      invalidate('layouts:');
      invalidate('layout:');
    } catch (err) {
      setNote({ tone: 'error', text: err instanceof Error ? err.message : String(err) });
    }
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!selected) return;
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      void remove(selected);
      return;
    }
    const step = e.shiftKey ? NUDGE_M * 4 : NUDGE_M;
    const moves: Record<string, (p: Pose) => Pose> = {
      ArrowLeft: (p) => ({ ...p, x: p.x - step }),
      ArrowRight: (p) => ({ ...p, x: p.x + step }),
      ArrowUp: (p) => ({ ...p, z: p.z - step }),
      ArrowDown: (p) => ({ ...p, z: p.z + step }),
      q: (p) => ({ ...p, yaw: norm(p.yaw + TURN_DEG) }),
      e: (p) => ({ ...p, yaw: norm(p.yaw - TURN_DEG) }),
    };
    const move = moves[e.key] ?? moves[e.key.toLowerCase()];
    if (!move) return;
    e.preventDefault();
    change(selected, move);
  };

  const ordered = layout.placements.filter((p: Placement) => items.has(p.item_id) && poses[p.item_id]);
  const selectedItem = selected ? items.get(selected) : undefined;
  const selectedPose = selected ? poses[selected] : undefined;

  return (
    <div className={className}>
      <svg
        ref={svg}
        viewBox={`0 0 ${w.toFixed(3)} ${h.toFixed(3)}`}
        className="block w-full touch-none select-none rounded-xl bg-ink outline-none focus-visible:ring-2 focus-visible:ring-sun"
        style={{ aspectRatio: `${w} / ${h}`, maxHeight: '46vh' }}
        role="application"
        aria-label={`Plan of ${room.label}. Drag a piece to move it; drag its round handle to turn it.`}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerDown={(e) => {
          if (e.target === e.currentTarget) setSelected(null);
        }}
      >
        <polygon points={pts(room.polygon)} fill="#26221c" stroke="#8f887c" strokeWidth={0.07} strokeLinejoin="round" pointerEvents="none" />
        {openings.map((o) => {
          const [p0, p1] = o.polygon;
          if (!p0 || !p1) return null;
          return <line key={o.opening_id} x1={sx(p0.x)} y1={sz(p0.z)} x2={sx(p1.x)} y2={sz(p1.z)} stroke={o.kind === 'door' ? '#f2b84b' : '#6fb6e8'} strokeWidth={0.13} strokeLinecap="round" pointerEvents="none" />;
        })}
        {ordered.map((p, i) => {
          const item = items.get(p.item_id)!;
          const pose = poses[p.item_id]!;
          const active = p.item_id === selected;
          const [fx, fz] = frontDirection(pose.yaw);
          const cx = sx(pose.x);
          const cz = sz(pose.z);
          const reach = item.dims_m.d / 2;
          const handle = reach + 0.35;
          return (
            <g key={p.item_id}>
              <polygon
                points={pts(footprint(pose.x, pose.z, item.dims_m.w, item.dims_m.d, pose.yaw))}
                fill={ITEM_COLORS[i % ITEM_COLORS.length]}
                fillOpacity={0.92}
                stroke={active ? '#ffd27a' : '#171512'}
                strokeWidth={active ? 0.07 : 0.04}
                strokeLinejoin="round"
                style={{ cursor: drag?.kind === 'move' && drag.id === p.item_id ? 'grabbing' : 'grab' }}
                onPointerDown={(e) => {
                  const at = toRoom(e);
                  if (!at) return;
                  e.currentTarget.ownerSVGElement?.setPointerCapture(e.pointerId);
                  setSelected(p.item_id);
                  setNote(null);
                  setDrag({ kind: 'move', id: p.item_id, dx: at[0] - pose.x, dz: at[1] - pose.z });
                }}
              />
              {/* A tick on the front edge shows which way the piece faces. */}
              <line x1={cx + fx * reach * 0.55} y1={cz + fz * reach * 0.55} x2={cx + fx * reach} y2={cz + fz * reach} stroke="#171512" strokeWidth={0.07} strokeLinecap="round" pointerEvents="none" />
              <text x={cx} y={cz} textAnchor="middle" dominantBaseline="central" fontSize={0.2} fill="#171512" fontWeight={600} pointerEvents="none">
                {item.item_class.replace(/_/g, ' ')}
              </text>
              {active && (
                <>
                  <line x1={cx + fx * reach} y1={cz + fz * reach} x2={cx + fx * handle} y2={cz + fz * handle} stroke="#ffd27a" strokeWidth={0.04} pointerEvents="none" />
                  <circle
                    cx={cx + fx * handle}
                    cy={cz + fz * handle}
                    r={0.16}
                    fill="#ffd27a"
                    stroke="#171512"
                    strokeWidth={0.04}
                    style={{ cursor: 'crosshair' }}
                    onPointerDown={(e) => {
                      e.currentTarget.ownerSVGElement?.setPointerCapture(e.pointerId);
                      setNote(null);
                      setDrag({ kind: 'turn', id: p.item_id });
                    }}
                  />
                </>
              )}
            </g>
          );
        })}
      </svg>

      <div className="mt-2 flex min-h-9 flex-wrap items-center gap-1.5 text-sm">
        {selectedItem && selectedPose ? (
          <>
            <span className="mr-1 truncate font-semibold">{selectedItem.label}</span>
            <button type="button" className="rounded-full bg-ink px-2.5 py-1 hover:bg-raised" onClick={() => change(selected!, (p) => ({ ...p, yaw: norm(p.yaw + TURN_DEG) }))} aria-label="Turn left 15 degrees">
              ⟲ 15°
            </button>
            <button type="button" className="rounded-full bg-ink px-2.5 py-1 hover:bg-raised" onClick={() => change(selected!, (p) => ({ ...p, yaw: norm(p.yaw - TURN_DEG) }))} aria-label="Turn right 15 degrees">
              ⟳ 15°
            </button>
            <button type="button" className="rounded-full bg-ink px-2.5 py-1 hover:bg-raised" onClick={() => change(selected!, (p) => ({ ...p, yaw: norm(p.yaw + 90) }))} aria-label="Turn a quarter">
              90°
            </button>
            <span className="ml-auto tabular-nums text-muted">{Math.round(norm(selectedPose.yaw))}°</span>
            <button type="button" className="rounded-full bg-ink px-2.5 py-1 hover:bg-raised" onClick={() => void remove(selected!)} title="Take this piece out of the room. It stays in your furniture.">
              Remove
            </button>
          </>
        ) : (
          <span className="text-muted">Drag a piece to move it. Select it to turn it.</span>
        )}
      </div>
      {note && (
        <p role="status" className={note.tone === 'error' ? 'mt-1 text-sm text-danger' : 'mt-1 text-sm text-muted'}>
          {note.tone === 'warn' ? 'Saved. Note: ' : ''}
          {note.text}
        </p>
      )}
    </div>
  );
}
