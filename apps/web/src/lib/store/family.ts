'use client';

import type { Household, Member } from '@ithaca/schema';
import { create } from 'zustand';
import { api } from '@/lib/api';
import { setApiUser } from '@/lib/api/user';
import { useSession } from './session';

/**
 * The household looking at this house. The desktop owns one, kept in localStorage so a reload
 * keeps the same family and its QR code. A phone holds the household it joined and `me`, the
 * member it speaks for: its agent, uploads and memory are that member's (setApiUser).
 */
interface FamilyStore {
  household: Household | null;
  me: Member | null;
  error: string | null;
  /** Desktop: restore() has finished, so the session can be created for the right family. */
  restored: boolean;
  /** Desktop: "Start a new family" was pressed, so the next family starts empty. */
  fresh: boolean;
  /** Desktop: load the saved household, if any. */
  restore(): Promise<void>;
  create(name: string): Promise<Household>;
  refresh(): Promise<void>;
  newCode(): Promise<void>;
  /** Desktop: forget this household (a new family starts from scratch). */
  leave(): void;
  /** Phone: after joining. */
  joined(household: Household, me: Member): void;
}

const HOUSEHOLD_KEY = 'ithaca.household';
/** Phone: which member this browser is, per household, so rescanning comes back as them. */
export const memberKey = (householdId: string) => `ithaca.member.${householdId}`;

const read = (key: string): string | null => {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
};
const write = (key: string, value: string | null) => {
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* remembered for this page load only */
  }
};

export const useFamily = create<FamilyStore>((set, get) => ({
  household: null,
  me: null,
  error: null,
  restored: false,
  fresh: false,

  async restore() {
    const id = read(HOUSEHOLD_KEY);
    if (!id || get().household) return set({ restored: true });
    try {
      set({ household: await api().getHousehold(id), error: null, restored: true });
    } catch {
      write(HOUSEHOLD_KEY, null); // the API forgot it (new database): start over when asked
      set({ restored: true });
    }
  },

  async create(name) {
    const household = await api().createHousehold(name, get().fresh);
    write(HOUSEHOLD_KEY, household.household_id);
    set({ household, error: null, fresh: false });
    return household;
  },

  async refresh() {
    const h = get().household;
    if (!h) return;
    try {
      set({ household: await api().getHousehold(h.household_id) });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },

  async newCode() {
    const h = get().household;
    if (h) set({ household: await api().rotateInvite(h.household_id) });
  },

  leave() {
    write(HOUSEHOLD_KEY, null);
    set({ household: null, fresh: true });
  },

  joined(household, me) {
    write(memberKey(household.household_id), me.member_id);
    setApiUser(me.member_id);
    set({ household, me, error: null });
  },
}));

/** Name of a household member, from the household or from who is connected. */
export function useMemberName(): (memberId: string | null | undefined) => string | null {
  const household = useFamily((s) => s.household);
  const connected = useSession((s) => s.members);
  return (id) => {
    if (!id) return null;
    return household?.members?.find((m) => m.member_id === id)?.name ?? connected[id]?.name ?? null;
  };
}
