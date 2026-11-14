import type { AnyEvent, ClientKind, EventPayloads, EventType, ViewerState } from '@ithaca/schema';
import { onFixtureJob } from '@/lib/api/fixture';
import { makeEvent } from './events';
import { Emitter, type Transport, type TransportStatus } from './transport';

type Wire = { kind: 'event'; event: AnyEvent } | { kind: 'hello'; client: ClientKind } | { kind: 'bye'; client: ClientKind } | { kind: 'hub-up' };

export interface LocalHubHooks {
  /** The desktop answers a join with the current viewer state, as the hub would. */
  snapshot(): ViewerState;
  /** A fake agent turn for a user utterance (the offline stand-in for AGENT_PROVIDER=scripted). */
  agent?(text: string, emit: <T extends EventType>(type: T, payload: EventPayloads[T]) => void): Promise<void>;
}

/**
 * Fixture-mode transport (A6): tabs of one browser share a session over BroadcastChannel.
 * The desktop tab plays the hub's part (state snapshot on join, presence) so the phone remote
 * and the agent dock work with no backend. Real phones need the real hub.
 */
export class LocalTransport implements Transport {
  private channel: BroadcastChannel | null = null;
  private messages = new Emitter<unknown>();
  private statuses = new Emitter<TransportStatus>();
  private stopJobs: (() => void) | null = null;
  private onHide = () => this.post({ kind: 'bye', client: this.client });

  constructor(
    private readonly sessionId: string,
    private readonly client: ClientKind,
    private readonly hub?: LocalHubHooks,
  ) {}

  private post(msg: Wire): void {
    this.channel?.postMessage(msg);
  }

  /** Send a hub- or agent-originated event to every tab, this one included. */
  private broadcast<T extends EventType>(type: T, payload: EventPayloads[T], source: 'server' | 'agent'): void {
    const event = makeEvent(type, payload, this.sessionId, source) as AnyEvent;
    this.post({ kind: 'event', event });
    this.messages.emit(event);
  }

  connect(): void {
    this.statuses.emit('connecting');
    this.channel = new BroadcastChannel(`ithaca:${this.sessionId}`);
    this.channel.onmessage = (ev: MessageEvent<Wire>) => {
      const msg = ev.data;
      if (msg.kind === 'event') {
        this.messages.emit(msg.event);
        this.maybeRunAgent(msg.event);
      } else if (msg.kind === 'hello' && this.hub) {
        this.broadcast('session.joined', { client: msg.client }, 'server');
        // Tell the newcomer who was already here.
        this.broadcast('session.joined', { client: this.client }, 'server');
        this.broadcast('session.state', this.hub.snapshot(), 'server');
      } else if (msg.kind === 'bye' && this.hub) {
        this.broadcast('session.left', { client: msg.client }, 'server');
      } else if (msg.kind === 'hub-up' && !this.hub) {
        this.post({ kind: 'hello', client: this.client });
      }
    };
    this.stopJobs = onFixtureJob((payload) => this.broadcast('job.progress', payload, 'server'));
    window.addEventListener('pagehide', this.onHide);
    this.statuses.emit('open');
    if (this.hub) {
      this.post({ kind: 'hub-up' });
      this.messages.emit(makeEvent('session.state', this.hub.snapshot(), this.sessionId, 'server'));
    } else {
      this.post({ kind: 'hello', client: this.client });
    }
  }

  private maybeRunAgent(event: AnyEvent): void {
    if (!this.hub?.agent) return;
    const text = event.type === 'chat.user' ? event.payload.text : event.type === 'voice.transcript' && event.payload.final ? event.payload.text : null;
    if (!text?.trim()) return;
    this.hub.agent(text, (type, payload) => this.broadcast(type, payload, 'agent')).catch((err: unknown) => {
      this.broadcast('agent.error', { message: err instanceof Error ? err.message : String(err) }, 'agent');
    });
  }

  send(event: AnyEvent): void {
    this.post({ kind: 'event', event });
    this.maybeRunAgent(event);
  }

  close(): void {
    this.onHide();
    window.removeEventListener('pagehide', this.onHide);
    this.stopJobs?.();
    this.stopJobs = null;
    this.channel?.close();
    this.channel = null;
    this.statuses.emit('closed');
  }

  onMessage(cb: (raw: unknown) => void): () => void {
    return this.messages.on(cb);
  }
  onStatus(cb: (status: TransportStatus) => void): () => void {
    return this.statuses.on(cb);
  }
}
