'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { dispatch } from '@/lib/store/session';
import { speechProvider } from './provider';

/**
 * Talking to the guide: hold (`start` / `stop`) or tap (`toggle`, which ends by itself at a pause).
 * While listening, interim transcripts go out as `voice.transcript {final: false}`;
 * on release the final one goes out with `final: true`, which starts an agent turn.
 */
export function usePushToTalk() {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [supported, setSupported] = useState(false);
  const stopRef = useRef<(() => void) | null>(null);
  const lastSent = useRef(0);

  useEffect(() => {
    setSupported(speechProvider().sttAvailable());
  }, []);

  const start = useCallback((auto = false) => {
    if (stopRef.current) return;
    setError(null);
    setListening(true);
    speechProvider().cancelSpeech();
    stopRef.current = speechProvider().listen({
      onTranscript(text, final) {
        if (final) {
          stopRef.current = null;
          setListening(false);
          dispatch('voice.transcript', { text, final: true });
          return;
        }
        const now = performance.now();
        if (now - lastSent.current > 250) {
          lastSent.current = now;
          dispatch('voice.transcript', { text, final: false });
        }
      },
      onError(message) {
        stopRef.current = null;
        setListening(false);
        setError(message);
        dispatch('voice.transcript', { text: '', final: true });
      },
    }, { auto });
  }, []);

  const stop = useCallback(() => stopRef.current?.(), []);
  /** Tap to talk: one tap starts, and it stops by itself when you pause. A second tap stops it now. */
  const toggle = useCallback(() => (stopRef.current ? stopRef.current() : start(true)), [start]);

  useEffect(() => () => stopRef.current?.(), []);

  return { supported, listening, error, start, stop, toggle };
}
