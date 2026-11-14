'use client';

import { useEffect, useRef, useState } from 'react';
import { cn } from '@/components/ui';
import { dispatch } from '@/lib/store/session';

/** How fast the view turns with the stick pushed all the way, in degrees per second. */
const TURN_DEG_PER_S = { yaw: 110, pitch: 60 };
/** How often a turn goes out while the stick is held. */
const SEND_MS = 40;
/** The Move screen's walk stick, a little smaller. */
const SIZE = 116;
const KNOB = 50;
const DEADZONE = 0.12;

/**
 * A stick that turns the big screen's camera, drawn like the Move screen's walk stick: push it and
 * the view keeps turning that way, faster the further it is pushed; let go and it springs back.
 * Right turns right, up looks up. Sends `viewer.look`.
 */
export function LookStick({ className }: { className?: string }) {
  const push = useRef<{ x: number; y: number } | null>(null);
  const [knob, setKnob] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const beat = setInterval(() => {
      const p = push.current;
      if (!p || Math.hypot(p.x, p.y) < DEADZONE) return;
      const s = SEND_MS / 1000;
      dispatch('viewer.look', { yaw_delta_deg: Number((p.x * TURN_DEG_PER_S.yaw * s).toFixed(2)), pitch_delta_deg: Number((p.y * TURN_DEG_PER_S.pitch * s).toFixed(2)) });
    }, SEND_MS);
    return () => clearInterval(beat);
  }, []);

  const move = (e: React.PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const reach = (SIZE - KNOB) / 2;
    let x = e.clientX - box.left - box.width / 2;
    let y = e.clientY - box.top - box.height / 2;
    const len = Math.hypot(x, y);
    if (len > reach) {
      x *= reach / len;
      y *= reach / len;
    }
    push.current = { x: x / reach, y: -y / reach };
    setKnob({ x, y });
  };
  const release = () => {
    push.current = null;
    setKnob({ x: 0, y: 0 });
  };

  return (
    <div
      role="application"
      aria-label="Turn the big screen: push the stick the way to look"
      title="Push to turn the big screen"
      data-testid="look-stick"
      className={cn('relative shrink-0 touch-none rounded-full border border-line-strong/50 bg-ink/80 select-none', className)}
      style={{ width: SIZE, height: SIZE }}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId);
        move(e);
      }}
      onPointerMove={(e) => push.current && move(e)}
      onPointerUp={release}
      onPointerCancel={release}
    >
      <div
        className={cn('pointer-events-none absolute rounded-full bg-main shadow-float transition-transform', push.current ? 'duration-0' : 'duration-150')}
        style={{ width: KNOB, height: KNOB, left: (SIZE - KNOB) / 2, top: (SIZE - KNOB) / 2, transform: `translate(${knob.x}px, ${knob.y}px)` }}
      />
    </div>
  );
}
