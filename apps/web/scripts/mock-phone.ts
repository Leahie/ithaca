/**
 * Mock phone (WORKSTREAMS.md section 3): pairs with a session and sends any v0 event.
 *
 *   pnpm --filter @ithaca/web mock-phone --session <id> --token <pair_token> send viewer.setMode '{"mode":"sun"}'
 *   pnpm --filter @ithaca/web mock-phone --session <id> --token <t> send viewer.goto '{"room_id":"..."}' chat.user '{"text":"hi"}'
 *   pnpm --filter @ithaca/web mock-phone --session <id> --token <t> listen
 *   pnpm --filter @ithaca/web mock-phone --session <id> --token <t> repl        # one "<type> <json>" per line
 *   pnpm --filter @ithaca/web mock-phone new                                    # create a session, print id + token
 *
 * Options: --api <http base, default http://localhost:8000>  --ws <ws base, default from --api>
 *          --user <X-User-Id, default demo>  --client phone|desktop  --wait <ms to keep listening after send, default 800>
 *          --no-join (skip POST /sessions/{id}/join)  --force (send event types that are not in schema v0)
 */
import { createInterface } from 'node:readline';
import { EVENT_TYPES, EVENT_VERSION, type ClientKind } from '@ithaca/schema';
import WebSocket from 'ws';

interface Options {
  api: string;
  ws: string;
  user: string;
  client: ClientKind;
  session?: string;
  token?: string;
  wait: number;
  join: boolean;
  force: boolean;
}

function parseArgs(argv: string[]): { opts: Options; command: string; rest: string[] } {
  const opts: Options = { api: process.env.MOCK_PHONE_API ?? 'http://localhost:8000', ws: '', user: 'demo', client: 'phone', wait: 800, join: true, force: false };
  const rest: string[] = [];
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    const next = () => {
      const v = argv[++i];
      if (v === undefined) fail(`${a} needs a value`);
      return v;
    };
    if (a === '--api') opts.api = next().replace(/\/$/, '');
    else if (a === '--ws') opts.ws = next().replace(/\/$/, '');
    else if (a === '--user') opts.user = next();
    else if (a === '--client') opts.client = next() as ClientKind;
    else if (a === '--session') opts.session = next();
    else if (a === '--token') opts.token = next();
    else if (a === '--wait') opts.wait = Number(next());
    else if (a === '--no-join') opts.join = false;
    else if (a === '--force') opts.force = true;
    else if (a === '--') continue;
    else rest.push(a);
  }
  if (!opts.ws) opts.ws = opts.api.replace(/^http/, 'ws');
  return { opts, command: rest[0] ?? 'help', rest: rest.slice(1) };
}

function fail(message: string): never {
  console.error(`mock-phone: ${message}`);
  process.exit(2);
}

async function rest<T>(opts: Options, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${opts.api}/api${path}`, {
    method: 'POST',
    headers: { 'X-User-Id': opts.user, ...(body ? { 'content-type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  }).catch((err: Error) => fail(`cannot reach ${opts.api} (${err.message})`));
  if (!res.ok) fail(`POST ${path} -> ${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

function envelope(opts: Options, type: string, payload: unknown) {
  return { v: EVENT_VERSION, id: crypto.randomUUID(), ts: new Date().toISOString(), session_id: opts.session!, source: opts.client, type, payload };
}

function parsePairs(opts: Options, args: string[]): { type: string; payload: unknown }[] {
  if (args.length === 0 || args.length % 2) fail('send needs pairs of <type> <json payload>');
  const out = [];
  for (let i = 0; i < args.length; i += 2) {
    const type = args[i]!;
    if (!(EVENT_TYPES as readonly string[]).includes(type) && !opts.force) fail(`"${type}" is not a v0 event type (use --force to send it anyway). Known: ${EVENT_TYPES.join(', ')}`);
    try {
      out.push({ type, payload: JSON.parse(args[i + 1]!) as unknown });
    } catch {
      fail(`payload for ${type} is not valid JSON: ${args[i + 1]}`);
    }
  }
  return out;
}

async function connect(opts: Options): Promise<WebSocket> {
  if (!opts.session) fail('--session is required (or run "new" to create one)');
  if (opts.join && opts.client === 'phone') await rest(opts, `/sessions/${encodeURIComponent(opts.session)}/join`, { pair_token: opts.token ?? '' });
  const url = `${opts.ws}/ws/sessions/${encodeURIComponent(opts.session)}?client=${opts.client}&token=${encodeURIComponent(opts.token ?? '')}`;
  const socket = new WebSocket(url);
  socket.on('message', (data) => console.log(`<- ${String(data)}`));
  await new Promise<void>((resolve) => {
    socket.once('open', () => resolve());
    socket.once('error', (err) => fail(`websocket ${url}: ${err.message}`));
  });
  socket.on('close', (code) => {
    console.error(`mock-phone: connection closed (${code})`);
    process.exit(code === 1000 ? 0 : 1);
  });
  return socket;
}

async function main(): Promise<void> {
  const { opts, command, rest: args } = parseArgs(process.argv.slice(2));

  if (command === 'new') {
    const s = await rest<{ session_id: string; pair_token: string; expires_at: string }>(opts, '/sessions');
    console.log(JSON.stringify(s));
    return;
  }
  if (command === 'send') {
    const events = parsePairs(opts, args);
    const socket = await connect(opts);
    for (const e of events) {
      const msg = JSON.stringify(envelope(opts, e.type, e.payload));
      socket.send(msg);
      console.log(`-> ${msg}`);
    }
    setTimeout(() => socket.close(1000), opts.wait);
    return;
  }
  if (command === 'listen') {
    await connect(opts);
    return;
  }
  if (command === 'repl') {
    const socket = await connect(opts);
    const rl = createInterface({ input: process.stdin });
    rl.on('line', (line) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      const space = trimmed.indexOf(' ');
      const [e] = parsePairs(opts, [space < 0 ? trimmed : trimmed.slice(0, space), space < 0 ? '{}' : trimmed.slice(space + 1)]);
      const msg = JSON.stringify(envelope(opts, e!.type, e!.payload));
      socket.send(msg);
      console.log(`-> ${msg}`);
    });
    rl.on('close', () => socket.close(1000));
    return;
  }
  console.log(`Usage: mock-phone [options] <new | send <type> <json> ... | listen | repl>\nEvent types: ${EVENT_TYPES.join(', ')}`);
}

void main();
