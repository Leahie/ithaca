'use client';

import type { InventoryItem, Opening, Placement, Room } from '@ithaca/schema';
import { bounds, footprint, frontDirection } from '@/lib/geometry/polygon';

const ITEM_COLORS = ['#8e98a6', '#b98d5c', '#c79a33', '#7fa58a', '#a58ab8', '#b07a7a'];

interface Props {
  room: Room;
  openings: Opening[];
  placements: Placement[];
  items: Map<string, InventoryItem>;
  className?: string;
  labels?: boolean;
}

/**
 * Top-down plan of a layout, drawn from the stored poses (A5). Seen from above: +X is right
 * and +Z is down the page. Used on variant cards until the rendered thumbnail arrives.
 */
export function PlanPreview({ room, openings, placements, items, className, labels = true }: Props) {
  const b = bounds(room.polygon);
  const pad = 0.35;
  const w = b.maxX - b.minX + pad * 2;
  const h = b.maxZ - b.minZ + pad * 2;
  const pts = (poly: readonly (readonly [number, number])[]) => poly.map(([x, z]) => `${(x - b.minX + pad).toFixed(3)},${(z - b.minZ + pad).toFixed(3)}`).join(' ');
  const sx = (x: number) => x - b.minX + pad;
  const sz = (z: number) => z - b.minZ + pad;

  return (
    <svg viewBox={`0 0 ${w.toFixed(3)} ${h.toFixed(3)}`} className={className} role="img" aria-label={`Plan of ${room.label} with ${placements.length} items`} preserveAspectRatio="xMidYMid meet">
      <polygon points={pts(room.polygon)} fill="#26221c" stroke="#8f887c" strokeWidth={0.07} strokeLinejoin="round" />
      {openings.map((o) => {
        const [p0, p1] = o.polygon;
        if (!p0 || !p1) return null;
        return <line key={o.opening_id} x1={sx(p0.x)} y1={sz(p0.z)} x2={sx(p1.x)} y2={sz(p1.z)} stroke={o.kind === 'door' ? '#f2b84b' : '#6fb6e8'} strokeWidth={0.13} strokeLinecap="round" />;
      })}
      {placements.map((p, i) => {
        const item = items.get(p.item_id);
        if (!item) return null;
        const fp = footprint(p.position.x, p.position.z, item.dims_m.w, item.dims_m.d, p.yaw_deg);
        const [fx, fz] = frontDirection(p.yaw_deg);
        const cx = sx(p.position.x);
        const cz = sz(p.position.z);
        const reach = item.dims_m.d / 2;
        return (
          <g key={p.item_id}>
            <polygon points={pts(fp)} fill={ITEM_COLORS[i % ITEM_COLORS.length]} fillOpacity={0.9} stroke="#171512" strokeWidth={0.04} strokeLinejoin="round" />
            {/* A tick on the front edge shows which way the item faces. */}
            <line x1={cx + fx * reach * 0.55} y1={cz + fz * reach * 0.55} x2={cx + fx * reach} y2={cz + fz * reach} stroke="#171512" strokeWidth={0.07} strokeLinecap="round" />
            {labels && (
              <text x={cx} y={cz} textAnchor="middle" dominantBaseline="central" fontSize={0.2} fill="#171512" fontWeight={600}>
                {item.item_class.replace(/_/g, ' ')}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}
