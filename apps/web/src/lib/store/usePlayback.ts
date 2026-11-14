'use client';

import { useEffect } from 'react';
import { setVoiceKind, voiceKind } from '@/lib/speech/provider';
import { minutesOfDay, withTimeOfDay } from '@/lib/time';
import { dispatch, onSessionEvent, useSession } from './session';
import { useThrottledDispatch } from './useThrottledDispatch';

/** A day in 36 s: 4 minutes of day per 100 ms tick. */
const STEP_MIN = 4;
const TICK_MS = 100;

/** Play or pause the day on the big screen, from any screen in the session. */
export function togglePlaying(): void {
  dispatch('viewer.playback', { playing: !useSession.getState().viewer.playback.playing });
}

/** Turn the big screen's outside noise on or off, from any screen in the session. */
export function toggleListening(): void {
  dispatch('viewer.playback', { listening: !useSession.getState().viewer.playback.listening });
}

/**
 * The big screen's side of `viewer.playback`. It is the one clock: while `playing` (whoever
 * pressed play) it moves the day on with `viewer.setTime`, which every screen follows. It also
 * keeps the guide's voice: the one this screen used last goes into a new session, and a choice
 * made on a phone is remembered here for next time.
 */
export function useDesktopPlayback(timezone: string | undefined): void {
  const playing = useSession((s) => s.viewer.playback.playing);
  const voice = useSession((s) => s.viewer.playback.voice);
  const setTime = useThrottledDispatch('viewer.setTime', 70);

  useEffect(() => {
    if (!playing || !timezone) return;
    const t = setInterval(() => {
      const now = useSession.getState().viewer.datetime;
      setTime({ datetime: withTimeOfDay(now, timezone, (minutesOfDay(now, timezone) + STEP_MIN) % 1440) });
    }, TICK_MS);
    return () => clearInterval(t);
  }, [playing, timezone, setTime]);

  useEffect(
    () =>
      onSessionEvent((event, origin) => {
        if (event.type !== 'session.state' || origin !== 'remote') return;
        const saved = voiceKind();
        if (saved !== useSession.getState().viewer.playback.voice) dispatch('viewer.playback', { voice: saved });
      }),
    [],
  );

  useEffect(() => {
    if (useSession.getState().status === 'open') setVoiceKind(voice);
  }, [voice]);
}
