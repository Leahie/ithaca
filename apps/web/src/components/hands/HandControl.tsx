'use client';

import { useEffect, useRef } from 'react';
import { cn } from '@/components/ui';
import { IconHand } from '@/components/ui/icons';
import type { HandView } from '@/lib/hands/engine';
import { handEngine, onHandTick, useHands } from '@/lib/hands/store';

/** The user's left hand is sky blue and the right coral, as in HoloTouch. */
const COLOR = { L: '#6fb6e8', R: '#ef8a6f' } as const;

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
        'inline-flex h-9 items-center gap-1.5 rounded-md px-3 text-sm transition-colors',
        on ? 'bg-sky/15 text-sky hover:bg-sky/25' : status === 'error' ? 'text-danger hover:bg-raised' : 'text-muted hover:bg-raised hover:text-text',
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
    ctx.strokeStyle = 'rgba(236,231,223,0.55)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(a[0], a[1]);
    ctx.lineTo(b[0], b[1]);
    ctx.stroke();
    ctx.setLineDash([]);
  }

  for (const hand of hands) {
    const [x, y] = at(hand);
    const color = COLOR[hand.side];
    const r = hand.pinching ? 11 : 26 - 12 * hand.strength;
    ctx.globalAlpha = hand.stale ? 0.4 : 1;
    // Ink halo first, so the ring reads over light and dark scenes alike.
    ctx.lineWidth = 6;
    ctx.strokeStyle = 'rgba(14,13,11,0.45)';
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.lineWidth = 2.5;
    ctx.strokeStyle = color;
    ctx.setLineDash(hand.stale ? [4, 4] : []);
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.stroke();
    ctx.setLineDash([]);
    if (hand.pinching) {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(x, y, 4.5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.font = '600 10px ui-sans-serif, system-ui, sans-serif';
    ctx.fillStyle = color;
    ctx.fillText(hand.side, x + r + 5, y - r + 4);
    ctx.globalAlpha = 1;
  }
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
    ctx.fillStyle = hand.pinching ? '#ece7df' : COLOR[hand.side];
    for (const i of [4, 8]) {
      ctx.beginPath();
      ctx.arc(p[i]!.x * w, p[i]!.y * h, hand.pinching ? 3.5 : 2.5, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}

const HINT = {
  none: 'Pinch thumb and index to grab the view',
  drag: 'Dragging the view',
  zoom: 'Spread hands to zoom in, bring together to zoom out',
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
      <div className="absolute bottom-3 left-3 z-30 w-60 overflow-hidden rounded-lg border border-line bg-ink/90 shadow-lg" data-testid="hands-preview">
        {status === 'error' ? (
          <div className="space-y-2 p-3 text-xs">
            <p className="font-medium text-danger">Hand control could not start</p>
            <p className="text-muted">{error}</p>
            <button type="button" onClick={stop} className="text-muted underline hover:text-text">
              Dismiss
            </button>
          </div>
        ) : (
          <>
            <div ref={preview} className="relative aspect-video w-full bg-black">
              <canvas ref={skeletons} className="absolute inset-0 h-full w-full" aria-hidden />
              {status === 'starting' && <span className="absolute inset-0 grid place-items-center text-xs text-muted">Starting the camera…</span>}
            </div>
            <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 text-[11px] text-muted">
              <span ref={hint}>{status === 'starting' ? 'Loading hand tracking…' : ''}</span>
              <button type="button" onClick={stop} className="shrink-0 hover:text-text" aria-label="Turn hand control off">
                Off
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
