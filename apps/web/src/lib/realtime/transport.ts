import type { AnyEvent } from '@ithaca/schema';

export type TransportStatus = 'idle' | 'connecting' | 'open' | 'closed';

/** A session event stream. Two implementations: WebSocket (real hub) and local (fixture mode). */
export interface Transport {
  connect(): void;
  send(event: AnyEvent): void;
  close(): void;
  /** Raw inbound messages. The store parses them (I-G7). */
  onMessage(cb: (raw: unknown) => void): () => void;
  onStatus(cb: (status: TransportStatus) => void): () => void;
}

export class Emitter<T> {
  private listeners = new Set<(value: T) => void>();
  on(cb: (value: T) => void): () => void {
    this.listeners.add(cb);
    return () => this.listeners.delete(cb);
  }
  emit(value: T): void {
    this.listeners.forEach((cb) => cb(value));
  }
}
