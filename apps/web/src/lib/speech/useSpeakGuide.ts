'use client';

import { useEffect } from 'react';
import { onSessionEvent } from '@/lib/store/session';

/** Markdown and symbols read badly aloud. */
function plain(text: string): string {
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/[*_`#>|]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

export const canSpeak = () => typeof window !== 'undefined' && 'speechSynthesis' in window;

/**
 * The guide speaks: each finished reply is read aloud by the browser's own voice (no service, no
 * key). A new reply, or the user starting to talk or type, cuts the current one off. Browsers
 * only allow speech after a click on the page.
 */
export function useSpeakGuide(enabled: boolean): void {
  useEffect(() => {
    if (!enabled || !canSpeak()) return;
    const synth = window.speechSynthesis;
    const off = onSessionEvent((event) => {
      if (event.type === 'chat.user' || event.type === 'voice.transcript') return synth.cancel();
      if (event.type !== 'agent.message') return;
      const text = plain(event.payload.text);
      if (!text) return;
      synth.cancel();
      const say = new SpeechSynthesisUtterance(text);
      const voices = synth.getVoices().filter((v) => v.lang.startsWith('en'));
      say.voice = voices.find((v) => /Samantha|Ava|Allison|Google US English|Jenny|Aria/.test(v.name)) ?? voices[0] ?? null;
      say.rate = 1.04;
      synth.speak(say);
    });
    return () => {
      off();
      synth.cancel();
    };
  }, [enabled]);
}
