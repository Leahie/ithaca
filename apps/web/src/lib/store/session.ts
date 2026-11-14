'use client';

import type { AnyEvent, ClientKind, EventPayloads, EventType, ViewerState } from '@ithaca/schema';
import { create } from 'zustand';
import { makeEvent, parseEvent } from '@/lib/realtime/events';
import type { Transport, TransportStatus } from '@/lib/realtime/transport';
import { applyEvent, initialCore, toViewerState, type CoreState, type ViewerSlice } from './reducer';

export type EventOrigin = 'local' | 'remote';
type EventListener = (event: AnyEvent, origin: EventOrigin) => void;

interface SessionStore extends CoreState {
  client: ClientKind;
  sessionId: string | null;
  /** A family member's phone: the member it speaks for. The hub stamps the same on its events. */
  memberId: string | null;
  status: TransportStatus;

  /** A UI action: apply it here and send it to the session (A1). */
  dispatch<T extends EventType>(type: T, payload: EventPayloads[T]): void;
  /** A message from the session: validate, drop our own echo, apply (I-G7, B9). */
  receive(raw: unknown): void;
  /** Local setup that is not an event: values that come from the twin, not from a user. */
  init(patch: { viewer?: Partial<ViewerSlice>; sweepRooms?: Record<string, string | null> }): void;
  start(opts: { client: ClientKind; sessionId: string; transport: Transport; memberId?: string | null }): void;
  stop(): void;
}

// Not reactive: connection plumbing.
let transport: Transport | null = null;
let unsubscribe: (() => void)[] = [];
const sentIds = new Set<string>();
const listeners = new Set<EventListener>();

/** Every applied event, local or remote. For effects that are not state (camera look, refetch). */
export function onSessionEvent(cb: EventListener): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function core(s: SessionStore): CoreState {
  return { viewer: s.viewer, chat: s.chat, interim: s.interim, jobs: s.jobs, peers: s.peers, members: s.members, sweepRooms: s.sweepRooms };
}

export const useSession = create<SessionStore>((set, get) => ({
  ...initialCore(new Date().toISOString()),
  client: 'desktop',
  sessionId: null,
  memberId: null,
  status: 'idle',

  dispatch(type, payload) {
    const { sessionId, client, memberId } = get();
    const event = makeEvent(type, payload, sessionId ?? 'local', client) as AnyEvent;
    // Applied here before the hub echoes it back: label it as the hub will.
    if (memberId) event.member_id = memberId;
    sentIds.add(event.id);
    if (sentIds.size > 2000) sentIds.delete(sentIds.values().next().value as string);
    set((s) => applyEvent(core(s), event));
    transport?.send(event);
    listeners.forEach((cb) => cb(event, 'local'));
  },

  receive(raw) {
    const parsed = parseEvent(raw);
    if (!parsed.ok) {
      console.warn(`[session] dropped message: ${parsed.reason}`, raw);
      return;
    }
    const event = parsed.event;
    if (sentIds.has(event.id)) return; // the hub echoes our own events back (B9)
    set((s) => applyEvent(core(s), event));
    listeners.forEach((cb) => cb(event, 'remote'));
  },

  init(patch) {
    set((s) => ({
      viewer: patch.viewer ? { ...s.viewer, ...patch.viewer } : s.viewer,
      sweepRooms: patch.sweepRooms ?? s.sweepRooms,
    }));
  },

  start({ client, sessionId, transport: next, memberId = null }) {
    get().stop();
    transport = next;
    set({ client, sessionId, memberId, status: 'connecting', peers: { desktop: client === 'desktop', phone: client === 'phone' } });
    unsubscribe = [next.onMessage((raw) => get().receive(raw)), next.onStatus((status) => set({ status }))];
    next.connect();
  },

  stop() {
    unsubscribe.forEach((u) => u());
    unsubscribe = [];
    transport?.close();
    transport = null;
    set({ status: 'idle' });
  },
}));

export function viewerSnapshot(): ViewerState {
  return toViewerState(useSession.getState().viewer);
}

/** Dispatch helper usable outside React. */
export function dispatch<T extends EventType>(type: T, payload: EventPayloads[T]): void {
  useSession.getState().dispatch(type, payload);
}
