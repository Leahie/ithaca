'use client';

import type { ClientKind, Session } from '@ithaca/schema';
import { useEffect, useState, type ReactNode } from 'react';
import { api } from '@/lib/api';
import { config } from '@/lib/config';
import { invalidate } from '@/lib/data/resource';
import { LocalTransport } from '@/lib/realtime/local';
import { scriptedAgentTurn } from '@/lib/realtime/scripted-agent';
import { WsTransport } from '@/lib/realtime/ws';
import { onSessionEvent, useSession, viewerSnapshot } from '@/lib/store/session';

const STORAGE_KEY = 'ithaca.session';

function restoreSession(): Session | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as Session;
  } catch {
    return null;
  }
}

function storeSession(session: Session): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    /* the session lasts for this page load only */
  }
}

export interface SessionInfo {
  session: Session | null;
  error: string | null;
  /** Desktop only: drop the session and create a new one (a fresh pairing code). */
  renew(): void;
}

let info: SessionInfo = { session: null, error: null, renew: () => {} };
const infoListeners = new Set<() => void>();
const setInfo = (next: Partial<SessionInfo>) => {
  info = { ...info, ...next };
  infoListeners.forEach((l) => l());
};

export function useSessionInfo(): SessionInfo {
  const [, bump] = useState(0);
  useEffect(() => {
    const l = () => bump((n) => n + 1);
    infoListeners.add(l);
    return () => {
      infoListeners.delete(l);
    };
  }, []);
  return info;
}

interface Props {
  client: ClientKind;
  propertyId: string;
  /** Phone: the session and token from the pairing link. Desktop: omitted, a session is created. */
  join?: { sessionId: string; token: string };
  children: ReactNode;
}

/**
 * Owns the session connection for a page. Desktop creates (or restores) a session; the phone
 * joins one. In fixture mode the transport is local and the desktop runs the scripted agent.
 */
export function SessionProvider({ client, propertyId, join, children }: Props) {
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setInfo({ error: null, renew: () => setGeneration((g) => g + 1) });

    void (async () => {
      try {
        let session: Session;
        if (join) {
          await api().joinSession(join.sessionId, join.token);
          session = { session_id: join.sessionId, pair_token: join.token, expires_at: '' };
        } else {
          const saved = generation === 0 ? restoreSession() : null;
          session = saved ?? (await api().createSession());
          storeSession(session);
        }
        if (cancelled) return;
        setInfo({ session });
        const transport = config.useFixtures
          ? new LocalTransport(
              session.session_id,
              client,
              client === 'desktop'
                ? { snapshot: viewerSnapshot, agent: (text, emit) => scriptedAgentTurn(text, emit, { api: api(), propertyId, viewer: viewerSnapshot }) }
                : undefined,
            )
          : new WsTransport(session.session_id, client, session.pair_token);
        useSession.getState().start({ client, sessionId: session.session_id, transport });
      } catch (err) {
        if (!cancelled) setInfo({ error: err instanceof Error ? err.message : String(err) });
      }
    })();

    return () => {
      cancelled = true;
      useSession.getState().stop();
    };
  }, [client, propertyId, join?.sessionId, join?.token, generation]); // eslint-disable-line react-hooks/exhaustive-deps

  // Effects of events that are not viewer state. They run for local and remote events alike (A1).
  useEffect(
    () =>
      onSessionEvent((event) => {
        if (event.type === 'job.progress' && event.payload.status === 'done') {
          invalidate('inventory');
          invalidate('layouts:');
        } else if (event.type === 'design.showVariants') {
          invalidate('layouts:');
        } else if (event.type === 'layout.select' && client === 'desktop') {
          // The desktop is the viewer authority (A2): it persists the choice, whoever made it.
          api()
            .selectLayout(event.payload.layout_id)
            .then(() => invalidate('layouts:'))
            .catch((err: unknown) => console.warn('[layout] select failed', err));
        }
      }),
    [client],
  );

  return <>{children}</>;
}
