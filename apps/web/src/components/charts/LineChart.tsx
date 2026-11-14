'use client';

import { useState } from 'react';
import { CHART, Tooltip, niceTicks, useWidth, type TooltipState } from './kit';

export interface LinePoint {
  /** Shown in the tooltip, e.g. "Jun 21". */
  label: string;
  value: number;
}

interface Props {
  points: LinePoint[];
  /** One series, so no legend: the title says what is plotted. */
  ariaLabel: string;
  color?: string;
  height?: number;
  format(value: number): string;
  seriesLabel: string;
  xTicks: { index: number; label: string }[];
  /** Index of the point to mark (the date in view). */
  marker?: number;
  onPick?(index: number): void;
}

const M = { top: 10, right: 12, bottom: 22, left: 30 };

/** Single-series line: 2px line, 10% area wash, hairline grid, crosshair + tooltip. */
export function LineChart({ points, ariaLabel, color = CHART.series1, height = 150, format, seriesLabel, xTicks, marker, onPick }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = points.length;
  const w = Math.max(0, width - M.left - M.right);
  const h = height - M.top - M.bottom;
  const max = Math.max(1, ...points.map((p) => p.value));
  const ticks = niceTicks(0, max, 3);
  const top = Math.max(max, ticks.at(-1) ?? max);
  const x = (i: number) => M.left + (n > 1 ? (i / (n - 1)) * w : w / 2);
  const y = (v: number) => M.top + h - (v / top) * h;
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join('');
  const active = hover ?? null;

  const indexAt = (clientX: number, rect: DOMRect) => Math.min(n - 1, Math.max(0, Math.round(((clientX - rect.left - M.left) / Math.max(1, w)) * (n - 1))));
  const tip: TooltipState | null =
    active !== null && points[active] ? { x: x(active), y: y(points[active].value), title: points[active].label, rows: [{ value: format(points[active].value), label: seriesLabel, color }] } : null;

  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {width > 0 && n > 0 && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={ariaLabel}
          tabIndex={0}
          className="block cursor-crosshair outline-none focus-visible:ring-1 focus-visible:ring-sun"
          onPointerMove={(e) => setHover(indexAt(e.clientX, e.currentTarget.getBoundingClientRect()))}
          onPointerLeave={() => setHover(null)}
          onClick={(e) => onPick?.(indexAt(e.clientX, e.currentTarget.getBoundingClientRect()))}
          onFocus={() => setHover(marker ?? 0)}
          onBlur={() => setHover(null)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
              e.preventDefault();
              setHover((i) => Math.min(n - 1, Math.max(0, (i ?? marker ?? 0) + (e.key === 'ArrowRight' ? 1 : -1) * (e.shiftKey ? 7 : 1))));
            } else if (e.key === 'Enter' && active !== null) onPick?.(active);
          }}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={M.left + w} y1={y(t)} y2={y(t)} stroke={t === 0 ? CHART.axis : CHART.grid} strokeWidth={1} />
              <text x={M.left - 6} y={y(t) + 3.5} textAnchor="end" fontSize={10} fill={CHART.muted} style={{ fontVariantNumeric: 'tabular-nums' }}>
                {t}
              </text>
            </g>
          ))}
          {xTicks.map((t) => (
            <text key={t.index} x={x(t.index)} y={height - 6} textAnchor="middle" fontSize={10} fill={CHART.muted}>
              {t.label}
            </text>
          ))}
          <path d={`${path}L${x(n - 1)},${y(0)}L${x(0)},${y(0)}Z`} fill={color} fillOpacity={0.1} />
          <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {active !== null && <line x1={x(active)} x2={x(active)} y1={M.top} y2={M.top + h} stroke={CHART.ink2} strokeWidth={1} />}
          {marker !== undefined && points[marker] && (
            <circle cx={x(marker)} cy={y(points[marker].value)} r={5} fill={color} stroke={CHART.surface} strokeWidth={2} />
          )}
          {active !== null && points[active] && active !== marker && (
            <circle cx={x(active)} cy={y(points[active].value)} r={4} fill={CHART.ink} stroke={CHART.surface} strokeWidth={2} />
          )}
        </svg>
      )}
      <Tooltip tip={tip} width={width} />
    </div>
  );
}
