'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { dispatch } from '@/lib/store/session';
import { speechProvider } from './provider';

/**
 * Hold-to-talk. While held, interim transcripts go out as `voice.transcript {final: false}`;
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

  const start = useCallback(() => {
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
    });
  }, []);

  const stop = useCallback(() => stopRef.current?.(), []);

  useEffect(() => () => stopRef.current?.(), []);

  return { supported, listening, error, start, stop };
}
