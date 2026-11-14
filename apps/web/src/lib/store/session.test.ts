import type { AnyEvent } from '@ithaca/schema';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { makeEvent, parseEvent } from '@/lib/realtime/events';
import { Emitter, type Transport, type TransportStatus } from '@/lib/realtime/transport';
import { applyEvent, initialCore, toViewerState, type CoreState } from './reducer';
import { onSessionEvent, useSession } from './session';

const T0 = '2026-12-21T09:00:00-06:00';
const ev = <T extends AnyEvent['type']>(type: T, payload: Extract<AnyEvent, { type: T }>['payload'], source: AnyEvent['source'] = 'agent') => makeEvent(type, payload as never, 's1', source) as AnyEvent;

/** A transport that behaves like the hub: it echoes every sent event back (B9). */
class EchoTransport implements Transport {
  sent: AnyEvent[] = [];
  private messages = new Emitter<unknown>();
  private statuses = new Emitter<TransportStatus>();
  connect() {
    this.statuses.emit('open');
  }
  send(event: AnyEvent) {
    this.sent.push(event);
    this.messages.emit(JSON.stringify(event));
  }
  inject(raw: unknown) {
    this.messages.emit(raw);
  }
  close() {}
  onMessage(cb: (raw: unknown) => void) {
    return this.messages.on(cb);
  }
  onStatus(cb: (s: TransportStatus) => void) {
    return this.statuses.on(cb);
  }
}

describe('reducer', () => {
  it('goto by room sets the room and bumps the sequence, even for the same room', () => {
    let s = initialCore(T0);
    s = applyEvent(s, ev('viewer.goto', { room_id: 'kitchen' }));
    s = applyEvent(s, ev('viewer.goto', { room_id: 'kitchen' }));
    expect(s.viewer.room_id).toBe('kitchen');
    expect(s.viewer.goto_seq).toBe(2);
  });

  it('goto by sweep resolves the room from the twin index', () => {
    let s: CoreState = { ...initialCore(T0), sweepRooms: { sw1: 'bedroom', sw2: null } };
    s = applyEvent(s, ev('viewer.goto', { sweep_id: 'sw1' }));
    expect(s.viewer).toMatchObject({ sweep_id: 'sw1', room_id: 'bedroom' });
    s = applyEvent(s, ev('viewer.goto', { sweep_id: 'sw2' }));
    expect(s.viewer).toMatchObject({ sweep_id: 'sw2', room_id: 'bedroom' }); // unknown room: keep the last one
  });

  it('rebuilds the viewer from session.state (A2)', () => {
    let s = initialCore(T0);
    s = applyEvent(
      s,
      ev('session.state', { mode: 'sound', room_id: 'r9', datetime: '2026-06-21T18:00:00-05:00', sound_scenario: { room_id: 'r9', scenario: { glazing: 'double' } }, shown_layout_ids: ['l1'] }, 'server'),
    );
    expect(toViewerState(s.viewer)).toEqual({ mode: 'sound', room_id: 'r9', datetime: '2026-06-21T18:00:00-05:00', sound_scenario: { room_id: 'r9', scenario: { glazing: 'double' } }, shown_layout_ids: ['l1'] });
  });

  it('streams agent tokens into one message, then finalizes it (A8)', () => {
    let s = initialCore(T0);
    s = applyEvent(s, ev('chat.user', { text: 'hi' }, 'desktop'));
    s = applyEvent(s, ev('agent.token', { message_id: 'm1', delta: 'Hel' }));
    s = applyEvent(s, ev('agent.token', { message_id: 'm1', delta: 'lo' }));
    expect(s.chat.at(-1)).toEqual({ kind: 'agent', id: 'm1', text: 'Hello', streaming: true });
    s = applyEvent(s, ev('agent.action', { tool: 'viewer_goto', summary: 'Moved you to the kitchen' }));
    s = applyEvent(s, ev('agent.message', { message_id: 'm1', text: 'Hello there.' }));
    expect(s.chat.map((c) => c.kind)).toEqual(['user', 'agent', 'action']);
    expect(s.chat[1]).toEqual({ kind: 'agent', id: 'm1', text: 'Hello there.', streaming: false });
  });

  it('shows an agent error and stops the streaming caret (A8)', () => {
    let s = initialCore(T0);
    s = applyEvent(s, ev('agent.token', { message_id: 'm1', delta: 'Thinking' }));
    s = applyEvent(s, ev('agent.error', { message: 'rate limited' }));
    expect(s.chat[0]).toMatchObject({ kind: 'agent', streaming: false });
    expect(s.chat[1]).toMatchObject({ kind: 'error', message: 'rate limited' });
  });

  it('keeps an interim voice transcript apart from the final one', () => {
    let s = initialCore(T0);
    s = applyEvent(s, ev('voice.transcript', { text: 'take me', final: false }, 'phone'));
    expect(s.interim).toEqual({ source: 'phone', text: 'take me' });
    expect(s.chat).toHaveLength(0);
    s = applyEvent(s, ev('voice.transcript', { text: 'take me to the kitchen', final: true }, 'phone'));
    expect(s.interim).toBeNull();
    expect(s.chat[0]).toMatchObject({ kind: 'user', via: 'voice', source: 'phone' });
  });

  it('tracks presence from joins, leaves and from who sends events', () => {
    let s = initialCore(T0);
    s = applyEvent(s, ev('viewer.setMode', { mode: 'sun' }, 'desktop'));
    expect(s.peers.desktop).toBe(true);
    s = applyEvent(s, ev('session.joined', { client: 'phone' }, 'server'));
    s = applyEvent(s, ev('session.left', { client: 'phone' }, 'server'));
    expect(s.peers).toEqual({ desktop: true, phone: false });
  });
});

