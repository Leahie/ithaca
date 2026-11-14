'use client';

import type { ClientKind, Session } from '@ithaca/schema';
import { useEffect, useState, type ReactNode, useRef } from 'react';
import { api } from '@/lib/api';
import { config } from '@/lib/config';
import { invalidate } from '@/lib/data/resource';
import { LocalTransport } from '@/lib/realtime/local';
import { scriptedAgentTurn } from '@/lib/realtime/scripted-agent';
import { WsTransport } from '@/lib/realtime/ws';
import { useFamily } from '@/lib/store/family';
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
  /**
   * Phone: the session and token from the pairing link. `prejoined`: a family member's phone,
   * whose token is already a client token (no pairing step). Desktop: omitted, a session is created.
   */
  join?: { sessionId: string; token: string; prejoined?: boolean };
  /** Desktop: the family whose phones join this session through the family QR code. */
  householdId?: string | null;
  children: ReactNode;
}

/**
 * Owns the session connection for a page. Desktop creates (or restores) a session; the phone
 * joins one. In fixture mode the transport is local and the desktop runs the scripted agent.
 */
/** Agent tools after which the furniture list or the layouts on screen are out of date. */
const FURNITURE_TOOLS = new Set(['catalog_add', 'delete_from_inventory', 'remove_from_room', 'propose_layouts', 'task']);

export function SessionProvider({ client, propertyId, join, householdId = null, children }: Props) {
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setInfo({ error: null, renew: () => setGeneration((g) => g + 1) });

    void (async () => {
      try {
        let session: Session;
        if (join) {
          if (!join.prejoined) await api().joinSession(join.sessionId, join.token);
          session = { session_id: join.sessionId, pair_token: join.token, expires_at: '' };
        } else {
          // A saved session is reused only for the same family: phones find the house through it.
          const saved = generation === 0 ? restoreSession() : null;
          session = saved && (saved as Session & { household_id?: string | null }).household_id === householdId ? saved : await api().createSession({ propertyId, householdId: householdId ?? undefined });
          storeSession({ ...session, household_id: householdId } as Session);
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
        useSession.getState().start({ client, sessionId: session.session_id, transport, memberId: join?.prejoined ? useFamily.getState().me?.member_id : null });
      } catch (err) {
        if (!cancelled) setInfo({ error: err instanceof Error ? err.message : String(err) });
      }
    })();

    return () => {
      cancelled = true;
      useSession.getState().stop();
    };
  }, [client, propertyId, join?.sessionId, join?.token, join?.prejoined, householdId, generation]); // eslint-disable-line react-hooks/exhaustive-deps

  /** A tool that changes the user's furniture or layouts ran in the current agent turn. */
  const furnitureTouched = useRef(false);
  // Effects of events that are not viewer state. They run for local and remote events alike (A1).
  useEffect(
    () =>
      onSessionEvent((event) => {
        if (event.type === 'job.progress' && event.payload.status === 'done') {
          invalidate('inventory');
          invalidate('layouts:');
        } else if (event.type === 'design.showVariants') {
          // New layouts often come with pieces the guide has just added: reload both.
          invalidate('inventory');
          invalidate('layouts:');
          invalidate('layout:');
        } else if (event.type === 'agent.action' && FURNITURE_TOOLS.has(event.payload.tool)) {
          furnitureTouched.current = true; // the tool has only been asked for; it finishes within this turn
        } else if (event.type === 'agent.turn' && event.payload.status === 'ended' && furnitureTouched.current) {
          furnitureTouched.current = false;
          invalidate('inventory');
          invalidate('layouts:');
          invalidate('layout:');
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
