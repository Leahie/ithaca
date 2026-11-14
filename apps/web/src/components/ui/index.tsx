'use client';

import type { ButtonHTMLAttributes, ReactNode } from 'react';

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

type ButtonVariant = 'primary' | 'subtle' | 'ghost' | 'danger';

const BUTTON: Record<ButtonVariant, string> = {
  primary: 'bg-sun text-ink hover:bg-sun/90 font-medium',
  subtle: 'bg-raised text-text border border-line hover:border-line-strong',
  ghost: 'text-muted hover:text-text hover:bg-raised',
  danger: 'bg-danger/15 text-danger border border-danger/30 hover:bg-danger/25',
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
        'inline-flex items-center justify-center gap-1.5 rounded-md transition-colors disabled:cursor-not-allowed disabled:opacity-45 select-none',
        'shrink-0',
        square
          ? size === 'sm' ? 'h-7 w-7 text-xs' : size === 'lg' ? 'h-12 w-12 text-base' : 'h-9 w-9 text-sm'
          : size === 'sm' ? 'h-7 px-2.5 text-xs' : size === 'lg' ? 'h-12 px-5 text-base' : 'h-9 px-3.5 text-sm',
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
    <div role="radiogroup" aria-label={label} className={cn('inline-flex rounded-lg border border-line bg-surface p-0.5', className)}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          title={o.title}
          onClick={() => onChange(o.value)}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-md whitespace-nowrap transition-colors',
            size === 'sm' ? 'px-2 py-1 text-xs' : 'px-3 py-1.5 text-sm',
            o.value === value ? 'bg-raised text-text shadow-[inset_0_0_0_1px_var(--color-line-strong)]' : 'text-muted hover:text-text',
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
        className={cn('relative h-5 w-9 shrink-0 rounded-full transition-colors', checked ? 'bg-sun' : 'bg-line-strong')}
      >
        <span className={cn('absolute top-0.5 h-4 w-4 rounded-full bg-ink transition-[left]', checked ? 'left-[18px]' : 'left-0.5')} />
      </button>
    </label>
  );
}

type Tone = 'neutral' | 'sun' | 'sky' | 'leaf' | 'danger';
const TONE: Record<Tone, string> = {
  neutral: 'border-line text-muted',
  sun: 'border-sun/40 text-sun bg-sun/10',
  sky: 'border-sky/40 text-sky bg-sky/10',
  leaf: 'border-leaf/40 text-leaf bg-leaf/10',
  danger: 'border-danger/40 text-danger bg-danger/10',
};

export function Badge({ tone = 'neutral', children, title, className }: { tone?: Tone; children: ReactNode; title?: string; className?: string }) {
  return (
    <span title={title} className={cn('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] leading-4 whitespace-nowrap', TONE[tone], className)}>
      {children}
    </span>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-muted">
      <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line-strong border-t-sun" />
      {label}
    </span>
  );
}

export function Notice({ tone = 'neutral', title, children, action }: { tone?: 'neutral' | 'warn' | 'error'; title?: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div
      role={tone === 'error' ? 'alert' : 'note'}
      className={cn(
        'rounded-lg border px-3 py-2.5 text-sm',
        tone === 'error' ? 'border-danger/40 bg-danger/10 text-danger' : tone === 'warn' ? 'border-sun/35 bg-sun/10 text-sun' : 'border-line bg-surface text-muted',
      )}
    >
      {title && <div className="font-medium">{title}</div>}
      {children && <div className={cn(title && 'mt-0.5', 'text-[13px] leading-5 opacity-90')}>{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Panel({ title, aside, children, className }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn('rounded-xl border border-line bg-surface', className)}>
      {(title || aside) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-2.5">
          <h2 className="text-xs font-semibold tracking-wide text-muted uppercase">{title}</h2>
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
