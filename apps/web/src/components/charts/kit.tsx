'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';

/**
 * Shared chart chrome. Colors are validated against the app's dark surface (#171512) with the
 * dataviz palette checker: categorical slot 1 blue, slot 2 orange, and amber for sun hours.
 */
export const CHART = {
  series1: '#3987e5',
  series1Dim: '#1c5cab',
  series2: '#d95926',
  sun: '#c98500',
  grid: '#2c2a26',
  axis: '#3d3933',
  ink: '#ece7df',
  ink2: '#c3bcb0',
  muted: '#8f887c',
  surface: '#171512',
} as const;

/** Sun-hours heat ramp: one hue, dark to bright, so "no sun" recedes into the dark surface. */
export const SUN_RAMP = ['#2a2113', '#4a3610', '#70500c', '#996c08', '#c98500', '#f2b84b', '#ffe2a0'] as const;

export function rampColor(t: number): [number, number, number] {
  const x = Math.min(1, Math.max(0, t)) * (SUN_RAMP.length - 1);
  const i = Math.min(SUN_RAMP.length - 2, Math.floor(x));
  const f = x - i;
  const a = hex(SUN_RAMP[i]!);
  const b = hex(SUN_RAMP[i + 1]!);
  return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
}

function hex(h: string): [number, number, number] {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Round tick values that cover [min, max]. */
export function niceTicks(min: number, max: number, target = 4): number[] {
  if (!(max > min)) return [min];
  const raw = (max - min) / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const ticks: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) ticks.push(Number(v.toFixed(10)));
  return ticks;
}

export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.floor(entry!.contentRect.width)));
    ro.observe(node);
    setWidth(Math.floor(node.getBoundingClientRect().width));
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

export interface TooltipState {
  x: number;
  y: number;
  /** Values lead, labels follow. */
  rows: { value: string; label: string; color?: string }[];
  title?: string;
}

export function Tooltip({ tip, width }: { tip: TooltipState | null; width: number }) {
  if (!tip) return null;
  const left = Math.min(Math.max(tip.x, 70), Math.max(70, width - 70));
  return (
    <div
      role="status"
      className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-md border border-line-strong bg-raised px-2.5 py-1.5 text-xs whitespace-nowrap shadow-lg"
      style={{ left, top: tip.y - 8 }}
    >
      {tip.title && <div className="mb-0.5 text-[11px] text-muted">{tip.title}</div>}
      {tip.rows.map((r, i) => (
        <div key={i} className="flex items-center gap-1.5">
          {r.color && <span className="inline-block h-0.5 w-3 rounded" style={{ background: r.color }} />}
          <span className="font-semibold text-text">{r.value}</span>
          <span className="text-muted">{r.label}</span>
        </div>
      ))}
    </div>
  );
}

export function Legend({ items }: { items: { label: string; color: string; shape: 'rect' | 'line' }[] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
      {items.map((i) => (
        <span key={i.label} className="inline-flex items-center gap-1.5">
          <span className={i.shape === 'rect' ? 'h-2.5 w-2.5 rounded-[2px]' : 'h-0.5 w-4 rounded'} style={{ background: i.color }} />
          {i.label}
        </span>
      ))}
    </div>
  );
}

/** The table twin of a chart: every value reachable without hover. */
export function TableView({ caption, columns, rows }: { caption: string; columns: string[]; rows: (string | number)[][] }) {
  return (
    <details className="mt-2 text-xs text-muted">
      <summary className="cursor-pointer select-none hover:text-text">Show as table</summary>
      <div className="mt-2 max-h-56 overflow-auto rounded-md border border-line">
        <table className="w-full border-collapse text-left tabular-nums">
          <caption className="sr-only">{caption}</caption>
          <thead className="sticky top-0 bg-raised text-[11px] text-muted">
            <tr>
              {columns.map((c) => (
                <th key={c} scope="col" className="px-2.5 py-1.5 font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t border-line">
                {r.map((cell, j) => (
                  <td key={j} className="px-2.5 py-1 text-text">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

export function ChartTitle({ title, subtitle, aside }: { title: string; subtitle?: ReactNode; aside?: ReactNode }) {
  return (
    <div className="mb-2 flex items-start justify-between gap-3">
      <div>
        <div className="text-sm font-medium text-text">{title}</div>
        {subtitle && <div className="text-xs text-muted">{subtitle}</div>}
      </div>
      {aside}
    </div>
  );
}
