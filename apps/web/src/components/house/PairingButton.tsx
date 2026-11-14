'use client';

import { QRCodeSVG } from 'qrcode.react';
import { useEffect, useState } from 'react';
import { useSessionInfo } from '@/components/session/SessionProvider';
import { Button, GLASS, GLASS_PANEL, cn, glassButton } from '@/components/ui';
import { IconPhone } from '@/components/ui/icons';
import { publicOrigin } from '@/lib/config';
import { useSession } from '@/lib/store/session';

/** QR pairing for the phone controller. The link carries the session id and its short-lived token (B10). */
export function PairingButton({ propertyId, avatar = false }: { propertyId: string; avatar?: boolean }) {
  const { session, error, renew } = useSessionInfo();
  const phoneOn = useSession((s) => s.peers.phone);
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!open) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [open]);

  const origin = publicOrigin();
  const url = session ? `${origin}/remote/${encodeURIComponent(session.session_id)}?t=${encodeURIComponent(session.pair_token)}&p=${encodeURIComponent(propertyId)}` : '';
  const expires = session?.expires_at ? new Date(session.expires_at).getTime() : null;
  const left = expires ? Math.max(0, Math.round((expires - now) / 1000)) : null;
  const isLocalhost = /\/\/(localhost|127\.0\.0\.1)(:|\/|$)/.test(origin);

  return (
    <div className="relative">
      {avatar ? (
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Connect a phone" data-testid="guide-avatar"
          className={`flex items-center gap-2 rounded-full py-1 pr-3.5 pl-1 text-sm ${GLASS}`}>
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-leaf font-display text-sm font-semibold text-main">G</span>
          {phoneOn ? 'Phone connected' : 'Connect a phone'}
        </button>
      ) : (
        <Button variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Pair a phone" className={cn(phoneOn && 'text-leaf-deep')}>
          <IconPhone />
          <span className="hidden xl:inline">{phoneOn ? 'Phone connected' : 'Pair phone'}</span>
        </Button>
      )}
      {open && (
        <div role="dialog" aria-label="Pair a phone" className={`absolute top-11 right-0 z-30 w-[300px] rounded-3xl p-4 ${GLASS_PANEL}`}>
          <div className="flex items-center justify-between">
            <div className="text-sm font-semibold">Phone controller</div>
            <span className={cn('flex items-center gap-1.5 text-xs', phoneOn ? 'text-leaf' : 'text-white/60')}>
              <i aria-hidden className={cn('h-2 w-2 rounded-full', phoneOn ? 'bg-leaf' : 'bg-white/40')} />
              {phoneOn ? 'connected' : 'waiting'}
            </span>
          </div>
          {error && (
            <p role="alert" className="mt-3 text-[13px] leading-5 text-coral">
              No session. {error}
            </p>
          )}
          {session && (
            <>
              <div className="mt-3 flex justify-center rounded-2xl bg-white p-3">
                <QRCodeSVG value={url} size={200} marginSize={0} data-testid="pair-qr" />
              </div>
              <p className="mt-3 text-xs leading-5 text-white/75">Scan with your phone camera. The phone becomes a remote: scrub the sun, jump rooms, talk to the agent, capture furniture.</p>
              <a href={url} target="_blank" rel="noreferrer" data-testid="pair-link" className="mt-2 block truncate font-mono text-[11px] text-sky hover:underline">
                {url}
              </a>
              {isLocalhost && (
                <p className="mt-2 text-[11px] leading-4 text-sun">
                  This link points at localhost. A real phone needs your LAN address: set NEXT_PUBLIC_PUBLIC_ORIGIN, and HTTPS for the microphone and gyroscope.
                </p>
              )}
              <div className="mt-3 flex items-center justify-between border-t border-white/20 pt-3 text-xs text-white/60">
                <span>{left === null ? '' : left > 0 ? `Code expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}` : 'Code expired'}</span>
                <button type="button" className={glassButton()} onClick={renew}>
                  New code
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
