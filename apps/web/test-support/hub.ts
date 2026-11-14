/**
 * TEST DOUBLE for Stream B's session API. Not for production and not a second hub.
 * It implements just enough of WORKSTREAMS.md 2.3 / 2.4 to test the WebSocket transport and
 * the mock phone without B's server:
 *
 *   POST /api/sessions                    -> Session
 *   POST /api/sessions/{sid}/join         -> {ok}   (pair token: single use, 10 minutes: B10)
 *   WS   /ws/sessions/{sid}?client=&token=
 *
 * Hub semantics (B9): events go, in order, to every client of the session including the
 * sender; the hub keeps the latest viewer state and sends `session.state` on join; it keeps
 * no event history.
 */
import { createServer, type IncomingMessage, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import type { ClientKind, ViewerState } from '@ithaca/schema';
import { WebSocketServer, type WebSocket } from 'ws';

interface SessionRecord {
  session_id: string;
  pair_token: string;
  expires_at: string;
  tokenUsed: boolean;
  state: ViewerState;
  clients: Map<WebSocket, ClientKind>;
}

export interface TestHub {
  url: string;
  wsUrl: string;
  port: number;
  sessions: Map<string, SessionRecord>;
  close(): Promise<void>;
}

const envelope = (session_id: string, type: string, payload: unknown) => ({ v: 1, id: crypto.randomUUID(), ts: new Date().toISOString(), session_id, source: 'server', type, payload });

async function body(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  try {
    return JSON.parse(Buffer.concat(chunks).toString() || '{}') as Record<string, unknown>;
  } catch {
    return {};
  }
}

export async function startTestHub(port = 0): Promise<TestHub> {
  const sessions = new Map<string, SessionRecord>();
  const server: Server = createServer(async (req, res) => {
    const send = (status: number, json: unknown) => {
      res.writeHead(status, { 'content-type': 'application/json', 'access-control-allow-origin': '*', 'access-control-allow-headers': '*' });
      res.end(JSON.stringify(json));
    };
    const path = new URL(req.url ?? '/', 'http://x').pathname;
    if (req.method === 'OPTIONS') return send(204, {});
    if (req.method === 'POST' && path === '/api/sessions') {
      const s: SessionRecord = {
        session_id: crypto.randomUUID().slice(0, 8),
        pair_token: crypto.randomUUID().slice(0, 8),
        expires_at: new Date(Date.now() + 10 * 60_000).toISOString(),
        tokenUsed: false,
        state: { mode: 'tour', room_id: null, datetime: new Date().toISOString(), sound_scenario: null, shown_layout_ids: [] },
        clients: new Map(),
      };
      sessions.set(s.session_id, s);
      return send(200, { session_id: s.session_id, pair_token: s.pair_token, expires_at: s.expires_at });
    }
    const join = /^\/api\/sessions\/([^/]+)\/join$/.exec(path);
    if (req.method === 'POST' && join) {
      const s = sessions.get(decodeURIComponent(join[1]!));
      const { pair_token } = await body(req);
      if (!s) return send(404, { detail: 'no such session' });
      if (s.tokenUsed || pair_token !== s.pair_token || Date.now() > Date.parse(s.expires_at)) return send(403, { detail: 'pairing token is invalid, used or expired' });
      s.tokenUsed = true;
      return send(200, { ok: true });
    }
    send(404, { detail: 'not found' });
  });

  const wss = new WebSocketServer({ noServer: true });
  server.on('upgrade', (req, socket, head) => {
    const url = new URL(req.url ?? '/', 'http://x');
    const match = /^\/ws\/sessions\/([^/]+)$/.exec(url.pathname);
    const s = match && sessions.get(decodeURIComponent(match[1]!));
    const client = url.searchParams.get('client') as ClientKind | null;
    if (!s || (client !== 'desktop' && client !== 'phone') || url.searchParams.get('token') !== s.pair_token) {
      socket.write('HTTP/1.1 403 Forbidden\r\n\r\n');
      socket.destroy();
      return;
    }
    wss.handleUpgrade(req, socket, head, (ws) => {
      const all = (msg: unknown) => {
        const text = JSON.stringify(msg);
        for (const c of s.clients.keys()) if (c.readyState === c.OPEN) c.send(text);
      };
      // Who was already here, then the newcomer, then the state snapshot.
      for (const kind of new Set(s.clients.values())) ws.send(JSON.stringify(envelope(s.session_id, 'session.joined', { client: kind })));
      s.clients.set(ws, client);
      all(envelope(s.session_id, 'session.joined', { client }));
      ws.send(JSON.stringify(envelope(s.session_id, 'session.state', s.state)));

      ws.on('message', (data) => {
        let event: { type?: string; payload?: Record<string, unknown> };
        try {
          event = JSON.parse(String(data)) as typeof event;
        } catch {
          return; // I-G7: ignore, never throw
        }
        const p = event.payload ?? {};
        if (event.type === 'viewer.setMode') s.state = { ...s.state, mode: p.mode as ViewerState['mode'] };
        else if (event.type === 'viewer.setTime') s.state = { ...s.state, datetime: String(p.datetime) };
        else if (event.type === 'viewer.goto' && p.room_id) s.state = { ...s.state, room_id: String(p.room_id) };
        else if (event.type === 'design.showVariants') s.state = { ...s.state, shown_layout_ids: p.layout_ids as string[] };
        else if (event.type === 'sound.setScenario') s.state = { ...s.state, sound_scenario: p as unknown as ViewerState['sound_scenario'] };
        all(event); // unknown types are relayed as they are
      });
      ws.on('close', () => {
        s.clients.delete(ws);
        all(envelope(s.session_id, 'session.left', { client }));
      });
    });
  });

  await new Promise<void>((resolve) => server.listen(port, '127.0.0.1', resolve));
  const actual = (server.address() as AddressInfo).port;
  return {
    url: `http://127.0.0.1:${actual}`,
    wsUrl: `ws://127.0.0.1:${actual}`,
    port: actual,
    sessions,
    close: () =>
      new Promise((resolve) => {
        for (const c of wss.clients) c.terminate();
        wss.close();
        server.close(() => resolve());
      }),
  };
}
