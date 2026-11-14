'use client';

import type { JoinHouseholdResult } from '@ithaca/schema';
import { useCallback, useEffect, useState } from 'react';
import { Button, Notice, Spinner } from '@/components/ui';
import { api } from '@/lib/api';
import { useFamily } from '@/lib/store/family';
import { RemoteController } from './RemoteController';

/** The person this phone joined as last time, so rescanning the family QR comes back as them. */
const ME_KEY = 'ithaca.member';
interface Remembered {
  member_id: string;
  name: string;
}

function remembered(): Remembered | null {
  try {
    return JSON.parse(window.localStorage.getItem(ME_KEY) ?? 'null') as Remembered | null;
  } catch {
    return null;
  }
}

function remember(me: Remembered | null): void {
  try {
    if (me) window.localStorage.setItem(ME_KEY, JSON.stringify(me));
    else window.localStorage.removeItem(ME_KEY);
  } catch {
    /* asked again next time */
  }
}

type Phase = { kind: 'name' } | { kind: 'joining' } | { kind: 'waiting'; result: JoinHouseholdResult } | { kind: 'in'; result: JoinHouseholdResult } | { kind: 'error'; message: string };

/** How often a phone that joined before the big screen opened a house asks again, ms. */
const WAIT_POLL_MS = 4000;

/**
 * A family member's phone: scan the family QR code, say who you are, and this phone becomes your
 * remote for the house on the big screen, with your own agent. A phone that joined before skips
 * the name and comes back as the same person.
 */
export function JoinFamily({ inviteToken, propertyId }: { inviteToken: string; propertyId: string }) {
  const [phase, setPhase] = useState<Phase>({ kind: 'name' });
  const [name, setName] = useState('');
  const joined = useFamily((s) => s.joined);

  const join = useCallback(
    async (who: string, memberId?: string) => {
      setPhase((p) => (p.kind === 'waiting' ? p : { kind: 'joining' }));
      try {
        const result = await api().joinHousehold(inviteToken, who, memberId);
        remember({ member_id: result.member.member_id, name: result.member.name });
        joined(result.household, result.member);
        setPhase(result.session_id && result.client_token ? { kind: 'in', result } : { kind: 'waiting', result });
      } catch (err) {
        setPhase({ kind: 'error', message: err instanceof Error ? err.message : String(err) });
      }
    },
    [inviteToken, joined],
  );

  // Known on this phone: join straight away as the same person.
  useEffect(() => {
    const me = remembered();
    if (me) {
      setName(me.name);
      void join(me.name, me.member_id);
    }
  }, [join]);

  // Joined, but the big screen has no house open yet: ask again until it does.
  useEffect(() => {
    if (phase.kind !== 'waiting') return;
    const t = setTimeout(() => void join(phase.result.member.name, phase.result.member.member_id), WAIT_POLL_MS);
    return () => clearTimeout(t);
  }, [phase, join]);

  if (phase.kind === 'in') {
    return <RemoteController sessionId={phase.result.session_id!} token={phase.result.client_token!} propertyId={propertyId} member />;
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 px-5 py-8">
      <div className="flex items-center gap-2 font-display text-2xl font-semibold">
        <span className="h-4 w-4 rounded-full bg-sun shadow-[0_0_0_1.5px_var(--color-main)]" />
        Ithaca
      </div>

      {phase.kind === 'waiting' ? (
        <div className="space-y-3">
          <h1 className="font-display text-2xl font-semibold">You're in, {phase.result.member.name}</h1>
          <p className="text-[15px] leading-6 text-muted">
            You joined {phase.result.household.name}. Open a house on the big screen and this phone connects to it by itself.
          </p>
          <Spinner label="Waiting for the big screen…" />
        </div>
      ) : (
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) void join(name.trim());
          }}
        >
          <div className="space-y-1.5">
            <h1 className="font-display text-2xl font-semibold">Join the family</h1>
            <p className="text-[15px] leading-6 text-muted">You get your own agent, which learns what you need from a home and talks with everyone else's.</p>
          </div>
          <label className="block space-y-1.5" htmlFor="member-name">
            <span className="text-sm font-semibold">Your name</span>
            <input
              id="member-name"
              autoFocus
              autoComplete="given-name"
              value={name}
              maxLength={40}
              onChange={(e) => setName(e.target.value)}
              placeholder="Maya"
              className="h-12 w-full rounded-full border-2 border-text bg-raised px-5 text-base outline-none placeholder:text-faint focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text"
            />
          </label>
          <Button type="submit" variant="primary" size="lg" className="w-full" disabled={!name.trim() || phase.kind === 'joining'}>
            {phase.kind === 'joining' ? 'Joining…' : 'Join'}
          </Button>
          {phase.kind === 'error' && (
            <Notice tone="error" title="Could not join">
              {phase.message}
            </Notice>
          )}
          {remembered() && (
            <button
              type="button"
              className="min-h-11 text-sm text-muted underline"
              onClick={() => {
                remember(null);
                setName('');
                setPhase({ kind: 'name' });
              }}
            >
              Not {remembered()?.name}? Join as someone else
            </button>
          )}
        </form>
      )}
    </main>
  );
}
