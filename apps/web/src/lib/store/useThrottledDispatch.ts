'use client';

import type { EventPayloads, EventType } from '@ithaca/schema';
import { useCallback, useEffect, useRef } from 'react';
import { dispatch } from './session';

/**
 * Dispatch at most once per `intervalMs`, always sending the latest value (trailing call).
 * For sliders and joysticks: the store updates smoothly without flooding the session.
 */
export function useThrottledDispatch<T extends EventType>(type: T, intervalMs = 70): (payload: EventPayloads[T]) => void {
  const last = useRef(0);
  const pending = useRef<EventPayloads[T] | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  return useCallback(
    (payload: EventPayloads[T]) => {
      const now = performance.now();
      const wait = intervalMs - (now - last.current);
      if (wait <= 0) {
        last.current = now;
        dispatch(type, payload);
        return;
      }
      pending.current = payload;
      timer.current ??= setTimeout(() => {
        timer.current = null;
        last.current = performance.now();
        if (pending.current) dispatch(type, pending.current);
        pending.current = null;
      }, wait);
    },
    [type, intervalMs],
  );
}
