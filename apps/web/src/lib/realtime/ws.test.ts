import { spawn } from 'node:child_process';
import path from 'node:path';
import type { AnyEvent } from '@ithaca/schema';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { startTestHub, type TestHub } from '../../../test-support/hub';
import { makeEvent, parseEvent } from './events';

/**
 * The WebSocket transport and the mock phone, against a test double of the session hub
 * (test-support/hub.ts). This is the M0 check, "the mock phone sends viewer.setMode and the
 * desktop changes tab", at the transport level.
 */
let hub: TestHub;
let WsTransport: typeof import('./ws').WsTransport;

beforeAll(async () => {
  hub = await startTestHub();
  process.env.NEXT_PUBLIC_WS_BASE = hub.wsUrl; // read when the config module loads
  ({ WsTransport } = await import('./ws'));
});
afterAll(() => hub.close());

async function newSession(): Promise<{ session_id: string; pair_token: string }> {
  return (await (await fetch(`${hub.url}/api/sessions`, { method: 'POST' })).json()) as { session_id: string; pair_token: string };
}

function collect(transport: InstanceType<typeof WsTransport>) {
  const events: AnyEvent[] = [];
  transport.onMessage((raw) => {
    const parsed = parseEvent(raw);
    if (parsed.ok) events.push(parsed.event);
  });
  const until = async (pred: (e: AnyEvent[]) => boolean, ms = 5000) => {
    const start = Date.now();
    while (!pred(events)) {
      if (Date.now() - start > ms) throw new Error(`timed out; saw ${events.map((e) => e.type).join(', ')}`);
      await new Promise((r) => setTimeout(r, 20));
    }
    return events;
  };
  return { events, until };
}

function runMockPhone(args: string[]): Promise<{ code: number | null; out: string }> {
  return new Promise((resolve) => {
    const cwd = path.resolve(import.meta.dirname, '../../..');
    const child = spawn(path.join(cwd, 'node_modules/.bin/tsx'), ['scripts/mock-phone.ts', '--api', hub.url, ...args], { cwd });
    let out = '';
    child.stdout.on('data', (d) => (out += String(d)));
    child.stderr.on('data', (d) => (out += String(d)));
    child.on('close', (code) => resolve({ code, out }));
  });
}

describe('WsTransport with a hub', () => {
  it('gets session.state on join, and both clients see an event in order, sender included (B9)', async () => {
    const s = await newSession();
    const desktop = new WsTransport(s.session_id, 'desktop', s.pair_token);
    const d = collect(desktop);
    desktop.connect();
    await d.until((e) => e.some((x) => x.type === 'session.state'));

    await fetch(`${hub.url}/api/sessions/${s.session_id}/join`, { method: 'POST', body: JSON.stringify({ pair_token: s.pair_token }) });
    const phone = new WsTransport(s.session_id, 'phone', s.pair_token);
    const p = collect(phone);
    phone.connect();
    await p.until((e) => e.some((x) => x.type === 'session.state'));
    await d.until((e) => e.some((x) => x.type === 'session.joined' && x.payload.client === 'phone'));
    // The late joiner learns that the desktop is already there.
    expect(p.events.some((x) => x.type === 'session.joined' && x.payload.client === 'desktop')).toBe(true);

    const first = makeEvent('viewer.setMode', { mode: 'sun' }, s.session_id, 'phone') as AnyEvent;
    const second = makeEvent('viewer.setTime', { datetime: '2026-12-21T09:00:00-06:00' }, s.session_id, 'phone') as AnyEvent;
    phone.send(first);
    phone.send(second);
    for (const side of [d, p]) {
      const seen = (await side.until((e) => e.some((x) => x.id === second.id))).filter((x) => x.id === first.id || x.id === second.id);
      expect(seen.map((x) => x.id)).toEqual([first.id, second.id]);
    }
    desktop.close();
    phone.close();
  });

  it('queues events sent before the socket is open', async () => {
    const s = await newSession();
    const desktop = new WsTransport(s.session_id, 'desktop', s.pair_token);
    const d = collect(desktop);
    const early = makeEvent('viewer.setMode', { mode: 'design' }, s.session_id, 'desktop') as AnyEvent;
    desktop.connect();
    desktop.send(early); // still connecting
    await d.until((e) => e.some((x) => x.id === early.id));
    desktop.close();
  });

  it('a pairing token joins once (B10)', async () => {
    const s = await newSession();
    const join = () => fetch(`${hub.url}/api/sessions/${s.session_id}/join`, { method: 'POST', body: JSON.stringify({ pair_token: s.pair_token }) });
    expect((await join()).status).toBe(200);
    expect((await join()).status).toBe(403);
  });
});

describe('mock phone CLI', () => {
  it('pairs and sends viewer.setMode; the desktop receives it (M0)', async () => {
    const s = await newSession();
    const desktop = new WsTransport(s.session_id, 'desktop', s.pair_token);
    const d = collect(desktop);
    desktop.connect();
    await d.until((e) => e.some((x) => x.type === 'session.state'));

    const run = await runMockPhone(['--session', s.session_id, '--token', s.pair_token, '--wait', '300', 'send', 'viewer.setMode', '{"mode":"sun"}', 'viewer.goto', '{"room_id":"kitchen"}']);
    expect(run.out).toContain('-> {');
    expect(run.code).toBe(0);
    const events = await d.until((e) => e.some((x) => x.type === 'viewer.goto'));
    expect(events.find((x) => x.type === 'viewer.setMode')).toMatchObject({ source: 'phone', payload: { mode: 'sun' }, session_id: s.session_id });
    await d.until((e) => e.some((x) => x.type === 'session.left' && x.payload.client === 'phone'));
    desktop.close();
  });

  it('refuses an event type that is not in schema v0, unless forced', async () => {
    const s = await newSession();
    const run = await runMockPhone(['--session', s.session_id, '--token', s.pair_token, 'send', 'viewer.teleport', '{}']);
    expect(run.code).toBe(2);
    expect(run.out).toContain('not a v0 event type');
  });

  it('"new" creates a session', async () => {
    const run = await runMockPhone(['new']);
    expect(JSON.parse(run.out.trim())).toMatchObject({ session_id: expect.any(String), pair_token: expect.any(String) });
  });
});
