'use client';

import { useEffect, useRef, useState } from 'react';
import { dispatch } from '@/lib/store/session';

/** Degrees of camera turn per pixel of a slow drag. */
const DRAG_SENSITIVITY = 0.3;
/** A quick flick turns further than a slow drag of the same length, as a trackpad does. */
const ACCEL_PER_PX = 0.035;
const ACCEL_MAX = 2.4;
/** After the finger lifts, the view glides on: the share of speed kept each 16 ms. */
const GLIDE = 0.9;
/** Zoom levels per doubling of the distance between two fingers. */
const PINCH_GAIN = 1.4;
/** How often gathered look motion is sent. */
const SEND_MS = 20;
/** While the stick is held over, a step goes out this often (a hop on the big screen takes ~650 ms). */
const STEP_MS = 720;
const STICK = 132;
const KNOB = 56;
const DEADZONE = 0.35;

/**
 * Game-style controls: a thumbstick at the bottom middle walks, a drag anywhere else looks around with a trackpad's feel (quick flicks go
 * further, and the view glides on after the finger lifts), and a two-finger pinch zooms.
 * The stick sends `viewer.step` with the way to go (the house is walked in hops between scan
 * positions, so holding the stick over repeats the step); the drag sends `viewer.look` deltas,
 * yaw > 0 looks right, pitch > 0 looks up; the pinch sends `viewer.zoom`.
 */
