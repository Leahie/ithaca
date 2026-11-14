'use client';

import { useEffect } from 'react';
import { onSessionEvent } from '@/lib/store/session';
import { speechProvider } from './provider';

export const canSpeak = () => speechProvider().ttsAvailable();

/**
 * The guide speaks: each finished reply is read aloud by the speech provider (the browser's own
 * voice, or Deepgram's through the API). A new reply, or the user starting to talk or type, cuts
 * the current one off. Browsers only allow speech after a click on the page.
 */
export function useSpeakGuide(enabled: boolean): void {
  useEffect(() => {
    if (!enabled || !canSpeak()) return;
    const voice = speechProvider();
    const off = onSessionEvent((event) => {
      if (event.type === 'chat.user' || event.type === 'voice.transcript') return voice.cancelSpeech();
      if (event.type === 'agent.message') voice.speak(event.payload.text);
    });
    return () => {
      off();
      voice.cancelSpeech();
    };
  }, [enabled]);
}
