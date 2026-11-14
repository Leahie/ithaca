'use client';

import { useEffect } from 'react';
import { onSessionEvent } from '@/lib/store/session';
import { speechProvider } from './provider';

export const canSpeak = () => speechProvider().ttsAvailable();

/** Where the last whole sentence in `text` ends (just past its closing mark), or 0. */
export function sentenceEnd(text: string): number {
  let end = 0;
  for (const m of text.matchAll(/[.!?]["')\]]*\s/g)) {
    // "3.5 m" and "1. First" are not sentence ends.
    if (m[0][0] === '.' && /\d$/.test(text.slice(0, m.index))) continue;
    end = m.index + m[0].length;
  }
  return end;
}

/** A first piece shorter than this waits for the next sentence: tiny clips sound choppy. */
const MIN_PIECE = 24;

/**
 * The guide speaks, and starts as soon as it has a sentence: a reply arrives word by word
 * (`agent.token`), every finished sentence goes to the speech provider's queue, and the end of the
 * reply (`agent.message`) sends what is left. The user starting to talk or type cuts it off, and
 * the rest of that reply stays unsaid. Browsers only allow speech after a click on the page.
 */
export function useSpeakGuide(enabled: boolean): void {
  useEffect(() => {
    if (!enabled || !canSpeak()) return;
    const voice = speechProvider();
    const written = new Map<string, string>(); // message_id -> text so far
    const said = new Map<string, number>(); // message_id -> characters already sent to the voice
    const muted = new Set<string>(); // replies the user talked over
    const off = onSessionEvent((event) => {
      if (event.type === 'chat.user' || event.type === 'voice.transcript') {
        for (const id of written.keys()) muted.add(id);
        return voice.cancelSpeech();
      }
      if (event.type === 'agent.token') {
        const id = event.payload.message_id;
        if (muted.has(id)) return;
        const text = (written.get(id) ?? '') + event.payload.delta;
        written.set(id, text);
        const from = said.get(id) ?? 0;
        const end = sentenceEnd(text.slice(from));
        if (end >= MIN_PIECE) {
          voice.enqueue(text.slice(from, from + end));
          said.set(id, from + end);
        }
      } else if (event.type === 'agent.message') {
        const id = event.payload.message_id;
        // A reply that streamed: say its tail. One that came whole: say all of it.
        const text = written.get(id) ?? event.payload.text;
        if (!muted.has(id)) voice.enqueue(text.slice(said.get(id) ?? 0));
        written.delete(id);
        said.delete(id);
        muted.delete(id);
      }
    });
    return () => {
      off();
      voice.cancelSpeech();
    };
  }, [enabled]);
}
