'use client';

import { create } from 'zustand';
import { useSession } from './session';

/**
 * How the big screen presents itself, shared by the top bar, the overlay on the house and the
 * chat dock, so they never disagree.
 *
 * `screen`: the big screen opens as only the house ("clean"), with or without a phone; "Controls"
 * brings back its own top bar, room list and chat until the house is opened again. `auto` follows
 * the phone: clean while one is connected.
 *
 * `voice`: whether the guide speaks its replies on this screen. `null` = not chosen: it speaks
 * when a phone is connected (the phone waits for the big screen to finish talking before it opens
 * its microphone) and on the clean screen (there is no chat to read there), and stays quiet when
 * the screen is used alone, next to the chat.
 */
interface GuideStore {
  screen: 'auto' | 'controls' | 'clean';
  voice: boolean | null;
  setScreen(screen: 'auto' | 'controls' | 'clean'): void;
  setVoice(voice: boolean): void;
}

export const useGuide = create<GuideStore>((set) => ({
  screen: 'clean',
  voice: null,
  setScreen: (screen) => set({ screen }),
  setVoice: (voice) => set({ voice }),
}));

/** Whether the guide's replies are spoken on this screen now, and the switch for it. */
export function useGuideVoice(): [on: boolean, set: (on: boolean) => void] {
  const choice = useGuide((s) => s.voice);
  const clean = useGuide((s) => s.screen === 'clean');
  const phoneHere = useSession((s) => s.peers.phone);
  return [choice ?? (phoneHere || clean), useGuide.getState().setVoice];
}
