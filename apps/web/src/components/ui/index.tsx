'use client';

import { useEffect, type ButtonHTMLAttributes, type ReactNode } from 'react';

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

/**
 * A modal's dimmed, blurred backdrop. `touch-none` and `overscroll-none`: a drag anywhere outside the
 * modal's scroll area moves nothing, here or underneath.
 */
export const MODAL_BACKDROP = 'fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-3 backdrop-blur-sm touch-none overscroll-none';
/**
 * A modal's scrolling body. The panel around it needs a definite height (`h-…`, not only `max-h-…`):
 * mobile Safari will not shrink a flex child to a max-height, so the list would be clipped, not
 * scrolled. It is its own scroll container, so it pans despite the backdrop's `touch-none`, and
 * `overscroll-contain` keeps a fling at either end from scrolling the page.
 */
export const MODAL_SCROLL = 'min-h-0 flex-1 touch-pan-y overflow-y-auto overscroll-contain';

/** While a modal is open, the page under it does not scroll. */
export function useScrollLock(active = true) {
  useEffect(() => {
    if (!active) return;
    const root = document.documentElement;
    const prev = root.style.overflow;
    root.style.overflow = 'hidden';
    return () => {
      root.style.overflow = prev;
    };
  }, [active]);
}

/** Everything floating over the house (tour, big screen) is dark glass with white text, never a paper card. See docs/style.md. */
export const GLASS = 'border border-white/30 bg-black/35 text-white shadow-lg backdrop-blur-md';
/** Glass for larger panels: a little darker, so text and drawings stay legible over a bright room. */
export const GLASS_PANEL = 'border border-white/25 bg-black/55 text-white shadow-lg backdrop-blur-xl';
/** Green on glass is a tint with a leaf edge, never solid leaf (too neon over a dark room). */
const GLASS_GREEN = 'bg-leaf/25 text-white ring-1 ring-leaf/60 ring-inset hover:bg-leaf/35';
/** A control on glass; `on` is the pressed state, in the green tint. */
export const glassToggle = (on: boolean) => (on ? GLASS_GREEN : 'text-white hover:bg-white/15');
/** A pill button on glass; `primary` is the one green action. */
export const glassButton = (primary = false) =>
  cn('inline-flex h-9 shrink-0 items-center justify-center rounded-full px-4 text-sm font-semibold transition-colors disabled:opacity-45', primary ? GLASS_GREEN : 'border border-white/40 text-white hover:bg-white/15');

type ButtonVariant = 'primary' | 'subtle' | 'ghost' | 'danger';

// Pills on a hard ink ledge that they drop onto when pressed (design language: PillButton). One
// primary per screen; subtle is paper with an ink line; ghost is the quiet header form, no ledge.
const LEDGE = 'shadow-ledge active:translate-y-1 active:shadow-none disabled:translate-y-0 disabled:shadow-none';
const BUTTON: Record<ButtonVariant, string> = {
  primary: `bg-main text-on-main hover:bg-main/90 ${LEDGE}`,
  subtle: `bg-raised text-text border-2 border-text hover:bg-surface ${LEDGE}`,
  ghost: 'text-muted hover:text-text hover:bg-ink',
  danger: `bg-raised text-danger border-2 border-coral hover:bg-coral/10 ${LEDGE}`,
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: 'sm' | 'md' | 'lg';
  /** Icon-only button: square, no horizontal padding. */
  square?: boolean;
}

export function Button({ variant = 'subtle', size = 'md', square = false, className, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        'inline-flex items-center justify-center gap-1.5 rounded-full font-display font-semibold transition-[background-color,transform,box-shadow] duration-100 disabled:cursor-not-allowed disabled:opacity-45 select-none',
        'shrink-0',
        square
          ? size === 'sm' ? 'h-8 w-8 text-sm' : size === 'lg' ? 'h-12 w-12 text-lg' : 'h-10 w-10 text-base'
          : size === 'sm' ? 'h-8 px-3.5 text-sm' : size === 'lg' ? 'h-12 px-6 text-lg' : 'h-10 px-5 text-base',
        BUTTON[variant],
        className,
      )}
      {...rest}
    />
  );
}

interface SegmentedProps<T extends string> {
  value: T;
  options: readonly { value: T; label: ReactNode; title?: string }[];
  onChange(value: T): void;
  size?: 'sm' | 'md';
  label: string;
  className?: string;
}