describe('parseEvent (I-G7)', () => {
  it('reports unknown types and malformed input without throwing', () => {
    expect(parseEvent('not json')).toMatchObject({ ok: false });
    expect(parseEvent({ type: 'viewer.teleport', id: 'x', payload: {} })).toEqual({ ok: false, reason: 'unknown event type "viewer.teleport"' });
    expect(parseEvent({ type: 'viewer.goto', id: 'x' })).toMatchObject({ ok: false });
    expect(parseEvent(null)).toMatchObject({ ok: false });
  });

  it('keeps unknown fields on a known event', () => {
    const parsed = parseEvent({ ...ev('viewer.setMode', { mode: 'sun' }), extra: 1, payload: { mode: 'sun', future_field: true } });
    expect(parsed.ok).toBe(true);
  });
});

describe('session store', () => {
  let transport: EchoTransport;
  beforeEach(() => {
    transport = new EchoTransport();
    useSession.setState({ ...initialCore(T0) });
    useSession.getState().start({ client: 'desktop', sessionId: 's1', transport });
  });

  it('a click and an incoming event produce the same viewer state (A1)', () => {
    useSession.getState().dispatch('viewer.setMode', { mode: 'sun' });
    useSession.getState().dispatch('viewer.setTime', { datetime: '2026-06-21T18:00:00-05:00' });
    const afterClicks = toViewerState(useSession.getState().viewer);

    useSession.setState({ ...initialCore(T0) });
    transport.inject(ev('viewer.setMode', { mode: 'sun' }, 'phone'));
    transport.inject(JSON.stringify(ev('viewer.setTime', { datetime: '2026-06-21T18:00:00-05:00' }, 'agent')));
    expect(toViewerState(useSession.getState().viewer)).toEqual(afterClicks);
  });

  it('sends local actions to the session, and drops its own echo (B9)', () => {
    const seen: string[] = [];
    const off = onSessionEvent((e, origin) => seen.push(`${origin}:${e.type}`));
    useSession.getState().dispatch('viewer.goto', { room_id: 'kitchen' });
    off();
    expect(transport.sent).toHaveLength(1);
    expect(transport.sent[0]).toMatchObject({ type: 'viewer.goto', source: 'desktop', session_id: 's1', v: 1 });
    expect(useSession.getState().viewer.goto_seq).toBe(1); // applied once, not twice
    expect(seen).toEqual(['local:viewer.goto']);
  });

  it('logs and drops an unknown event, and keeps working (I-G7)', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const before = useSession.getState().viewer;
    expect(() => transport.inject({ v: 1, id: 'u1', ts: T0, session_id: 's1', source: 'server', type: 'future.event', payload: { a: 1 } })).not.toThrow();
    expect(() => transport.inject('{broken')).not.toThrow();
    expect(useSession.getState().viewer).toBe(before);
    expect(warn).toHaveBeenCalledTimes(2);
    warn.mockRestore();
    transport.inject(ev('viewer.setMode', { mode: 'design' }, 'phone'));
    expect(useSession.getState().viewer.mode).toBe('design');
  });
});
