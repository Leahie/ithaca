'use client';

import { useEffect, useRef } from 'react';
import { cn } from '@/components/ui';
import { IconHand } from '@/components/ui/icons';
import type { HandView } from '@/lib/hands/engine';
import { handEngine, onHandTick, useHands } from '@/lib/hands/store';

/** The user's left hand is sky blue and the right coral, as in HoloTouch. */
const COLOR = { L: '#4aa8ff', R: '#ff6b5c' } as const;
const CREAM = '#f6f9f7';
const INK = '#06261f';

/** Bones of the 21-point MediaPipe hand. */
const BONES: [number, number][] = [
  [0, 1], [1, 2], [2, 3], [3, 4],
  [0, 5], [5, 6], [6, 7], [7, 8],
  [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16],
  [13, 17], [0, 17], [17, 18], [18, 19], [19, 20],
];

/** Header button: turns hand tracking (and the camera) on and off. */
export function HandsButton() {
  const status = useHands((s) => s.status);
  const error = useHands((s) => s.error);
  const toggle = useHands((s) => s.toggle);
  const on = status === 'on' || status === 'starting';
  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={on}
      title={status === 'error' ? (error ?? undefined) : on ? 'Stop hand control and turn the camera off' : 'Control the view with your hands, through the camera'}
      className={cn(
        'inline-flex h-10 items-center gap-1.5 rounded-full px-3.5 font-display font-semibold transition-colors',
        // Switched on is mint, as everywhere in the language.
        on ? 'bg-leaf text-main hover:bg-leaf/85' : status === 'error' ? 'text-danger hover:bg-ink' : 'text-muted hover:bg-ink hover:text-text',
      )}
    >
      <IconHand />
      <span className="hidden xl:inline">{status === 'starting' ? 'Starting…' : on ? 'Hands on' : 'Hands'}</span>
    </button>
  );
}

function fitCanvas(canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(canvas.clientWidth * dpr);
  const h = Math.round(canvas.clientHeight * dpr);
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
  const ctx = canvas.getContext('2d');
  ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
  return ctx;
}

