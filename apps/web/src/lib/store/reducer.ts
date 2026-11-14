import type { AnyEvent, CatalogPiece, ClientKind, EventSource, JobProgressPayload, SoundSetScenarioPayload, ViewerMode, ViewerPlayback, ViewerState, RemoteViewPayload } from '@ithaca/schema';

/**
 * Viewer state and its reducer (A1). Every change to what the viewer shows is an event:
 * a click dispatches it locally, the phone or the agent sends it over the session, and both
 * go through `applyEvent`. There is no second path.
 */
export interface ViewerSlice {
  mode: ViewerMode;
  room_id: string | null;
  /** Set when the last goto named a sweep; cleared by a goto that names a room. */
  sweep_id: string | null;
  /** I-G13: ISO 8601 with offset, in the property's timezone. */
  datetime: string;
  sound_scenario: SoundSetScenarioPayload | null;
  shown_layout_ids: string[];
  selected_layout_id: string | null;
  /** Where to stand and what to face in `room_id`, when the last goto said (the agent's best view). */
  view: { at_sweep_id: string; look_at: { x: number; y: number; z: number } } | null;
  /** Bumps on every goto, so a repeated goto to the same room still moves the camera. */
  goto_seq: number;
  /** What the big screen is playing; the desktop runs the clock while `playing`. */
  playback: Playback;
}

export type SoundLayer = NonNullable<ViewerPlayback['sounds']>[number];

export interface Playback {
  playing: boolean;
  listening: boolean;
  /** The outdoor sounds chosen to be heard while `listening`. */
  sounds: SoundLayer[];
  voice: 'female' | 'male';
}

const NOTHING_PLAYING: Playback = { playing: false, listening: false, sounds: ['highway', 'streets', 'birds', 'insects', 'frogs'], voice: 'female' };

/**
 * One line of the conversation. `member_id` says whose it is in a household session: the member
 * who spoke, or the member whose agent replied. Null for the desktop and its own agent.
 */
export type ChatItem =
  | { kind: 'user'; id: string; text: string; via: 'chat' | 'voice'; source: EventSource; member_id: string | null }
  | { kind: 'agent'; id: string; text: string; streaming: boolean; member_id: string | null }
  | { kind: 'action'; id: string; tool: string; summary: string; member_id: string | null }
  | { kind: 'error'; id: string; message: string; member_id: string | null }
  /** Layout options the agent made, shown as small plans the user can click to put in the room. */
  | { kind: 'layouts'; id: string; layout_ids: string[]; member_id: string | null }
  /** Catalog pieces the agent suggested, shown as picture cards the user can click to add. */
  | { kind: 'catalog'; id: string; property_id: string; room_id: string | null; near_item_id: string | null; pieces: CatalogPiece[]; member_id: string | null }
  /** A note one member's agent shared on the family board. */
  | { kind: 'note'; id: string; text: string; room_id: string | null; member_id: string }
  /** One member's agent asked another's; `answer` arrives later. */
  | { kind: 'ask'; id: string; from_member_id: string; to_member_id: string; question: string; answer: string | null };

/** A household member seen in this session. */
export interface MemberPresence {
  name: string;
  connected: boolean;
}

export interface CoreState {
  viewer: ViewerSlice;
  chat: ChatItem[];
  /** Live, not yet final, voice transcript. */
  interim: { source: EventSource; text: string } | null;
  jobs: Record<string, JobProgressPayload>;
  peers: Record<ClientKind, boolean>;
  /** Household members by member_id: who has a phone here now. */
  members: Record<string, MemberPresence>;
  /** sweep_id -> room_id, from the twin, so a goto by sweep also updates the room. */
  sweepRooms: Record<string, string | null>;
  /** The screen the phone remote is on, while a phone is connected. */
  remoteView: RemoteViewPayload['view'] | null;
}

const MAX_CHAT = 300;

export function initialCore(datetime: string): CoreState {
  return {
    viewer: { mode: 'tour', room_id: null, sweep_id: null, datetime, sound_scenario: null, shown_layout_ids: [], selected_layout_id: null, view: null, goto_seq: 0, playback: NOTHING_PLAYING },
    chat: [],
    interim: null,
    jobs: {},
    peers: { desktop: false, phone: false },
    remoteView: null,
    members: {},
    sweepRooms: {},
  };
}

const push = (chat: ChatItem[], item: ChatItem): ChatItem[] => [...chat, item].slice(-MAX_CHAT);

