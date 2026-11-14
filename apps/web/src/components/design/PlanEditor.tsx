'use client';

import type { HousePiece, InventoryItem, Opening, Room } from '@ithaca/schema';
import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from 'react';
import { api } from '@/lib/api';
import { invalidate } from '@/lib/data/resource';
import { dispatch } from '@/lib/store/session';
import { compassToInterior, type Mat4 } from '@/lib/geometry/frames';
import { bounds, footprint, frontDirection } from '@/lib/geometry/polygon';

const ITEM_COLORS = ['#b9c7d6', '#e2c39b', '#f0d58a', '#b5dcc4', '#d3c2e6', '#e6b9b4'];
const SELECTED = '#0a6b47';
const NUDGE_M = 0.05;
const TURN_DEG = 15;

interface Pose {
  x: number;
  z: number;
  yaw: number;
}

/** What a toolbar outside the plan can do to the selected piece. */
export interface PlanControls {
  /** Turn by this many degrees (positive: counter-clockwise seen from above). */
  turn(deg: number): void;
  /** Move by this many meters along the plan: +x right, +z down the page. */
  nudge(dx: number, dz: number): void;
  remove(): void;
}

interface Props {
  propertyId: string;
  room: Room;
  openings: Opening[];
  /** The pieces standing in this room of the house. */
  pieces: Pick<HousePiece, 'item_id' | 'position' | 'yaw_deg'>[];
  items: Map<string, InventoryItem>;
  className?: string;
  /** Shows a "Look at it" button for the selected piece (the phone turns the big screen to it). */
  onLookAt?(itemId: string, at: { x: number; z: number }): void;
  /** Told whenever the selected piece changes (null: none). */
  onSelect?(itemId: string | null): void;
  /** Told after every saved change, once the house's furniture has been reloaded. */
  onChanged?(): void;
  /** Only the plan, filling its box: the piece list and buttons are the caller's (see `controls`). */
  fill?: boolean;
  /** Look, don't touch: a suggested layout that is not in the house. */
  readOnly?: boolean;
  /** Select this piece from outside (null: none). */
  selected?: string | null;
  controls?: Ref<PlanControls | null>;
  /** The twin's interior_to_world: each wall gets the letter of the side of the room it is on. */
  interiorToWorld?: Mat4;
}

const SIDES = [
  ['N', 0],
  ['E', 90],
  ['S', 180],
  ['W', 270],
] as const;

/** The compass side (N, E, S or W) each wall of a room outline looks out to, at its middle. */
function wallSides(polygon: readonly (readonly [number, number])[], interiorToWorld: Mat4): { x: number; z: number; nx: number; nz: number; side: string }[] {
  const axes = SIDES.map(([side, az]) => {
    const [x, , z] = compassToInterior(interiorToWorld, az, 0);
    return { side, x, z };
  });
  let turn = 0;
  polygon.forEach(([ax, az], i) => {
    const [bx, bz] = polygon[(i + 1) % polygon.length]!;
    turn += ax * bz - bx * az;
  });
  // One letter per side, on its longest wall.
  const longest = new Map<string, { x: number; z: number; nx: number; nz: number; side: string; len: number }>();
  for (let i = 0; i < polygon.length; i++) {
    const [ax, az] = polygon[i]!;
    const [bx, bz] = polygon[(i + 1) % polygon.length]!;
    const len = Math.hypot(bx - ax, bz - az);
    if (len < 0.8) continue;
    // Outward normal: (dz, -dx) for a counter-clockwise outline in (x, z).
    let nx = (bz - az) / len;
    let nz = -(bx - ax) / len;
    if (turn < 0) [nx, nz] = [-nx, -nz];
    const best = axes.reduce((a, b) => (a.x * nx + a.z * nz >= b.x * nx + b.z * nz ? a : b));
    if ((longest.get(best.side)?.len ?? 0) < len) longest.set(best.side, { x: (ax + bx) / 2, z: (az + bz) / 2, nx, nz, side: best.side, len });
  }
  return [...longest.values()];
}

type Drag = { kind: 'move'; id: string; dx: number; dz: number } | { kind: 'turn'; id: string };

const norm = (deg: number) => ((deg % 360) + 360) % 360;

/**
 * Top-down plan of one room of the house that the user edits by hand: drag a piece to move it
 * anywhere, drag its round handle (or use the buttons, or Q / E) to turn it to any angle. Arrow
 * keys nudge the selected piece 5 cm; Remove (or Delete) takes it out of the house. A change is
 * sent when the pointer is released; the server keeps it, finds the room from the spot (a piece
 * dragged through a doorway joins the next room) and may answer with warnings (an overlap, a
 * blocked door), which are shown but never block. Seen from above: +X is right and +Z is down the
 * page, as in PlanPreview.
 */
