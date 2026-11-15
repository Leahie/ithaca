'use client';

import { useEffect, useRef } from 'react';
import { cn } from '@/components/ui';
import { IconHand } from '@/components/ui/icons';
import type { HandView } from '@/lib/hands/engine';
import { handEngine, handHint, onHandTick, useHands } from '@/lib/hands/store';

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

/**
 * A canvas to draw on in CSS pixels. Its size is measured when its box changes, not on every
 * frame: reading the layout sixty times a second, twice per canvas, was work for nothing.
 */
interface Surface {
  ctx: CanvasRenderingContext2D | null;
  w: number;
  h: number;
  /** What was last drawn, so a frame that would look the same is not drawn again. */
  drawn: string | null;
}

/** Keeps a canvas at its box's size in device pixels. Returns the surface and how to stop. */
function watchCanvas(canvas: HTMLCanvasElement): [Surface, () => void] {
  const surface: Surface = { ctx: canvas.getContext('2d'), w: 0, h: 0, drawn: null };
  const fit = () => {
    const dpr = window.devicePixelRatio || 1;
    surface.w = canvas.clientWidth;
    surface.h = canvas.clientHeight;
    const w = Math.round(surface.w * dpr);
    const h = Math.round(surface.h * dpr);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    // Sizing a canvas clears it and resets its transform.
    surface.ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
    surface.drawn = null;
  };
  fit();
  const observer = new ResizeObserver(fit);
  observer.observe(canvas);
  // A move to a screen with another pixel density changes no box.
  window.addEventListener('resize', fit);
  return [
    surface,
    () => {
      observer.disconnect();
      window.removeEventListener('resize', fit);
    },
  ];
}

/** A ring per hand over the view, tightening as the pinch closes. */
function drawCursors({ ctx, w, h }: Surface, hands: HandView[], zooming: boolean) {
  if (!ctx) return;
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

/**
 * What the cursor canvas would show, to the half pixel: when it is the same as last frame (a
 * hand held still, or no hand at all) the canvas, which covers the whole view, is left alone.
 */
function cursorKey({ w, h }: Surface, hands: HandView[], zooming: boolean): string {
  let key = zooming && hands.length === 2 ? 'z' : '';
  for (const hand of hands) {
    key += `|${hand.side}${Math.round(hand.u * w * 2)},${Math.round(hand.v * h * 2)},${hand.pinching ? 'p' : Math.round(hand.strength * 40)}${hand.stale ? 's' : ''}`;
  }
  return key;
}

/** The tracked hands drawn over the camera picture. */
function drawSkeletons({ ctx, w, h }: Surface, hands: HandView[]) {
  if (!ctx) return;
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
  none: 'Pinch a piece to pick it up',
  drag: 'Move your hand · let go to place',
  zoom: 'Turn your hands to turn it',
  turn: 'Pinch a piece to pick it up',
  forward: 'Pinch a piece to pick it up',
  back: 'Pinch a piece to pick it up',
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
  const meter = useRef<HTMLSpanElement | null>(null);

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
    const [ring, unwatchRing] = cursors.current ? watchCanvas(cursors.current) : [null, () => {}];
    const [bones, unwatchBones] = skeletons.current ? watchCanvas(skeletons.current) : [null, () => {}];
    let lastHint = '';
    let meterAt = 0;
    const off = onHandTick(() => {
      // How the tracking keeps up, a few times a second: read every frame, it would flicker.
      const now = performance.now();
      if (meter.current && now - meterAt >= 250) {
        meterAt = now;
        const { rate, modelMs, delayMs, gpuMs, on } = handEngine.stats;
        meter.current.textContent = rate ? `${on || '?'} · ${rate.toFixed(0)}/s · model ${modelMs ? modelMs.toFixed(0) : '–'} · gpu ${gpuMs ? gpuMs.toFixed(0) : '–'} · delay ${delayMs.toFixed(0)} ms` : '';
      }
      const hands = handEngine.hands;
      const zooming = handEngine.gesture === 'zoom';
      // The rings move every display frame (they glide between the tracker's results), but only
      // while a hand moves: a frame that would look the same is not drawn.
      const ringKey = ring ? cursorKey(ring, hands, zooming) : null;
      if (ring && ringKey !== ring.drawn) {
        drawCursors(ring, hands, zooming);
        ring.drawn = ringKey;
      }
      // The hands in the camera picture change only when the tracker has a new result.
      if (bones) {
        let bonesKey = `${handEngine.results}`;
        for (const hand of hands) bonesKey += `|${hand.id}${hand.side}${hand.pinching ? 'p' : ''}${hand.stale ? 's' : ''}`;
        if (bonesKey !== bones.drawn) {
          drawSkeletons(bones, hands);
          bones.drawn = bonesKey;
        }
      }
      const text = hands.length === 0 ? 'Raise a hand in front of the camera' : (handHint.text ?? HINT[handEngine.gesture]);
      if (hint.current && text !== lastHint) hint.current.textContent = lastHint = text;
    });
    return () => {
      off();
      unwatchRing();
      unwatchBones();
    };
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
              <span
                ref={meter}
                title="What runs the hand model · tracker results a second · the hand model's time per frame (ms) · how long the graphics card's queue holds anything up (ms) · from the picture to its result reaching the page (ms; the cursor's smoothing comes on top)"
                className="absolute top-1 left-1 rounded bg-black/55 px-1.5 font-mono text-[11px] leading-4 text-cream tabular-nums empty:hidden"
              />
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
