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
    const open = () => {
      const { enabled: on, listening: busy, start: go } = live.current;
      drop();
      if (on && !busy && speechProvider().canListenUntapped()) go(true);
    };
    const off = onSessionEvent((event) => {
      const mine = (event.member_id ?? null) === memberId;
      if (event.type === 'chat.user' || (event.type === 'voice.transcript' && event.payload.text.trim() !== '')) return drop();
      if (event.type === 'agent.message' && mine) {
        drop();
        if (!live.current.enabled || !asksSomething(event.payload.text) || !speechProvider().canListenUntapped()) return;
        pending = event.payload.message_id;
        setWaiting(true);
        // The big screen says when it is done; if it is silent or absent, don't wait for ever.
        const quiet = !useSession.getState().peers.desktop;
        timer = window.setTimeout(open, quiet ? 400 : readingMs(event.payload.text) + 4000);
      } else if (event.type === 'voice.spoken' && pending === event.payload.message_id) {
        window.clearTimeout(timer);
        timer = window.setTimeout(open, 350); // let the room go quiet
      }
    });
    return () => {
      off();
      window.clearTimeout(timer);
    };
  }, [memberId]);

  return waiting;
}
