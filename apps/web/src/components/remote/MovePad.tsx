'use client';

import { useEffect, useRef, useState } from 'react';
import { dispatch } from '@/lib/store/session';

/** Degrees of camera turn per pixel of drag. */
const DRAG_SENSITIVITY = 0.22;
/** How often gathered look motion is sent. */
const SEND_MS = 33;
/** While the stick is held over, a step goes out this often (a hop on the big screen takes ~650 ms). */
const STEP_MS = 720;
const STICK = 132;
const KNOB = 56;
const DEADZONE = 0.35;

/**
 * Game-style controls: a thumbstick walks, a drag anywhere else looks around. Both work at once.
 * The stick sends `viewer.step` with the way to go (the house is walked in hops between scan
 * positions, so holding the stick over repeats the step); the drag sends `viewer.look` deltas,
 * yaw > 0 looks right, pitch > 0 looks up.
 */
export function MovePad({ hint }: { hint: string }) {
  const acc = useRef({ yaw: 0, pitch: 0 });
  const last = useRef<{ x: number; y: number } | null>(null);
  const stick = useRef<{ x: number; y: number } | null>(null); // -1..1, y up
  const [knob, setKnob] = useState({ x: 0, y: 0 });

  // Look motion gathers and goes out on a steady beat, so none is dropped between sends.
  useEffect(() => {
    const beat = setInterval(() => {
      const { yaw, pitch } = acc.current;
      if (Math.abs(yaw) < 0.05 && Math.abs(pitch) < 0.05) return;
      acc.current = { yaw: 0, pitch: 0 };
      dispatch('viewer.look', { yaw_delta_deg: Number(yaw.toFixed(2)), pitch_delta_deg: Number(pitch.toFixed(2)) });
    }, SEND_MS);
    return () => clearInterval(beat);
  }, []);

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
        last.current = { x: e.clientX, y: e.clientY };
      }}
      onPointerMove={(e) => {
        if (!last.current) return;
        acc.current.yaw += (e.clientX - last.current.x) * DRAG_SENSITIVITY;
        acc.current.pitch -= (e.clientY - last.current.y) * DRAG_SENSITIVITY;
        last.current = { x: e.clientX, y: e.clientY };
      }}
      onPointerUp={() => (last.current = null)}
      onPointerCancel={() => (last.current = null)}
    >
      <p className="pointer-events-none absolute inset-x-0 top-6 text-center text-sm text-muted">{hint}</p>
      <p className="pointer-events-none absolute inset-x-0 top-1/3 text-center text-xs text-faint">drag anywhere to look around</p>
      <div
        data-testid="walk-stick"
        aria-label="Walk"
        className="absolute bottom-24 left-6 rounded-full border border-line-strong/50 bg-ink/80"
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
