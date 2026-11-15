'use client';

import type { LibraryPiece, Room } from '@ithaca/schema';
import { useCallback, useEffect, useRef, useState } from 'react';
import { furnitureChanged } from '@/components/house/ArrangePanel';
import { api } from '@/lib/api';
import { pointInPolygon } from '@/lib/geometry/polygon';
import { speechProvider } from '@/lib/speech/provider';
import { dispatch } from '@/lib/store/session';
import { useStudio, type StudioMark, type StudioPiece } from './store';

/**
 * A request made in the room studio, spoken or typed ("put a green sofa here"), turned into a
 * piece of furniture standing on the mark the hands made: the words name the piece, the mark says
 * where and how wide. The library is searched for the words, the piece nearest the mark's size is
 * taken, added to the family's furniture and stood on the spot.
 *
 * This is worked out here, in the page, not by the guide: a request that is not for furniture
 * goes to the guide as any spoken line does.
 */

export interface StudioContext {
  propertyId: string;
  room: Room;
}

export type StudioRequest = { kind: 'add'; query: string } | { kind: 'clear' } | { kind: 'other' };

/** The whole line, not a word in it: "a clear glass table" is a table. */
const CLEAR = /^(please )?(clear|cancel|never ?mind|forget (it|that)|scratch that|undo|remove)( it| the| that| my)?( mark| spot)?( please)?$/;
/** Asking for something: with one of these, what is left of the sentence is taken as the piece. */
const VERBS = /\b(add|put|place|drop|give me|i want|i need|i'd like|i would like|let's have|lets have|let's get|lets get|bring in|bring|set|get me)\b/;
/** Longest first, so "tv stand" is found before "stand" could be. */
const FURNITURE = [
  'tv stand', 'coffee table', 'side table', 'dining table', 'night stand', 'book shelf', 'book case',
  'sofa', 'couch', 'sectional', 'loveseat', 'bed', 'crib', 'bunk', 'nightstand', 'dresser', 'wardrobe', 'desk', 'table', 'chair',
  'armchair', 'stool', 'bench', 'ottoman', 'lamp', 'rug', 'plant', 'bookshelf', 'bookcase', 'shelf', 'cabinet', 'console', 'mirror',
  'sideboard', 'chest', 'piano',
];
const NAMES_FURNITURE = new RegExp(`\\b(${FURNITURE.join('|')})s?\\b`);
/** Phrases taken out whole, before the single words. */
const PHRASES = [
  'can you', 'could you', 'would you', 'give me', 'i want', 'i need', "i'd like", 'i would like', "let's have", 'lets have', "let's get", 'lets get', 'bring in', 'get me',
  'in the', 'in this', 'in that', 'where i', 'my hands', 'right here', 'right there', 'over here', 'over there', 'this big', 'this wide', 'this long', 'this size',
];
/** Words that say where or how big, or nothing at all: not part of the piece's name. */
const DROP = new Set([
  'add', 'put', 'place', 'drop', 'set', 'bring', 'get', 'have', 'a', 'an', 'the', 'some', 'here', 'there', 'right', 'over', 'about', 'around', 'roughly',
  'this', 'that', 'these', 'those', 'big', 'size', 'sized', 'wide', 'long', 'please', 'room', 'corner', 'spot', 'place', 'marked', 'mark', 'hands', 'hand', 'my',
  'like', 'so', 'um', 'uh', 'er', 'of', 'it', 'one', 'me', 'want', 'need', 'to', 'for', 'i', 'in', 'on', 'at', 'by', 'and', 'with', 'just', 'now', 'ok', 'okay',
  'hey', 'can', 'could', 'would', 'you', 'we', 'us', "let's", 'lets', 'new', 'another', 'where', 'is', 'be', 'go', 'goes', 'should', 'then', 'also', 'too', 'up', 'down', 'thing',
]);

export function parseRequest(text: string): StudioRequest {
  const said = text
    .toLowerCase()
    .replace(/[^a-z0-9' -]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!said) return { kind: 'other' };
  if (CLEAR.test(said)) return { kind: 'clear' };
  if (!VERBS.test(said) && !NAMES_FURNITURE.test(said)) return { kind: 'other' };
  let rest = ` ${said} `;
  for (const phrase of PHRASES) rest = rest.split(` ${phrase} `).join(' ');
  const query = rest
    .split(' ')
    .filter((word) => word && !DROP.has(word))
    .join(' ')
    .trim();
  return query ? { kind: 'add', query } : { kind: 'other' };
}

/** How many of the library's first pieces are weighed by size: further down they are no longer what was asked for. */
const WEIGHED = 40;

/**
 * The piece for a mark. The library lists the most relevant first: with no width marked, that
 * one. With a width, the piece whose own width is nearest it (and its depth, when one was marked),
 * a little in favour of the more relevant; a piece that fits turned a quarter counts too, less.
 * One clearly wider than the mark is held back: it would not stand where the hands showed.
 */
export function pickBySize(pieces: LibraryPiece[], mark: Pick<StudioMark, 'width' | 'depth'>): LibraryPiece | null {
  const first = pieces[0];
  if (!first) return null;
  const { width, depth } = mark;
  if (!width || width <= 0) return first;
  let best = first;
  let bestScore = Infinity;
  pieces.slice(0, WEIGHED).forEach((piece, index) => {
    const { w, d } = piece.dims_m;
    const score = (across: number, deep: number) =>
      Math.abs(across - width) / width + 0.35 * (depth ? Math.abs(deep - depth) / depth : 0) + 0.004 * index + (across > 1.15 * width ? 0.6 : 0);
    const s = Math.min(score(w, d), score(d, w) + 0.15);
    if (s < bestScore) {
      bestScore = s;
      best = piece;
    }
  });
  return best;
}

/** How far off a wall a piece is stood (m). */
const WALL_GAP_M = 0.04;

/**
 * A mark made close to a wall, moved off it just far enough for the piece chosen for it to stand
 * clear: the hands show the middle of the piece, and a sofa "against that wall" must not end up
 * half through it. A spot already clear of the walls is left where it is.
 */
export function clearOfWalls(room: Room, x: number, z: number, w: number, d: number, yawDeg: number): [number, number] {
  const a = (yawDeg * Math.PI) / 180;
  // The piece's width and depth as directions on the floor (x, z), as three turns a model about +Y.
  const wide = [Math.cos(a), -Math.sin(a)] as const;
  const deep = [Math.sin(a), Math.cos(a)] as const;
  const reach = (ux: number, uz: number) => (Math.abs(wide[0] * ux + wide[1] * uz) * w) / 2 + (Math.abs(deep[0] * ux + deep[1] * uz) * d) / 2;
  const poly = room.polygon;
  let px = x;
  let pz = z;
  // Twice round: off one wall of a corner, then off the other.
  for (let pass = 0; pass < 2; pass++) {
    for (let i = 0; i < poly.length; i++) {
      const p = poly[i]!;
      const q = poly[(i + 1) % poly.length]!;
      const len = Math.hypot(q[0] - p[0], q[1] - p[1]);
      if (len < 1e-6) continue;
      const tx = (q[0] - p[0]) / len;
      const tz = (q[1] - p[1]) / len;
      let nx = -tz;
      let nz = tx;
      if (!pointInPolygon([(p[0] + q[0]) / 2 + nx * 0.05, (p[1] + q[1]) / 2 + nz * 0.05], poly)) {
        nx = -nx;
        nz = -nz;
      }
      // Only a wall the piece stands beside: not one further along, round a corner.
      const along = (px - p[0]) * tx + (pz - p[1]) * tz;
      const slack = reach(tx, tz);
      if (along < -slack || along > len + slack) continue;
      const off = (px - p[0]) * nx + (pz - p[1]) * nz;
      const need = reach(nx, nz) + WALL_GAP_M;
      if (off >= need || off < -need) continue;
      px += nx * (need - off);
      pz += nz * (need - off);
    }
  }
  return pointInPolygon([px, pz], poly) ? [px, pz] : [x, z];
}

/** Counts requests: a newer one makes an older one stop where it is, without a word. */
let runSeq = 0;

function say(line: string): void {
  try {
    speechProvider().speak(line);
  } catch {
    // No voice here: the line is still written on the screen.
  }
}

/** The server's refusal in words: `{"violations": ["…"]}` or `{"detail": "…"}` comes back as JSON text. */
function reason(err: unknown): string {
  const text = err instanceof Error ? err.message : String(err);
  return /"violations":\s*\["([^"]+)"/.exec(text)?.[1] ?? /"detail":\s*"([^"]+)"/.exec(text)?.[1] ?? text;
}

const WAIT_FOR_MARK_MS = 20_000;
const PLACED_SHOWS_MS = 4000;

/** Waits for the hands to mark a spot. Null when none came in time, the studio closed, or the wait was called off. */
function waitForMark(): Promise<StudioMark | null> {
  return new Promise((resolve) => {
    let done = false;
    const finish = (mark: StudioMark | null) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      off();
      resolve(mark);
    };
    const off = useStudio.subscribe((s) => {
      if (s.mark) finish(s.mark);
      // Opening the microphone while it waits is not calling the wait off.
      else if (!s.open || (s.phase !== 'waiting' && s.phase !== 'listening')) finish(null);
    });
    const timer = setTimeout(() => finish(null), WAIT_FOR_MARK_MS);
    // It may have been marked between the question and here.
    const now = useStudio.getState();
    if (now.mark) finish(now.mark);
    else if (!now.open || (now.phase !== 'waiting' && now.phase !== 'listening')) finish(null);
  });
}

/** One request, start to finish. Progress is written into the studio's store as it goes. */
export async function runRequest(text: string, ctx: StudioContext): Promise<void> {
  const run = ++runSeq;
  const studio = () => useStudio.getState();
  // Another request, the studio shut, or another room on show: this one stops where it is.
  const live = () => run === runSeq && studio().open && studio().roomId === ctx.room.room_id;
  const patch: ReturnType<typeof studio>['patch'] = (part) => studio().patch(part);
  try {
    patch({ heard: text });
    const request = parseRequest(text);
    if (request.kind === 'clear') {
      studio().setMark(null);
      patch({ phase: 'idle', reply: 'Mark cleared.' });
      return;
    }
    if (request.kind === 'other') {
      // Not about furniture for the mark: the guide answers it, as it does any spoken line.
      dispatch('voice.transcript', { text, final: true });
      patch({ phase: 'idle', reply: null });
      return;
    }
    const { query } = request;

    let mark = studio().mark;
    if (!mark) {
      const ask = 'Show me where. Hold both hands apart over the spot, or point at it.';
      patch({ phase: 'waiting', reply: ask });
      say(ask);
      mark = await waitForMark();
      if (!live()) return;
      if (!mark) {
        // Only if the wait ran out: a wait that was called off has its own words already.
        if (studio().phase === 'waiting' || studio().phase === 'listening') patch({ phase: 'idle', reply: 'No spot was marked.' });
        return;
      }
    }

    patch({ phase: 'searching', reply: `Looking for a ${query}${mark.width ? ` about ${mark.width.toFixed(1)} m wide` : ''}…`, piece: null });
    let items = (await api().listLibrary({ q: query, limit: 60 })).items;
    if (!live()) return;
    const lastWord = query.split(' ').pop() ?? query;
    if (!items.length && lastWord !== query) {
      items = (await api().listLibrary({ q: lastWord, limit: 60 })).items;
      if (!live()) return;
    }
    const choice = pickBySize(items, mark);
    if (!choice) {
      const none = `I could not find a ${query} in the library.`;
      patch({ phase: 'error', reply: none });
      say(none);
      return;
    }

    const wide = choice.dims_m.w.toFixed(2);
    const piece: StudioPiece = { label: choice.label, dims: { w: choice.dims_m.w, d: choice.dims_m.d, h: choice.dims_m.h }, url: null, thumbnail: choice.thumbnail_url };
    patch({ phase: 'fetching', piece, reply: `${choice.label}: ${wide} m wide${mark.width ? `, the closest to your ${mark.width.toFixed(1)} m` : ''}. Bringing it in…` });
    const item = await api().addFromLibrary(choice.library_id);
    if (!live()) {
      furnitureChanged(ctx.propertyId); // it is in storage all the same
      return;
    }
    patch({ phase: 'placing', piece: { ...piece, url: api().assetUrl(item.glb_url) } });

    const yaw = ((mark.yaw_deg % 360) + 360) % 360;
    // On the mark, turned as the mark is, and off the wall by the piece's own depth.
    const [x, z] = clearOfWalls(ctx.room, mark.x, mark.z, choice.dims_m.w, choice.dims_m.d, yaw);
    const spot = { x, z, yaw_deg: yaw, floor_id: ctx.room.floor_id, room_id: ctx.room.room_id };
    try {
      await api().placePiece(ctx.propertyId, item.item_id, { ...spot, snap: false });
    } catch {
      // Exactly there was refused: the nearest spot that works.
      try {
        await api().placePiece(ctx.propertyId, item.item_id, { ...spot, snap: true });
      } catch (err) {
        if (live()) patch({ phase: 'error', reply: `The ${choice.label} could not be put there: ${reason(err)}` });
        return;
      }
    }
    // It stands in the house now, whoever is still looking: every view loads the furniture again.
    furnitureChanged(ctx.propertyId);
    if (!live()) return;
    patch({ phase: 'placed', placedId: item.item_id, reply: `${choice.label} is in, ${wide} m wide.` });
    say(`Done. The ${choice.label} is in.`);
    studio().setMark(null);
    setTimeout(() => {
      if (run === runSeq && studio().open && studio().phase === 'placed') patch({ phase: 'idle' });
    }, PLACED_SHOWS_MS);
  } catch (err) {
    if (live()) patch({ phase: 'error', reply: reason(err) });
  }
}

export interface StudioVoice {
  supported: boolean;
  listening: boolean;
  /** How loud the microphone hears things now, 0 to 1; null until sound arrives. */
  level: number | null;
  error: string | null;
  /** Hold to talk: `start`, then `stop`. */
  start(): void;
  stop(): void;
  /** Tap to talk: it ends by itself at a pause; a second tap ends it now. */
  toggle(): void;
  /** A request typed, not spoken. */
  submit(text: string): void;
}

/**
 * Talking to the studio. As `usePushToTalk`, but the words stay here: what is being said shows in
 * the studio as it is heard, and the finished line is a request for the mark (`runRequest`), which
 * itself passes anything that is not about furniture on to the guide.
 */
export function useStudioVoice(ctx: StudioContext | null): StudioVoice {
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [supported, setSupported] = useState(false);
  const [level, setLevel] = useState<number | null>(null);
  const stopRef = useRef<(() => void) | null>(null);
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;

  useEffect(() => {
    setSupported(speechProvider().sttAvailable());
  }, []);

  const submit = useCallback((text: string) => {
    const said = text.trim();
    const at = ctxRef.current;
    if (!said || !at) return;
    void runRequest(said, at);
  }, []);

  const begin = useCallback(
    (auto: boolean) => {
      if (stopRef.current) return;
      setError(null);
      setLevel(null);
      setListening(true);
      const before = useStudio.getState().phase;
      useStudio.getState().patch({ heard: '', phase: 'listening' });
      /** Listening is over and nothing came of it: back to idle, unless something else has moved the studio on. */
      const settle = () => {
        if (useStudio.getState().phase === 'listening') useStudio.getState().patch({ phase: before === 'listening' ? 'idle' : before === 'waiting' ? 'waiting' : 'idle' });
      };
      try {
        speechProvider().cancelSpeech();
      } catch {
        // Nothing was being said.
      }
      // The provider may fail before `listen` returns: its stop function must not then be kept.
      let ended = false;
      const stopListening = speechProvider().listen(
        {
          onTranscript(text, final) {
            if (!final) {
              useStudio.getState().patch({ heard: text, phase: 'listening' });
              return;
            }
            ended = true;
            stopRef.current = null;
            setListening(false);
            const said = text.trim();
            if (said && ctxRef.current) submit(said);
            else settle();
          },
          onLevel: setLevel,
          onError(message) {
            ended = true;
            stopRef.current = null;
            setListening(false);
            setError(message);
            settle();
          },
        },
        { auto },
      );
      stopRef.current = ended ? null : stopListening;
    },
    [submit],
  );

  const start = useCallback(() => begin(false), [begin]);
  const stop = useCallback(() => stopRef.current?.(), []);
  const toggle = useCallback(() => (stopRef.current ? stopRef.current() : begin(true)), [begin]);

  useEffect(() => () => stopRef.current?.(), []);

  return { supported, listening, level, error, start, stop, toggle, submit };
}