/** A ring per hand over the view, tightening as the pinch closes. */
function drawCursors(canvas: HTMLCanvasElement, hands: HandView[], zooming: boolean) {
  const ctx = fitCanvas(canvas);
  if (!ctx) return;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  ctx.clearRect(0, 0, w, h);
  const at = (hand: HandView): [number, number] => [Math.min(Math.max(hand.u, -0.02), 1.02) * w, Math.min(Math.max(hand.v, -0.02), 1.02) * h];

  if (zooming && hands.length === 2) {
    const [a, b] = hands.map(at) as [[number, number], [number, number]];
    ctx.setLineDash([6, 6]);
    ctx.strokeStyle = CREAM;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  for (const hand of hands) {
    const [x, y] = at(hand);
    const color = COLOR[hand.side];
    // Ready 28 px radius (56 px ring), closing toward holding at 24 (48 px).
    const r = hand.pinching ? 24 : 28 - 4 * hand.strength;
    ctx.globalAlpha = hand.stale ? 0.4 : 1;
    // The keyline: an ink hairline outside a cream line, then the ring in the hand's colour. It
    // reads over a bright exterior and a dark hallway alike.
    ring(ctx, x, y, r, 10.5, 'rgba(30,27,46,0.8)');
    ring(ctx, x, y, r, 8, CREAM);
    ctx.setLineDash(hand.stale ? [5, 5] : []);
    ring(ctx, x, y, r, 4, color);
    ctx.setLineDash([]);
    // The dot is the measured pinch: it grows as the fingers close and is full while holding.
    const dot = hand.pinching ? 13 : 7 * hand.strength;
    if (dot > 0.5) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, y, dot, 0, Math.PI * 2);
      ctx.fill();
    }
    // The tag: the hand's letter on its own side, Fredoka 16 px, ink on the hand's colour.
    const tx = hand.side === 'L' ? x - r - 6 : x + r + 6;
    const ty = y - r + 2;
    ctx.fillStyle = CREAM;
    ctx.beginPath();
    ctx.arc(tx, ty, 15, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(tx, ty, 13, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = INK;
    ctx.font = `500 16px ${displayFamily()}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(hand.side, tx, ty + 1);
    ctx.globalAlpha = 1;
  }
}

/** Canvas fonts cannot read CSS variables: resolve the Fredoka family next/font named. */
let family: string | null = null;
function displayFamily(): string {
  family ??= `${getComputedStyle(document.documentElement).getPropertyValue('--font-figtree').trim() || 'Figtree'}, system-ui, sans-serif`;
  return family;
}

function ring(ctx: CanvasRenderingContext2D, x: number, y: number, r: number, width: number, color: string) {
  ctx.lineWidth = width;
  ctx.strokeStyle = color;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, Math.PI * 2);
  ctx.stroke();
}

/** The tracked hands drawn over the camera picture. */
function drawSkeletons(canvas: HTMLCanvasElement, hands: HandView[]) {
  const ctx = fitCanvas(canvas);
  if (!ctx) return;
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  ctx.clearRect(0, 0, w, h);
  for (const hand of hands) {
    const p = hand.landmarks;
    if (p.length < 21) continue;
    ctx.strokeStyle = COLOR[hand.side];
    ctx.lineWidth = 1.5;
    ctx.globalAlpha = hand.stale ? 0.4 : 0.95;
    ctx.beginPath();
    for (const [a, b] of BONES) {
      ctx.moveTo(p[a]!.x * w, p[a]!.y * h);
      ctx.lineTo(p[b]!.x * w, p[b]!.y * h);
    }
    ctx.stroke();
    ctx.fillStyle = hand.pinching ? CREAM : COLOR[hand.side];
    for (const i of [4, 8]) {
      ctx.beginPath();
      ctx.arc(p[i]!.x * w, p[i]!.y * h, hand.pinching ? 3.5 : 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}

// Short, one line each: the box must not change size as the gesture changes.
const HINT = {
  none: 'Point to walk · sweep to turn',
  drag: 'Dragging the view',
  zoom: 'Zooming',
  turn: 'Turning',
  forward: 'Walking forward',
  back: 'Walking back',
} as const;

/**
 * Over the active view while hand control is on: a cursor ring for each hand, and a small
 * camera preview with the tracked hands, so the user can see what the camera sees.
 */
export function HandsOverlay() {
  const status = useHands((s) => s.status);
  const error = useHands((s) => s.error);
  const video = useHands((s) => s.video);
  const stop = useHands((s) => s.stop);
  const cursors = useRef<HTMLCanvasElement | null>(null);
  const skeletons = useRef<HTMLCanvasElement | null>(null);
  const preview = useRef<HTMLDivElement | null>(null);
  const hint = useRef<HTMLSpanElement | null>(null);

  // The camera picture is the tracker's own <video>: put it in the preview.
  useEffect(() => {
    const host = preview.current;
    if (!host || !video) return;
    video.className = 'absolute inset-0 h-full w-full -scale-x-100 object-cover';
    host.prepend(video);
    return () => video.remove();
  }, [video]);

  useEffect(() => {
    if (status !== 'on') return;
    let lastHint = '';
    return onHandTick(() => {
      const hands = handEngine.hands;
      if (cursors.current) drawCursors(cursors.current, hands, handEngine.gesture === 'zoom');
      if (skeletons.current) drawSkeletons(skeletons.current, hands);
      const text = hands.length === 0 ? 'Raise a hand in front of the camera' : HINT[handEngine.gesture];
      if (hint.current && text !== lastHint) hint.current.textContent = lastHint = text;
    });
  }, [status]);

  if (status === 'off') return null;
  return (
    <>
      <canvas ref={cursors} className="pointer-events-none absolute inset-0 z-30 h-full w-full" aria-hidden />
      <div className="absolute bottom-14 left-3 z-30 w-64 overflow-hidden rounded-[var(--radius-frame)] border border-line bg-surface shadow-float" data-testid="hands-preview">
        {status === 'error' ? (
          <div className="space-y-2 p-4 text-sm">
            <p className="font-semibold text-danger">Hand control could not start</p>
            <p className="text-[13.5px] text-muted">{error}</p>
            <button type="button" onClick={stop} className="min-h-11 font-display font-semibold text-text underline">
              Dismiss
            </button>
          </div>
        ) : (
          <>
            <div ref={preview} className="relative aspect-video w-full bg-[#26232f]">
              <canvas ref={skeletons} className="absolute inset-0 h-full w-full" aria-hidden />
              {status === 'starting' && <span className="absolute inset-0 grid place-items-center font-display text-base font-medium text-cream/80">Starting the camera…</span>}
            </div>
            <div className="flex items-center justify-between gap-2 py-1 pr-1 pl-3 font-display text-base font-medium text-text">
              <span ref={hint} className="h-5 truncate leading-5">{status === 'starting' ? 'Loading hand tracking…' : ''}</span>
              <button type="button" onClick={stop} className="h-11 min-w-11 shrink-0 rounded-full px-3 font-semibold text-muted hover:bg-ink hover:text-text" aria-label="Turn hand control off">
                Off
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