export function MovePad({ hint }: { hint?: string }) {
  const acc = useRef({ yaw: 0, pitch: 0, zoom: 0 });
  // Fingers on the look surface: one drags the view, two pinch to zoom.
  const fingers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<number | null>(null);
  const speed = useRef({ yaw: 0, pitch: 0 }); // degrees per 16 ms, for the glide
  const glide = useRef<number | null>(null);
  const stick = useRef<{ x: number; y: number } | null>(null); // -1..1, y up
  const [knob, setKnob] = useState({ x: 0, y: 0 });

  // Motion gathers and goes out on a steady beat, so none is dropped between sends.
  useEffect(() => {
    const beat = setInterval(() => {
      const { yaw, pitch, zoom } = acc.current;
      if (Math.abs(yaw) >= 0.05 || Math.abs(pitch) >= 0.05) {
        acc.current.yaw = acc.current.pitch = 0;
        dispatch('viewer.look', { yaw_delta_deg: Number(yaw.toFixed(2)), pitch_delta_deg: Number(pitch.toFixed(2)) });
      }
      if (Math.abs(zoom) >= 0.01) {
        acc.current.zoom = 0;
        dispatch('viewer.zoom', { delta: Number(zoom.toFixed(3)) });
      }
    }, SEND_MS);
    return () => {
      clearInterval(beat);
      if (glide.current) cancelAnimationFrame(glide.current);
    };
  }, []);

  const stopGlide = () => {
    if (glide.current) cancelAnimationFrame(glide.current);
    glide.current = null;
  };
  const startGlide = () => {
    const run = () => {
      speed.current.yaw *= GLIDE;
      speed.current.pitch *= GLIDE;
      if (Math.hypot(speed.current.yaw, speed.current.pitch) < 0.08) return stopGlide();
      acc.current.yaw += speed.current.yaw;
      acc.current.pitch += speed.current.pitch;
      glide.current = requestAnimationFrame(run);
    };
    stopGlide();
    glide.current = requestAnimationFrame(run);
  };
  const spread = () => {
    const [a, b] = [...fingers.current.values()];
    return a && b ? Math.hypot(a.x - b.x, a.y - b.y) : null;
  };

  // Walking: one step at once when the stick leaves the middle, then one per beat while it is held.
  const walking = useRef<ReturnType<typeof setInterval> | null>(null);
  const step = () => {
    const s = stick.current;
    if (!s || Math.hypot(s.x, s.y) < DEADZONE) return;
    dispatch('viewer.step', { direction: 'forward', angle_deg: Math.round((Math.atan2(s.x, s.y) * 180) / Math.PI) });
  };
  const stopWalking = () => {
    if (walking.current) clearInterval(walking.current);
    walking.current = null;
    stick.current = null;
    setKnob({ x: 0, y: 0 });
  };
  useEffect(() => stopWalking, []);

  const moveStick = (e: React.PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const reach = (STICK - KNOB) / 2;
    let x = e.clientX - box.left - box.width / 2;
    let y = e.clientY - box.top - box.height / 2;
    const len = Math.hypot(x, y);
    if (len > reach) {
      x *= reach / len;
      y *= reach / len;
    }
    const was = stick.current ? Math.hypot(stick.current.x, stick.current.y) >= DEADZONE : false;
    stick.current = { x: x / reach, y: -y / reach };
    setKnob({ x, y });
    const is = Math.hypot(stick.current.x, stick.current.y) >= DEADZONE;
    if (is && !was) {
      step();
      if (walking.current) clearInterval(walking.current);
      walking.current = setInterval(step, STEP_MS);
    }
  };

  return (
    <div
      role="application"
      aria-label="Drag to look around. Use the stick to walk."
      data-testid="look-pad"
      className="relative h-full w-full touch-none select-none"
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        stopGlide();
        speed.current = { yaw: 0, pitch: 0 };
        fingers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
        pinch.current = spread();
      }}
      onPointerMove={(e) => {
        const was = fingers.current.get(e.pointerId);
        if (!was) return;
        fingers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (fingers.current.size >= 2) {
          const now = spread();
          if (now && pinch.current) acc.current.zoom += Math.log2(now / pinch.current) * PINCH_GAIN;
          pinch.current = now;
          return;
        }
        const dx = e.clientX - was.x;
        const dy = e.clientY - was.y;
        const gain = DRAG_SENSITIVITY * Math.min(ACCEL_MAX, 1 + Math.hypot(dx, dy) * ACCEL_PER_PX);
        // The view follows the finger, like dragging the scene: swiping down looks up, swiping
        // right looks left.
        const yaw = -dx * gain;
        const pitch = dy * gain;
        acc.current.yaw += yaw;
        acc.current.pitch += pitch;
        speed.current = { yaw: speed.current.yaw * 0.5 + yaw * 0.5, pitch: speed.current.pitch * 0.5 + pitch * 0.5 };
      }}
      onPointerUp={(e) => {
        const pinching = fingers.current.size >= 2;
        fingers.current.delete(e.pointerId);
        pinch.current = null;
        if (!pinching && fingers.current.size === 0) startGlide();
      }}
      onPointerCancel={(e) => {
        fingers.current.delete(e.pointerId);
        pinch.current = null;
      }}
    >
      {hint && <p className="pointer-events-none absolute inset-x-0 top-6 text-center text-sm text-muted">{hint}</p>}
      <p className="pointer-events-none absolute inset-x-0 top-1/3 text-center text-xs text-faint">drag to look around · pinch to zoom</p>
      <div
        data-testid="walk-stick"
        aria-label="Walk"
        className="absolute bottom-8 left-1/2 -translate-x-1/2 rounded-full border border-line-strong/50 bg-ink/80"
        style={{ width: STICK, height: STICK }}
        onPointerDown={(e) => {
          e.stopPropagation();
          e.currentTarget.setPointerCapture(e.pointerId);
          moveStick(e);
        }}
        onPointerMove={(e) => {
          e.stopPropagation();
          if (stick.current) moveStick(e);
        }}
        onPointerUp={(e) => {
          e.stopPropagation();
          stopWalking();
        }}
        onPointerCancel={stopWalking}
      >
        <div
          className="pointer-events-none absolute rounded-full bg-main shadow-float"
          style={{ width: KNOB, height: KNOB, left: (STICK - KNOB) / 2 + knob.x, top: (STICK - KNOB) / 2 + knob.y }}
        />
      </div>
    </div>
  );
}
