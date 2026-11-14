import type { AnyEvent, ClientKind } from '@ithaca/schema';
import { wsOrigin } from '@/lib/config';
import { Emitter, type Transport, type TransportStatus } from './transport';

const MAX_QUEUE = 200;

/** WS /ws/sessions/{sid}?client=desktop|phone&token= . Reconnects with backoff. */
export class WsTransport implements Transport {
  private socket: WebSocket | null = null;
  private queue: string[] = [];
  private attempts = 0;
  private closed = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private messages = new Emitter<unknown>();
  private statuses = new Emitter<TransportStatus>();

  constructor(
    private readonly sessionId: string,
    private readonly client: ClientKind,
    private readonly token: string,
  ) {}

  private url(): string {
    const params = new URLSearchParams({ client: this.client, token: this.token });
    return `${wsOrigin()}/ws/sessions/${encodeURIComponent(this.sessionId)}?${params}`;
  }

  connect(): void {
    this.closed = false;
    this.open();
  }

  private open(): void {
    this.statuses.emit('connecting');
    let socket: WebSocket;
    try {
      socket = new WebSocket(this.url());
    } catch {
      this.retry();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      this.attempts = 0;
      this.statuses.emit('open');
      for (const msg of this.queue.splice(0)) socket.send(msg);
    };
    socket.onmessage = (ev) => this.messages.emit(ev.data);
    socket.onclose = () => {
      if (this.socket === socket) this.socket = null;
      this.statuses.emit('closed');
      this.retry();
    };
    socket.onerror = () => socket.close();
  }

  private retry(): void {
    if (this.closed || this.timer) return;
    const delay = Math.min(10_000, 500 * 2 ** this.attempts++);
    this.timer = setTimeout(() => {
      this.timer = null;
      if (!this.closed) this.open();
    }, delay);
  }

  send(event: AnyEvent): void {
    const msg = JSON.stringify(event);
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(msg);
    else if (this.queue.length < MAX_QUEUE) this.queue.push(msg);
  }

  close(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.socket?.close();
    this.socket = null;
  }

  onMessage(cb: (raw: unknown) => void): () => void {
    return this.messages.on(cb);
  }
  onStatus(cb: (status: TransportStatus) => void): () => void {
    return this.statuses.on(cb);
  }
}
