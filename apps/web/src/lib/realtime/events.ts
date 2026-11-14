import { EVENT_TYPES, EVENT_VERSION, type AnyEvent, type Event, type EventPayloads, type EventSource, type EventType } from '@ithaca/schema';

const KNOWN = new Set<string>(EVENT_TYPES);

export function makeEvent<T extends EventType>(type: T, payload: EventPayloads[T], sessionId: string, source: EventSource): Event<T> {
  return { v: EVENT_VERSION, id: crypto.randomUUID(), ts: new Date().toISOString(), session_id: sessionId, source, type, payload };
}

export type ParseResult = { ok: true; event: AnyEvent } | { ok: false; reason: string };

/**
 * I-G7: an unknown event type or a malformed envelope is reported, never thrown.
 * Unknown fields inside a known event are kept and ignored by consumers.
 */
export function parseEvent(raw: unknown): ParseResult {
  let value = raw;
  if (typeof value === 'string') {
    try {
      value = JSON.parse(value);
    } catch {
      return { ok: false, reason: 'not JSON' };
    }
  }
  if (!value || typeof value !== 'object') return { ok: false, reason: 'not an object' };
  const e = value as Record<string, unknown>;
  if (typeof e.type !== 'string' || typeof e.id !== 'string') return { ok: false, reason: 'missing type or id' };
  if (!KNOWN.has(e.type)) return { ok: false, reason: `unknown event type "${e.type}"` };
  if (!e.payload || typeof e.payload !== 'object') return { ok: false, reason: `event "${e.type}" has no payload` };
  return { ok: true, event: value as AnyEvent };
}
