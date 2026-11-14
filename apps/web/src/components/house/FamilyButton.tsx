'use client';

import { QRCodeSVG } from 'qrcode.react';
import { useEffect, useState } from 'react';
import { useSessionInfo } from '@/components/session/SessionProvider';
import { Button, Notice, cn } from '@/components/ui';
import { IconPhone } from '@/components/ui/icons';
import { publicOrigin } from '@/lib/config';
import { useFamily } from '@/lib/store/family';
import { useSession } from '@/lib/store/session';

const initials = (name: string) =>
  name
    .split(/\s+/)
    .map((w) => w[0] ?? '')
    .join('')
    .slice(0, 2)
    .toUpperCase();

/**
 * The family QR code. One code for the whole family, reusable for a day: each person scans it,
 * says their name, and their phone becomes their own remote with their own agent, on whatever
 * house this screen has open. `avatar`: the trigger is the guide's face on glass, for the clean
 * big screen, in place of the top-bar button.
 */
export function FamilyButton({ propertyId, avatar = false }: { propertyId: string; avatar?: boolean }) {
  const household = useFamily((s) => s.household);
  const create = useFamily((s) => s.create);
  const newCode = useFamily((s) => s.newCode);
  const refresh = useFamily((s) => s.refresh);
  const leave = useFamily((s) => s.leave);
  const connected = useSession((s) => s.members);
  const { error: sessionError } = useSessionInfo();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Someone new joined: fetch the member list again so they show by name.
  const known = new Set(household?.members?.map((m) => m.member_id) ?? []);
  const unknown = Object.keys(connected).filter((id) => !known.has(id)).join();
  useEffect(() => {
    if (unknown) void refresh();
  }, [unknown, refresh]);

  const here = Object.values(connected).filter((m) => m.connected).length;
  const origin = publicOrigin();
  const url = household ? `${origin}/join/${encodeURIComponent(household.invite_token)}?p=${encodeURIComponent(propertyId)}` : '';
  const hoursLeft = household ? Math.max(0, Math.round((new Date(household.invite_expires_at).getTime() - Date.now()) / 3_600_000)) : 0;
  const isLocalhost = /\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(origin);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      await create(name.trim() || 'Our family');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative">
      {avatar ? (
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Connect a phone" data-testid="guide-avatar"
          className="flex items-center gap-2 rounded-full border border-white/30 bg-black/35 py-1 pr-3.5 pl-1 text-sm text-white shadow-lg backdrop-blur-md">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-sun font-display text-sm font-semibold text-main">G</span>
          {here > 0 ? `${here} phone${here === 1 ? '' : 's'} connected` : 'Connect a phone'}
        </button>
      ) : (
        <Button variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Family phones" className={cn(here > 0 && 'bg-leaf text-main hover:bg-leaf/85')}>
          <IconPhone />
          <span className="hidden xl:inline">{here > 0 ? `Family · ${here} here` : 'Family'}</span>
        </Button>
      )}
      {open && (
        <div role="dialog" aria-label="Family phones" className="absolute top-12 right-0 z-30 w-[320px] rounded-[var(--radius-card)] border border-line bg-raised p-4 shadow-float">
          {!household ? (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                void start();
              }}
            >
              <div>
                <div className="font-display text-lg font-semibold">Look at this house together</div>
                <p className="mt-1 text-[13.5px] leading-5 text-muted">Everyone joins from their own phone and gets their own agent. The agents compare notes and ask each other, and it all shows here.</p>
              </div>
              <label className="block space-y-1" htmlFor="family-name">
                <span className="text-sm font-semibold">Family name</span>
                <input
                  id="family-name"
                  value={name}
                  maxLength={40}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="The Riveras"
                  className="h-11 w-full rounded-full border-2 border-text bg-surface px-4 text-[15px] outline-none placeholder:text-faint focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-text"
                />
              </label>
              <Button type="submit" variant="primary" className="w-full" disabled={busy}>
                {busy ? 'Starting…' : 'Show the family code'}
              </Button>
              {error && (
                <Notice tone="error" title="Could not start the family">
                  {error}
                </Notice>
              )}
            </form>
          ) : (
            <div className="space-y-3">
              <div className="flex items-baseline justify-between gap-3">
                <div className="truncate font-display text-lg font-semibold">{household.name}</div>
                <span className="shrink-0 text-[13px] text-muted">{here} of {household.members?.length ?? 0} here</span>
              </div>
              {sessionError && (
                <Notice tone="error" title="No live session">
                  {sessionError}
                </Notice>
              )}
              <div className="flex justify-center rounded-[var(--radius-frame)] bg-white p-3">
                <QRCodeSVG value={url} size={196} marginSize={0} data-testid="family-qr" />
              </div>
              <p className="text-[13.5px] leading-5 text-muted">Everyone scans this with their phone camera and types their name. The code works for {hoursLeft} more hours.</p>
              {isLocalhost && (
                <p className="text-[13px] leading-5 text-sun-deep">
                  This code points at localhost, which a phone can't reach. Set NEXT_PUBLIC_PUBLIC_ORIGIN to this computer's address on your Wi-Fi.
                </p>
              )}
              {(household.members?.length ?? 0) > 0 && (
                <ul className="space-y-1" aria-label="Family members">
                  {household.members!.map((m) => {
                    const on = connected[m.member_id]?.connected ?? false;
                    return (
                      <li key={m.member_id} className="flex items-center gap-2.5 text-[15px]">
                        <span className="grid h-8 w-8 place-items-center rounded-full bg-main font-display text-xs font-semibold text-on-main">{initials(m.name)}</span>
                        <span className="flex-1 truncate">{m.name}</span>
                        <span className={cn('flex items-center gap-1.5 text-[13px]', on ? 'text-leaf-deep' : 'text-muted')}>
                          <i aria-hidden className={cn('h-2 w-2 rounded-full', on ? 'bg-leaf' : 'bg-line-strong')} />
                          {on ? 'here' : 'away'}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
              <div className="flex items-center justify-between gap-2 border-t border-line pt-3">
                <button type="button" onClick={leave} className="min-h-11 text-[13px] text-muted underline hover:text-text">
                  Start a new family
                </button>
                <Button size="sm" onClick={() => void newCode()}>
                  New code
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