export function PlanEditor({ propertyId, room, openings, pieces, items, className, onLookAt, onSelect, onChanged, fill = false, readOnly = false, selected: pick, controls, interiorToWorld }: Props) {
  const svg = useRef<SVGSVGElement>(null);
  const [poses, setPoses] = useState<Record<string, Pose>>({});
  // With a `selected` prop the caller owns the selection; without one, the plan does.
  const [own, setOwn] = useState<string | null>(null);
  const controlled = pick !== undefined;
  const selected = controlled ? pick : own;
  const sides = useMemo(() => (interiorToWorld ? wallSides(room.polygon, interiorToWorld) : []), [room.polygon, interiorToWorld]);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [note, setNote] = useState<{ tone: 'warn' | 'error'; text: string } | null>(null);
  const saved = useRef<Record<string, Pose>>({});
  const report = useRef(onSelect);
  report.current = onSelect;
  useEffect(() => {
    if (controlled) return;
    report.current?.(own);
    return () => report.current?.(null);
  }, [own, controlled]);
  const setSelected = (id: string | null) => (controlled ? onSelect?.(id) : setOwn(id));

  // The stored house is the truth when nothing is being dragged and no change is on its way to
  // the server. (A reply to an earlier turn must not undo the turns made since.)
  const pending = useRef(0);
  useEffect(() => {
    if (drag || pending.current > 0) return;
    const next: Record<string, Pose> = {};
    for (const p of pieces) next[p.item_id] = { x: p.position.x, z: p.position.z, yaw: p.yaw_deg };
    saved.current = next;
    setPoses(next);
  }, [pieces, drag]);
  const announce = useRef(onChanged);
  announce.current = onChanged;
  const reload = useCallback(() => {
    invalidate(`furniture:${propertyId}`); // the 3D views redraw from the stored house
    dispatch('layout.changed', { layout_id: null }); // and so do the other screens
    announce.current?.();
  }, [propertyId]);

  const b = bounds(room.polygon);
  const pad = sides.length ? 0.7 : 0.5; // room for the wall letters
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
    [b.minX, b.minZ, pad],
  );

  const commit = useCallback(
    async (id: string, pose: Pose) => {
      const before = saved.current[id];
      if (before && Math.abs(before.x - pose.x) < 1e-3 && Math.abs(before.z - pose.z) < 1e-3 && Math.abs(before.yaw - pose.yaw) < 0.05) return;
      pending.current += 1;
      try {
        const result = await api().placePiece(propertyId, id, { x: pose.x, z: pose.z, yaw_deg: norm(pose.yaw), floor_id: room.floor_id, room_id: null, snap: false });
        saved.current = { ...saved.current, [id]: pose };
        setNote(
          result.room_id !== room.room_id
            ? { tone: 'warn', text: 'It went through to the next room.' }
            : result.warnings?.length
              ? { tone: 'warn', text: result.warnings[0]! }
              : null,
        );
        pending.current -= 1;
        if (pending.current === 0) reload();
      } catch (err) {
        pending.current -= 1;
        // The only refusal is a spot in no room at all: put the piece back where it was.
        if (before) setPoses((all) => ({ ...all, [id]: before }));
        const text = err instanceof Error ? err.message : String(err);
        const match = /"violations":\s*\["([^"]+)"/.exec(text);
        setNote({ tone: 'error', text: match?.[1] ?? text });
      }
    },
    [propertyId, room.floor_id, room.room_id, reload],
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
    if (!pose || readOnly) return;
    const next = f(pose);
    setPoses((all) => ({ ...all, [id]: next }));
    if (!fill) svg.current?.focus({ preventScroll: true }); // keep the keys on the plan for the next turn
    void commit(id, next);
  };

  /** Take a piece out of the house. It stays in the family's furniture. */
  const remove = async (id: string) => {
    try {
      await api().removePiece(propertyId, id);
      setSelected(null);
      setNote(null);
      reload();
    } catch (err) {
      setNote({ tone: 'error', text: err instanceof Error ? err.message : String(err) });
    }
  };

  useImperativeHandle(controls, () => ({
    turn: (deg) => selected && change(selected, (p) => ({ ...p, yaw: norm(p.yaw + deg) })),
    nudge: (dx, dz) => selected && change(selected, (p) => ({ ...p, x: p.x + dx, z: p.z + dz })),
    remove: () => selected && void remove(selected),
  }));

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

  const ordered = pieces.filter((p) => items.has(p.item_id) && poses[p.item_id]);
  const selectedItem = selected ? items.get(selected) : undefined;
  const selectedPose = selected ? poses[selected] : undefined;

  return (
    <div className={fill ? `relative h-full w-full ${className ?? ''}` : className}>
      <svg
        ref={svg}
        viewBox={`0 0 ${w.toFixed(3)} ${h.toFixed(3)}`}
        className={
          fill
            ? 'block h-full w-full touch-none select-none outline-none'
            : 'block w-full touch-none select-none rounded-xl bg-ink outline-none focus-visible:ring-2 focus-visible:ring-leaf-deep'
        }
        style={fill ? undefined : { aspectRatio: `${w} / ${h}`, maxHeight: '46vh' }}
        preserveAspectRatio="xMidYMid meet"
        role="application"
        aria-label={readOnly ? `Plan of ${room.label} with a suggested layout` : `Plan of ${room.label}. Drag a piece to move it; drag its round handle to turn it.`}
        tabIndex={0}
        onKeyDown={readOnly ? undefined : onKeyDown}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerCancel={endDrag}
        onPointerDown={(e) => {
          if (e.target === e.currentTarget) setSelected(null);
        }}
      >
        <polygon points={pts(room.polygon)} fill="#ffffff" stroke="#06261f" strokeWidth={0.09} strokeLinejoin="round" pointerEvents="none" />
        {sides.map((s, i) => (
          <text key={i} x={sx(s.x + s.nx * 0.38)} y={sz(s.z + s.nz * 0.38)} textAnchor="middle" dominantBaseline="central" fontSize={0.26} fontWeight={700} fill="#0a6b47" pointerEvents="none">
            {s.side}
          </text>
        ))}
        {openings.map((o) => {
          const [p0, p1] = o.polygon;
          if (!p0 || !p1) return null;
          return <line key={o.opening_id} x1={sx(p0.x)} y1={sz(p0.z)} x2={sx(p1.x)} y2={sz(p1.z)} stroke={o.kind === 'door' ? '#f1c84b' : '#4aa8ff'} strokeWidth={0.15} strokeLinecap="round" pointerEvents="none" />;
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
                stroke={active ? SELECTED : '#06261f'}
                strokeWidth={active ? 0.09 : 0.03}
                strokeLinejoin="round"
                style={readOnly ? undefined : { cursor: drag?.kind === 'move' && drag.id === p.item_id ? 'grabbing' : 'grab' }}
                pointerEvents={readOnly ? 'none' : undefined}
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
              <line x1={cx + fx * reach * 0.55} y1={cz + fz * reach * 0.55} x2={cx + fx * reach} y2={cz + fz * reach} stroke="#06261f" strokeWidth={0.07} strokeLinecap="round" pointerEvents="none" />
              <text x={cx} y={cz} textAnchor="middle" dominantBaseline="central" fontSize={0.2} fill="#06261f" fontWeight={600} pointerEvents="none">
                {item.item_class.replace(/_/g, ' ')}
              </text>
              {active && !readOnly && (
                <>
                  <line x1={cx + fx * reach} y1={cz + fz * reach} x2={cx + fx * handle} y2={cz + fz * handle} stroke={SELECTED} strokeWidth={0.05} pointerEvents="none" />
                  <circle
                    cx={cx + fx * handle}
                    cy={cz + fz * handle}
                    r={0.2}
                    fill="#2bdc86"
                    stroke="#06261f"
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

      {fill ? (
        note && (
          <p role="status" className={`absolute inset-x-3 top-14 rounded-xl border px-3 py-1.5 text-sm shadow-sm ${note.tone === 'error' ? 'border-danger/30 bg-surface text-danger' : 'border-line bg-surface text-muted'}`}>
            {note.tone === 'warn' ? 'Saved. Note: ' : ''}
            {note.text}
          </p>
        )
      ) : (
        listAndBar()
      )}
    </div>
  );

  function listAndBar() {
    return (
      <>
      {/* Every piece in the room, to tap: small ones are hard to hit on the plan. */}
      {ordered.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5 text-sm" data-testid="plan-pieces">
          {ordered.map((p, i) => (
            <button key={p.item_id} type="button" aria-pressed={p.item_id === selected} onClick={() => setSelected(p.item_id === selected ? null : p.item_id)}
              className={p.item_id === selected ? 'rounded-full bg-main px-3 py-1 font-semibold text-on-main' : 'rounded-full border border-line bg-raised px-3 py-1'}>
              <i aria-hidden className="mr-1.5 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ background: ITEM_COLORS[i % ITEM_COLORS.length] }} />
              {items.get(p.item_id)!.label}
            </button>
          ))}
        </div>
      )}
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
            {onLookAt && (
              <button type="button" className="rounded-full bg-main px-2.5 py-1 font-semibold text-on-main" onClick={() => onLookAt(selected!, { x: selectedPose.x, z: selectedPose.z })}>
                Look at it
              </button>
            )}
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
      </>
    );
  }
}
