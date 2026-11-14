'use client';

import { useEffect } from 'react';
import { dispatch, onSessionEvent } from '@/lib/store/session';
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
    const waits = new Set<number>();
    // Words wait for the camera: while the view walks or jumps to a room, what the guide says
    // next is held, so the description is heard on arrival and not over a moving picture. (The
    // short "Walking you to the kitchen" line comes before the move starts, so it is not held.)
    let holdUntil = 0;
    let held: string[] = [];
    let pump = 0;
    const release = () => {
      window.clearTimeout(pump);
      const wait = holdUntil - performance.now();
      if (wait > 0) {
        pump = window.setTimeout(release, wait);
        return;
      }
      for (const text of held.splice(0)) voice.enqueue(text);
    };
    const say = (text: string) => {
      if (!text.trim()) return;
      held.push(text);
      release();
    };
    const off = onSessionEvent((event) => {
      // The user's own words cut the guide off. An open microphone that has heard nothing yet
      // (it sends empty transcripts while it waits) does not.
      if (event.type === 'chat.user' || (event.type === 'voice.transcript' && event.payload.text.trim() !== '')) {
        for (const id of written.keys()) muted.add(id);
        held = [];
        holdUntil = 0;
        return voice.cancelSpeech();
      }
      if (event.type === 'viewer.walk' && event.source === 'agent') {
        // As the tour plays it: 1.2 s to the first position, then 0.7 to 1.6 s for each one after.
        const n = Math.max(1, event.payload.sweep_ids.length);
        const step = Math.max(700, Math.min(1600, (event.payload.duration_s * 1000) / n));
        holdUntil = performance.now() + Math.min(15000, 1200 + (n - 1) * step);
        return;
      }
      if (event.type === 'viewer.goto' && event.source === 'agent') {
        holdUntil = Math.max(holdUntil, performance.now() + 1300);
        return;
      }
      if (event.type === 'agent.token') {
        const id = event.payload.message_id;
        if (muted.has(id)) return;
        const text = (written.get(id) ?? '') + event.payload.delta;
        written.set(id, text);
        const from = said.get(id) ?? 0;
        const end = sentenceEnd(text.slice(from));
        if (end >= MIN_PIECE) {
          say(text.slice(from, from + end));
          said.set(id, from + end);
        }
      } else if (event.type === 'agent.message') {
        const id = event.payload.message_id;
        // A reply that streamed: say its tail. One that came whole: say all of it.
        const text = written.get(id) ?? event.payload.text;
        if (!muted.has(id)) say(text.slice(said.get(id) ?? 0));
        // Tell the phones when this reply has been said to the end: one of them may be waiting
        // to open its microphone for the answer.
        const wait = window.setInterval(() => {
          if (voice.isSpeaking() || held.length) return;
          window.clearInterval(wait);
          waits.delete(wait);
          dispatch('voice.spoken', { message_id: id });
        }, 200);
        waits.add(wait);
        written.delete(id);
        said.delete(id);
        muted.delete(id);
      }
    });
    return () => {
      off();
      waits.forEach((w) => window.clearInterval(w));
      window.clearTimeout(pump);
      voice.cancelSpeech();
    };
  }, [enabled]);
}