export function applyEvent(state: CoreState, event: AnyEvent): CoreState {
  // An event from a client proves that client is there, even if we joined after it did.
  if ((event.source === 'desktop' || event.source === 'phone') && !state.peers[event.source]) {
    state = { ...state, peers: { ...state.peers, [event.source]: true } };
  }
  const v = state.viewer;
  switch (event.type) {
    case 'session.state': {
      const p = event.payload;
      return {
        ...state,
        viewer: {
          ...v,
          mode: p.mode ?? v.mode,
          room_id: p.room_id ?? v.room_id,
          sweep_id: p.room_id && p.room_id !== v.room_id ? null : v.sweep_id,
          datetime: p.datetime || v.datetime,
          sound_scenario: p.sound_scenario ?? null,
          shown_layout_ids: p.shown_layout_ids ?? [],
          playback: { ...NOTHING_PLAYING, ...p.playback },
        },
      };
    }
    case 'session.joined': {
      const { client, member_id, name } = event.payload;
      const members = member_id ? { ...state.members, [member_id]: { name: name ?? state.members[member_id]?.name ?? 'Someone', connected: true } } : state.members;
      return { ...state, peers: { ...state.peers, [client]: true }, members };
    }
    case 'session.left': {
      const { client, member_id } = event.payload;
      if (member_id) {
        // One member leaving does not mean no phone is left.
        const members = { ...state.members, [member_id]: { name: state.members[member_id]?.name ?? 'Someone', connected: false } };
        return { ...state, members, peers: { ...state.peers, phone: Object.values(members).some((m) => m.connected) } };
      }
      return { ...state, peers: { ...state.peers, [client]: false }, remoteView: client === 'phone' ? null : state.remoteView };
    }
    case 'viewer.goto': {
      const { room_id, sweep_id, at_sweep_id, look_at } = event.payload;
      const view = at_sweep_id && look_at ? { at_sweep_id, look_at } : null;
      if (room_id) return { ...state, viewer: { ...v, room_id, sweep_id: null, view, goto_seq: v.goto_seq + 1 } };
      if (sweep_id) {
        // A sweep in no room (stairs, patio) leaves no room, rather than the last one.
        const room = sweep_id in state.sweepRooms ? state.sweepRooms[sweep_id]! : v.room_id;
        return { ...state, viewer: { ...v, sweep_id, room_id: room, view: null, goto_seq: v.goto_seq + 1 } };
      }
      return state;
    }
    case 'viewer.setTime':
      return event.payload.datetime ? { ...state, viewer: { ...v, datetime: event.payload.datetime } } : state;
    case 'viewer.setMode':
      return { ...state, viewer: { ...v, mode: event.payload.mode } };
    case 'viewer.playback': {
      const { playing, listening, sounds, voice } = event.payload;
      return {
        ...state,
        viewer: {
          ...v,
          playback: {
            playing: playing ?? v.playback.playing,
            listening: listening ?? v.playback.listening,
            sounds: sounds ?? v.playback.sounds,
            voice: voice ?? v.playback.voice,
          },
        },
      };
    }
    case 'design.showVariants':
      return {
        ...state,
        viewer: { ...v, shown_layout_ids: event.payload.layout_ids },
        chat: event.payload.layout_ids.length ? push(state.chat, { kind: 'layouts', id: event.id, layout_ids: event.payload.layout_ids, member_id: event.member_id ?? null }) : state.chat,
      };
    case 'layout.select':
      return { ...state, viewer: { ...v, selected_layout_id: event.payload.layout_id ?? null } };
    case 'sound.setScenario':
      return { ...state, viewer: { ...v, sound_scenario: event.payload } };
    case 'chat.user':
      return { ...state, chat: push(state.chat, { kind: 'user', id: event.id, text: event.payload.text, via: 'chat', source: event.source, member_id: event.member_id ?? null }) };
    case 'voice.transcript':
      if (!event.payload.final) return { ...state, interim: { source: event.source, text: event.payload.text } };
      return {
        ...state,
        interim: null,
        chat: event.payload.text.trim() ? push(state.chat, { kind: 'user', id: event.id, text: event.payload.text, via: 'voice', source: event.source, member_id: event.member_id ?? null }) : state.chat,
      };
    case 'agent.token': {
      const { message_id, delta } = event.payload;
      const at = state.chat.findIndex((c) => c.kind === 'agent' && c.id === message_id);
      if (at < 0) return { ...state, chat: push(state.chat, { kind: 'agent', id: message_id, text: delta, streaming: true, member_id: event.member_id ?? null }) };
      const chat = [...state.chat];
      const item = chat[at] as Extract<ChatItem, { kind: 'agent' }>;
      chat[at] = { ...item, text: item.text + delta, streaming: true };
      return { ...state, chat };
    }
    case 'agent.message': {
      const { message_id, text } = event.payload;
      const at = state.chat.findIndex((c) => c.kind === 'agent' && c.id === message_id);
      if (at < 0) return { ...state, chat: push(state.chat, { kind: 'agent', id: message_id, text, streaming: false, member_id: event.member_id ?? null }) };
      const chat = [...state.chat];
      chat[at] = { kind: 'agent', id: message_id, text, streaming: false, member_id: event.member_id ?? null };
      return { ...state, chat };
    }
    case 'agent.action':
      return { ...state, chat: push(state.chat, { kind: 'action', id: event.id, tool: event.payload.tool, summary: event.payload.summary, member_id: event.member_id ?? null }) };
    case 'agent.error':
      return {
        ...state,
        chat: push(
          state.chat.map((c) => (c.kind === 'agent' && c.streaming && c.member_id === (event.member_id ?? null) ? { ...c, streaming: false } : c)),
          { kind: 'error', id: event.id, message: event.payload.message, member_id: event.member_id ?? null },
        ),
      };
    case 'family.note': {
      const p = event.payload;
      return { ...state, chat: push(state.chat, { kind: 'note', id: p.note_id, text: p.text, room_id: p.room_id ?? null, member_id: p.member_id }) };
    }
    case 'family.ask': {
      const p = event.payload;
      return { ...state, chat: push(state.chat, { kind: 'ask', id: p.ask_id, from_member_id: p.from_member_id, to_member_id: p.to_member_id, question: p.question, answer: null }) };
    }
    case 'family.answer': {
      const p = event.payload;
      const at = state.chat.findIndex((c) => c.kind === 'ask' && c.id === p.ask_id);
      if (at < 0) {
        return { ...state, chat: push(state.chat, { kind: 'ask', id: p.ask_id, from_member_id: p.from_member_id, to_member_id: p.to_member_id, question: '', answer: p.answer }) };
      }
      const chat = [...state.chat];
      chat[at] = { ...(chat[at] as Extract<ChatItem, { kind: 'ask' }>), answer: p.answer };
      return { ...state, chat };
    }
    case 'job.progress':
      return { ...state, jobs: { ...state.jobs, [event.payload.job_id]: event.payload } };
    case 'viewer.walk': {
      // The Tour plays the walk from the event itself. Here the session only learns where it ends.
      // goto_seq stays as it is, so the goto effect doesn't also jump the camera.
      const last = event.payload.sweep_ids[event.payload.sweep_ids.length - 1] ?? null;
      return { ...state, viewer: { ...v, room_id: event.payload.to_room_id, sweep_id: last, view: null } };
    }
    case 'catalog.suggest': {
      const p = event.payload;
      return { ...state, chat: push(state.chat, { kind: 'catalog', id: event.id, property_id: p.property_id, room_id: p.room_id ?? null, near_item_id: p.near_item_id ?? null, pieces: p.pieces, member_id: event.member_id ?? null }) };
    }
    case 'remote.view':
      return { ...state, remoteView: event.payload.view };
    case 'catalog.picked': // the user's click; the agent is told by the server
    case 'layout.confirm': // "keep this layout": the agent records it; the view already shows it
    case 'viewer.step': // a camera move, like viewer.look
    case 'viewer.zoom':
    case 'layout.changed': // other screens reload the layouts (SessionProvider)
    case 'furniture.select': // the tour points the piece out
    case 'voice.spoken': // a phone opens its microphone for the answer
    case 'viewer.look':
    case 'capture.upload':
    case 'sound.placeSource': // TODO(A): C's place-a-sound demo (docs/handoffs/stream-a)
    case 'sound.clearSource':
    case 'agent.turn': // brackets one agent turn; nothing stored yet (use it for a typing indicator)
      return state; // transient: handled by event listeners, not stored
    default: {
      const _exhaustive: never = event;
      return state;
    }
  }
}

export function toViewerState(v: ViewerSlice): ViewerState {
  return { mode: v.mode, room_id: v.room_id, datetime: v.datetime, sound_scenario: v.sound_scenario, shown_layout_ids: v.shown_layout_ids, playback: v.playback };
}