export function Segmented<T extends string>({ value, options, onChange, size = 'md', label, className }: SegmentedProps<T>) {
  return (
    <div role="radiogroup" aria-label={label} className={cn('inline-flex gap-0.5 rounded-full bg-ink p-1', className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-full font-display font-semibold whitespace-nowrap transition-colors',
            size === 'sm' ? 'px-2.5 py-1 text-xs' : 'px-3.5 py-1.5 text-sm',
            o.value === value ? 'bg-raised text-text shadow-[0_2px_0_var(--color-line)]' : 'text-muted hover:text-text',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange(next: boolean): void; label: string; disabled?: boolean }) {
  return (
    <label className={cn('flex cursor-pointer items-center justify-between gap-3 text-sm', disabled && 'cursor-not-allowed opacity-50')}>
      <span>{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn('relative h-6 w-11 shrink-0 rounded-full border-2 border-text transition-colors', checked ? 'bg-leaf' : 'bg-ink')}
      >
        {/* The knob's side says on or off as well as the mint fill. */}
        <span className={cn('absolute top-0.5 h-4 w-4 rounded-full border-2 border-text bg-raised transition-[left]', checked ? 'left-[20px]' : 'left-0.5')} />
      </button>
    </label>
  );
}

type Tone = 'neutral' | 'sun' | 'sky' | 'leaf' | 'danger';
// Chips state one fact. Butter is a caveat, mint is done or running, coral-ringed paper a failure.
// Sky is kept for "sky" callers but drawn neutral: in this language sky is the left hand only.
const TONE: Record<Tone, string> = {
  neutral: 'border-line bg-raised text-muted',
  sun: 'border-transparent bg-sun text-main',
  sky: 'border-line bg-raised text-text',
  leaf: 'border-transparent bg-leaf text-main',
  danger: 'border-coral bg-raised text-danger',
};

export function Badge({ tone = 'neutral', children, title, className }: { tone?: Tone; children: ReactNode; title?: string; className?: string }) {
  return (
    <span title={title} className={cn('inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs leading-5 font-semibold whitespace-nowrap', TONE[tone], className)}>
      {children}
    </span>
  );
}

const DOT: Record<Tone, string> = { neutral: 'bg-white/50', sun: 'bg-sun', sky: 'bg-sky', leaf: 'bg-leaf', danger: 'bg-coral' };

/** A badge over the house: glass, with its tone as a dot. */
export function GlassBadge({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={cn('inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold whitespace-nowrap', GLASS)}>
      <i aria-hidden className={cn('h-2 w-2 rounded-full', DOT[tone])} />
      {children}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-muted">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-main" />
      {label}
    </span>
  );
}

export function Notice({ tone = 'neutral', title, children, action }: { tone?: 'neutral' | 'warn' | 'error'; title?: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'note'}
      className={cn(
        // A notice that needs acting on gets an ink outline; the mark says its weight by shape too.
        'flex gap-3 rounded-[var(--radius-frame)] bg-raised px-4 py-3 text-sm text-text',
        tone === 'neutral' ? 'border border-line' : 'border-2 border-text',
      )}
    >
      {tone !== 'neutral' && (
        <span
          aria-hidden
          className={cn(
            'mt-0.5 grid h-6 w-6 shrink-0 place-items-center font-display text-sm font-semibold text-main',
            tone === 'error' ? 'rounded-md bg-coral' : 'bg-sun pt-1 [clip-path:polygon(50%_4%,98%_94%,2%_94%)]',
          )}
        >
          {tone === 'error' ? '×' : '!'}
        </span>
      )}
      <div className="min-w-0">
        {title && <div className={cn('font-semibold', tone === 'error' && 'text-danger')}>{title}</div>}
        {children && <div className={cn(title && 'mt-0.5', 'text-[13.5px] leading-5 text-muted')}>{children}</div>}
        {action && <div className="mt-2">{action}</div>}
      </div>
    </div>
  );
}

export function Panel({ title, aside, children, className }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn('rounded-[var(--radius-card)] border border-line bg-raised', className)}>
      {(title || aside) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-[11px] font-semibold tracking-[0.08em] text-muted uppercase">{title}</h2>
          {aside}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

/** Centered state for a whole view: loading, empty or failed. */
export function ViewState({ children }: { children: ReactNode }) {
  return <div className="flex h-full min-h-40 w-full items-center justify-center p-6">{<div className="max-w-md">{children}</div>}</div>;
}
