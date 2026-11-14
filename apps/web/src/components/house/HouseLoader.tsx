'use client';

import { cn } from '@/components/ui';

/**
 * Full-cover loading screen for the house: an outline of a house drawing itself under a rising
 * sun. One component for every stage (twin, tour code, Matterport viewer), so the first load
 * reads as one animation rather than a run of spinners.
 */
export function HouseLoader({ label, leaving = false, className }: { label: string; leaving?: boolean; className?: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy={!leaving}
      className={cn('flex h-full w-full flex-col items-center justify-center gap-6 bg-surface transition-opacity duration-700', leaving && 'pointer-events-none opacity-0', className)}
    >
      <svg viewBox="0 0 120 96" className="house-loader h-24 w-30" aria-hidden>
        <circle className="house-loader-sun" cx="60" cy="40" r="9" />
        <path className="house-loader-ground" d="M8 84h104" />
        <path className="house-loader-outline" pathLength={1} d="M24 84V48L60 20l36 28v36M50 84V62h20v22" />
      </svg>
      <div className="flex flex-col items-center gap-3">
        <span className="font-display text-base font-medium text-muted">{label}</span>
        <span className="house-loader-bar relative h-1 w-40 overflow-hidden rounded-full bg-ink" />
      </div>
    </div>
  );
}
