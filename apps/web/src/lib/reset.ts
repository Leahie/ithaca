'use client';

import type { ResetResult } from '@ithaca/schema';
import { create } from 'zustand';
import { api } from '@/lib/api';
import { setApiUser } from '@/lib/api/user';
import { clearResources } from '@/lib/data/resource';
import { useSession } from '@/lib/store/session';

/**
 * "Reset everything", as one screen sees it. The server deletes the data and sends `app.reset` to
 * every screen (routers/reset.py). Each screen then forgets what it kept itself: the screen that
 * asked goes back to the home page, every other screen shows a notice and stops.
 */

/**
 * Every key this app keeps in the browser starts with this, so they are found by name, and one
 * added later is cleared too. Today, in localStorage: `ithaca.household` (the big screen's
 * family), `ithaca.member` and `ithaca.member.<household>` (who a phone joined as), `ithaca.voice`
 * (the guide's voice), `ithaca.hand` and `ithaca.handsfree` (the phone remote's settings),
 * `ithaca.fixture.<user>` and `ithaca.fixture.households` (fixture mode's changes). In
 * sessionStorage: `ithaca.session` (the session and its tokens) and `ithaca.house` (the last house).
 */
const PREFIX = 'ithaca.';

export function clearStoredKeys(): void {
  for (const pick of [() => window.localStorage, () => window.sessionStorage]) {
    try {
      const storage = pick();
      const ours: string[] = [];
      for (let i = 0; i < storage.length; i++) {
        const key = storage.key(i);
        if (key?.startsWith(PREFIX)) ours.push(key);
      }
      ours.forEach((key) => storage.removeItem(key));
    } catch {
      /* storage is switched off: nothing was kept */
    }
  }
}

/** This screen forgets what it kept: its session, its stored keys, who it speaks for. */
export function forgetThisBrowser(): void {
  useSession.getState().stop(); // the session is gone on the server: connecting again would only loop
  clearStoredKeys();
  setApiUser(null);
}

/** Whether another screen's reset reached this one: the notice is up in place of the page. */
export const useResetNotice = create<{ shown: boolean }>(() => ({ shown: false }));

/** True from the moment this screen asks for the reset: its own `app.reset` must not put the notice over the modal. */
let asking = false;

/** This screen was sent `app.reset` (SessionProvider). */
export function onAppReset(): void {
  forgetThisBrowser();
  if (asking) return; // this screen asked: its modal is up, and it starts over when the answer comes
  // The notice first: it takes the page off the screen, so nothing is left to load the dropped data again.
  useResetNotice.setState({ shown: true });
  clearResources();
}

/**
 * Ask for everything to be deleted. Resolves with the server's answer, which may say that a part
 * failed (`ok: false`); rejects when the server could not be asked or refused.
 */
export async function resetEverything(): Promise<ResetResult> {
  asking = true;
  try {
    const result = await api().resetEverything();
    // Fixture mode has no server to tell the other tabs: this tab does, over the channel they share.
    if (api().mode === 'fixture') useSession.getState().dispatch('app.reset', {});
    // The sessions are ended on the server whatever else happened: this screen's own is dead too.
    forgetThisBrowser();
    return result;
  } catch (err) {
    asking = false;
    throw err;
  }
}

/** Back to the home page with a full load, so every store in memory starts empty. */
export function startOver(): void {
  forgetThisBrowser();
  clearResources(); // just before the page goes: a page still on screen would load it all again
  window.location.replace('/');
}
