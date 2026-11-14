'use client';

import { useState } from 'react';
import { CHART, Tooltip, niceTicks, useWidth, type TooltipState } from './kit';

export interface Column {
  /** Axis label; may be empty to skip a tick. */
  tick: string;
  /** Full name for the tooltip, e.g. "8:00 am". */
  label: string;
  value: number;
  /** Second series drawn as a marker line on the column (e.g. the baseline scenario). */
  reference?: number;
}

interface Props {
  columns: Column[];
  ariaLabel: string;
  height?: number;
  format(value: number): string;
  seriesLabel: string;
  referenceLabel?: string;
  selected?: number;
  onSelect?(index: number): void;
  /** Lower bound of the value axis. Columns grow from here. */
  floor?: number;
  maxTicks?: number;
}

const M = { top: 10, right: 8, bottom: 22, left: 30 };

/**
 * Columns from one baseline: at most 24px thick, 4px rounded tops, 2px gaps.
 * The selected column carries the full series color; the rest step back.
 */
export function ColumnChart({ columns, ariaLabel, height = 190, format, seriesLabel, referenceLabel, selected, onSelect, floor = 0, maxTicks = 4 }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const n = columns.length;
  const w = Math.max(0, width - M.left - M.right);
  const h = height - M.top - M.bottom;
  const max = Math.max(floor + 1, ...columns.flatMap((c) => [c.value, c.reference ?? -Infinity]));
  const ticks = niceTicks(floor, max, maxTicks);
  const top = Math.max(max, ticks.at(-1) ?? max);
  const band = n ? w / n : 0;
  const bar = Math.max(2, Math.min(24, band - 2));
  const cx = (i: number) => M.left + band * (i + 0.5);
  const y = (v: number) => M.top + h - ((Math.max(floor, v) - floor) / (top - floor)) * h;
  const base = y(floor);

  const active = hover;
  const col = active !== null ? columns[active] : undefined;
  const tip: TooltipState | null = col
    ? {
        x: cx(active!),
        y: y(Math.max(col.value, col.reference ?? -Infinity)),
        title: col.label,
        rows: [
          { value: format(col.value), label: seriesLabel, color: CHART.series1 },
          ...(col.reference !== undefined && referenceLabel ? [{ value: format(col.reference), label: referenceLabel, color: CHART.series2 }] : []),
        ],
      }
    : null;

  return (
    <div ref={ref} className="relative w-full" style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="group" aria-label={ariaLabel} className="block">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={M.left + w} y1={y(t)} y2={y(t)} stroke={t === floor ? CHART.axis : CHART.grid} strokeWidth={1} />
              <text x={M.left - 6} y={y(t) + 3.5} textAnchor="end" fontSize={10} fill={CHART.muted} style={{ fontVariantNumeric: 'tabular-nums' }}>
                {t}
              </text>
            </g>
          ))}
          {columns.map((c, i) => {
            const yTop = y(c.value);
            const r = Math.min(4, bar / 2, Math.max(0, base - yTop));
            const x0 = cx(i) - bar / 2;
            const isSel = i === selected;
            return (
              <g key={i}>
                <path
                  d={`M${x0},${base}V${yTop + r}Q${x0},${yTop} ${x0 + r},${yTop}H${x0 + bar - r}Q${x0 + bar},${yTop} ${x0 + bar},${yTop + r}V${base}Z`}
                  fill={isSel || selected === undefined ? CHART.series1 : CHART.series1Dim}
                  opacity={active === i && !isSel ? 0.85 : 1}
                />
                {c.reference !== undefined && (
                  <line x1={x0 - 1} x2={x0 + bar + 1} y1={y(c.reference)} y2={y(c.reference)} stroke={CHART.series2} strokeWidth={2} strokeLinecap="round" />
                )}
                {c.tick && (
                  <text x={cx(i)} y={height - 6} textAnchor="middle" fontSize={10} fill={isSel ? CHART.ink : CHART.muted}>
                    {c.tick}
                  </text>
                )}
                {/* The hit target is the whole band, not only the painted bar. */}
                <rect
                  x={cx(i) - band / 2}
                  y={M.top}
                  width={band}
                  height={h}
                  fill="transparent"
                  tabIndex={0}
                  role="button"
                  aria-pressed={isSel}
                  aria-label={`${c.label}: ${format(c.value)}${c.reference !== undefined && referenceLabel ? `, ${referenceLabel} ${format(c.reference)}` : ''}`}
                  className={onSelect ? 'cursor-pointer outline-none' : 'outline-none'}
                  onPointerEnter={() => setHover(i)}
                  onPointerLeave={() => setHover((cur) => (cur === i ? null : cur))}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover((cur) => (cur === i ? null : cur))}
                  onClick={() => onSelect?.(i)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      onSelect?.(i);
                    }
                  }}
                />
              </g>
            );
          })}
          {active !== null && (
            <rect x={cx(active) - bar / 2 - 2} y={M.top} width={bar + 4} height={h} fill="none" stroke={CHART.ink2} strokeOpacity={0.35} strokeWidth={1} rx={3} pointerEvents="none" />
          )}
        </svg>
      )}
      <Tooltip tip={tip} width={width} />
    </div>
  );
}
