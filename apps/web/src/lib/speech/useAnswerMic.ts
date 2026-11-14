'use client';

import { useEffect, useRef, useState } from 'react';
import { onSessionEvent, useSession } from '@/lib/store/session';
import { speechProvider } from './provider';

/** A reply that ends by asking something. */
export const asksSomething = (text: string) => /\?["')\]*_\s]*$/.test(text.trim());

/** With no big screen saying the reply, wait about as long as reading it aloud would take. */
const readingMs = (text: string) => Math.min(15000, 1200 + text.length * 65);

/**
 * The phone answers without a tap. When this phone's guide ends a reply with a question, the
 * microphone opens by itself once the big screen has finished saying it (`voice.spoken`), and
 * closes when the user stops talking. It only does so while the microphone is still open from an
 * earlier tap (a phone will not start audio without one), and never after the user has moved on
 * (typed, talked, or started another turn).
 */
export function useAnswerMic({ enabled, memberId, listening, start }: { enabled: boolean; memberId: string | null; listening: boolean; start(auto: boolean): void }): boolean {
  const [waiting, setWaiting] = useState(false);
  const live = useRef({ enabled, listening, start });
  live.current = { enabled, listening, start };

  useEffect(() => {
    let pending: string | null = null;
    let timer = 0;
    const drop = () => {
      pending = null;
      window.clearTimeout(timer);
      setWaiting(false);
    };
    // If the microphone opened by itself and the user said nothing, they did not want to answer:
    // it stays shut after later questions until they tap the mic or type again.
    let unanswered = false;
    let openedHere = false;
    const open = () => {
      const { enabled: on, listening: busy, start: go } = live.current;
      drop();
      if (on && !busy && !unanswered && speechProvider().canListenUntapped()) {
        openedHere = true;
        go(true);
      }
    };
    const off = onSessionEvent((event) => {
      const mine = (event.member_id ?? null) === memberId;
      if (event.type === 'voice.transcript' && event.payload.final && mine) {
        if (openedHere && event.payload.text.trim() === '') unanswered = true;
        else if (event.payload.text.trim() !== '') unanswered = false;
        openedHere = false;
      }
      if (event.type === 'chat.user' && mine) unanswered = false;
      if (event.type === 'chat.user' || (event.type === 'voice.transcript' && event.payload.text.trim() !== '')) return drop();
      if (event.type === 'agent.message' && mine) {
        drop();
        if (!live.current.enabled || !asksSomething(event.payload.text) || !speechProvider().canListenUntapped()) return;
        pending = event.payload.message_id;
        setWaiting(true);
        // The big screen says when it has finished speaking, and only then does the microphone
        // open: guessing would open it over the guide's own voice. With no big screen there is
        // no voice to wait for. If the signal never comes, the user taps as usual.
        if (!useSession.getState().peers.desktop) timer = window.setTimeout(open, 400);
        else timer = window.setTimeout(drop, readingMs(event.payload.text) + 20000);
      } else if (event.type === 'voice.spoken' && pending === event.payload.message_id) {
        window.clearTimeout(timer);
        timer = window.setTimeout(open, 500); // let the room go quiet
      }
    });
    return () => {
      off();
      window.clearTimeout(timer);
    };
  }, [memberId]);

  return waiting;
}
