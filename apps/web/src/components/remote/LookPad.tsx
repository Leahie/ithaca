'use client';

import { useEffect, useRef, useState } from 'react';
import { Toggle } from '@/components/ui';
import { dispatch } from '@/lib/store/session';

/** Degrees of camera turn per pixel of drag. */
const DRAG_SENSITIVITY = 0.22;
/** How often gathered motion is sent. */
const SEND_MS = 33;

type OrientationCtor = typeof DeviceOrientationEvent & { requestPermission?: () => Promise<'granted' | 'denied'> };

/**
 * Look control: drag the pad, or turn the phone (gyroscope).
 * Sends `viewer.look` deltas: yaw > 0 looks right, pitch > 0 looks up.
 */
export function LookPad() {
  const acc = useRef({ yaw: 0, pitch: 0 });
  const last = useRef<{ x: number; y: number } | null>(null);
  const [gyro, setGyro] = useState(false);
  const [gyroError, setGyroError] = useState<string | null>(null);
  const [gyroSupported, setGyroSupported] = useState(false);

  useEffect(() => {
    setGyroSupported(typeof window !== 'undefined' && 'DeviceOrientationEvent' in window);
  }, []);

  // Motion gathers here and goes out on a steady beat, so no movement is dropped between sends
  // and the big screen gets evenly spaced, evenly sized turns.
  const push = (yaw: number, pitch: number) => {
    acc.current.yaw += yaw;
    acc.current.pitch += pitch;
  };
  useEffect(() => {
    const beat = setInterval(() => {
      const { yaw, pitch } = acc.current;
      if (Math.abs(yaw) < 0.05 && Math.abs(pitch) < 0.05) return;
      acc.current = { yaw: 0, pitch: 0 };
      dispatch('viewer.look', { yaw_delta_deg: Number(yaw.toFixed(2)), pitch_delta_deg: Number(pitch.toFixed(2)) });
    }, SEND_MS);
    return () => clearInterval(beat);
  }, []);

  useEffect(() => {
    if (!gyro) return;
    let prev: { alpha: number; beta: number } | null = null;
    const onOrientation = (e: DeviceOrientationEvent) => {
      if (e.alpha === null || e.beta === null) return;
      if (prev) {
        let dAlpha = e.alpha - prev.alpha;
        if (dAlpha > 180) dAlpha -= 360;
        if (dAlpha < -180) dAlpha += 360;
        const dBeta = e.beta - prev.beta;
        // alpha grows counter-clockwise: turning the phone right is a negative change.
        if (Math.abs(dAlpha) < 30 && Math.abs(dBeta) < 30) push(-dAlpha, dBeta);
      }
      prev = { alpha: e.alpha, beta: e.beta };
    };
    window.addEventListener('deviceorientation', onOrientation);
    return () => window.removeEventListener('deviceorientation', onOrientation);
  }, [gyro]);

  const toggleGyro = async (on: boolean) => {
    setGyroError(null);
    if (!on) return setGyro(false);
    try {
      const Ctor = window.DeviceOrientationEvent as OrientationCtor;
      // iOS asks for permission, and only from a user gesture on HTTPS.
      if (typeof Ctor.requestPermission === 'function' && (await Ctor.requestPermission()) !== 'granted') {
        setGyroError('Motion access was denied.');
        return;
      }
      setGyro(true);
    } catch {
      setGyroError('Motion sensors need HTTPS and your permission.');
    }
  };

  return (
    <div>
      <div
        role="application"
        aria-label="Look pad. Drag to look around."
        data-testid="look-pad"
        className="flex h-40 touch-none items-center justify-center rounded-xl border border-line-strong bg-[radial-gradient(circle_at_center,var(--color-raised),var(--color-ink))] text-xs text-faint select-none"
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          last.current = { x: e.clientX, y: e.clientY };
        }}
        onPointerMove={(e) => {
          if (!last.current) return;
          push((e.clientX - last.current.x) * DRAG_SENSITIVITY, -(e.clientY - last.current.y) * DRAG_SENSITIVITY);
          last.current = { x: e.clientX, y: e.clientY };
        }}
        onPointerUp={() => (last.current = null)}
        onPointerCancel={() => (last.current = null)}
      >
        drag to look around
      </div>
      <div className="mt-3">
        <Toggle label="Turn the phone to look" checked={gyro} disabled={!gyroSupported} onChange={(on) => void toggleGyro(on)} />
        {gyroError && <p className="mt-1 text-xs text-danger">{gyroError}</p>}
      </div>
    </div>
  );
}
