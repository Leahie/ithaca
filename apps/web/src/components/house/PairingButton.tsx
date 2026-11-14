'use client';

import { QRCodeSVG } from 'qrcode.react';
import { useEffect, useState } from 'react';
import { useSessionInfo } from '@/components/session/SessionProvider';
import { Badge, Button, Notice, cn } from '@/components/ui';
import { IconPhone } from '@/components/ui/icons';
import { publicOrigin } from '@/lib/config';
import { useSession } from '@/lib/store/session';

/** QR pairing for the phone controller. The link carries the session id and its short-lived token (B10). */
export function PairingButton({ propertyId }: { propertyId: string }) {
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
      <Button variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open} aria-label="Pair a phone" className={cn(phoneOn && 'text-leaf')}>
        <IconPhone />
        <span className="hidden xl:inline">{phoneOn ? 'Phone connected' : 'Pair phone'}</span>
      </Button>
      {open && (
        <div role="dialog" aria-label="Pair a phone" className="absolute top-11 right-0 z-30 w-[300px] rounded-xl border border-line-strong bg-raised p-4 shadow-2xl">
          <div className="flex items-center justify-between">
            <div className="text-sm font-medium">Phone controller</div>
            <Badge tone={phoneOn ? 'leaf' : 'neutral'}>{phoneOn ? 'connected' : 'waiting'}</Badge>
          </div>
          {error && (
            <div className="mt-3">
              <Notice tone="error" title="No session">
                {error}
              </Notice>
            </div>
          )}
          {session && (
            <>
              <div className="mt-3 flex justify-center rounded-lg bg-white p-3">
                <QRCodeSVG value={url} size={200} marginSize={0} data-testid="pair-qr" />
              </div>
              <p className="mt-3 text-xs leading-5 text-muted">Scan with your phone camera. The phone becomes a remote: scrub the sun, jump rooms, talk to the agent, capture furniture.</p>
              <a href={url} target="_blank" rel="noreferrer" data-testid="pair-link" className="mt-2 block truncate font-mono text-[11px] text-sky hover:underline">
                {url}
              </a>
              {isLocalhost && (
                <p className="mt-2 text-[11px] leading-4 text-sun">
                  This link points at localhost. A real phone needs your LAN address: set NEXT_PUBLIC_PUBLIC_ORIGIN, and HTTPS for the microphone and gyroscope.
                </p>
              )}
              <div className="mt-3 flex items-center justify-between text-xs text-faint">
                <span>{left === null ? '' : left > 0 ? `Code expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}` : 'Code expired'}</span>
                <Button size="sm" onClick={renew}>
                  New code
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
