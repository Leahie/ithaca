import type { AnyEvent, ClientKind, EventSource, JobProgressPayload, SoundSetScenarioPayload, ViewerMode, ViewerState } from '@ithaca/schema';

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
  /** Bumps on every goto, so a repeated goto to the same room still moves the camera. */
  goto_seq: number;
}

export type ChatItem =
  | { kind: 'user'; id: string; text: string; via: 'chat' | 'voice'; source: EventSource }
  | { kind: 'agent'; id: string; text: string; streaming: boolean }
  | { kind: 'action'; id: string; tool: string; summary: string }
  | { kind: 'error'; id: string; message: string };

export interface CoreState {
  viewer: ViewerSlice;
  chat: ChatItem[];
  /** Live, not yet final, voice transcript. */
  interim: { source: EventSource; text: string } | null;
  jobs: Record<string, JobProgressPayload>;
  peers: Record<ClientKind, boolean>;
  /** sweep_id -> room_id, from the twin, so a goto by sweep also updates the room. */
  sweepRooms: Record<string, string | null>;
}

const MAX_CHAT = 300;

export function initialCore(datetime: string): CoreState {
  return {
    viewer: { mode: 'tour', room_id: null, sweep_id: null, datetime, sound_scenario: null, shown_layout_ids: [], selected_layout_id: null, goto_seq: 0 },
    chat: [],
    interim: null,
    jobs: {},
    peers: { desktop: false, phone: false },
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
        },
      };
    }
    case 'session.joined':
      return { ...state, peers: { ...state.peers, [event.payload.client]: true } };
    case 'session.left':
      return { ...state, peers: { ...state.peers, [event.payload.client]: false } };
    case 'viewer.goto': {
      const { room_id, sweep_id } = event.payload;
      if (room_id) return { ...state, viewer: { ...v, room_id, sweep_id: null, goto_seq: v.goto_seq + 1 } };
      if (sweep_id) return { ...state, viewer: { ...v, sweep_id, room_id: state.sweepRooms[sweep_id] ?? v.room_id, goto_seq: v.goto_seq + 1 } };
      return state;
    }
    case 'viewer.setTime':
      return event.payload.datetime ? { ...state, viewer: { ...v, datetime: event.payload.datetime } } : state;
    case 'viewer.setMode':
      return { ...state, viewer: { ...v, mode: event.payload.mode } };
    case 'design.showVariants':
      return { ...state, viewer: { ...v, shown_layout_ids: event.payload.layout_ids } };
    case 'layout.select':
      return { ...state, viewer: { ...v, selected_layout_id: event.payload.layout_id } };
    case 'sound.setScenario':
      return { ...state, viewer: { ...v, sound_scenario: event.payload } };
    case 'chat.user':
      return { ...state, chat: push(state.chat, { kind: 'user', id: event.id, text: event.payload.text, via: 'chat', source: event.source }) };
    case 'voice.transcript':
      if (!event.payload.final) return { ...state, interim: { source: event.source, text: event.payload.text } };
      return {
        ...state,
        interim: null,
        chat: event.payload.text.trim() ? push(state.chat, { kind: 'user', id: event.id, text: event.payload.text, via: 'voice', source: event.source }) : state.chat,
      };
    case 'agent.token': {
      const { message_id, delta } = event.payload;
      const at = state.chat.findIndex((c) => c.kind === 'agent' && c.id === message_id);
      if (at < 0) return { ...state, chat: push(state.chat, { kind: 'agent', id: message_id, text: delta, streaming: true }) };
      const chat = [...state.chat];
      const item = chat[at] as Extract<ChatItem, { kind: 'agent' }>;
      chat[at] = { ...item, text: item.text + delta, streaming: true };
      return { ...state, chat };
    }
    case 'agent.message': {
      const { message_id, text } = event.payload;
      const at = state.chat.findIndex((c) => c.kind === 'agent' && c.id === message_id);
      if (at < 0) return { ...state, chat: push(state.chat, { kind: 'agent', id: message_id, text, streaming: false }) };
      const chat = [...state.chat];
      chat[at] = { kind: 'agent', id: message_id, text, streaming: false };
      return { ...state, chat };
    }
    case 'agent.action':
      return { ...state, chat: push(state.chat, { kind: 'action', id: event.id, tool: event.payload.tool, summary: event.payload.summary }) };
    case 'agent.error':
      return {
        ...state,
        chat: push(
          state.chat.map((c) => (c.kind === 'agent' && c.streaming ? { ...c, streaming: false } : c)),
          { kind: 'error', id: event.id, message: event.payload.message },
        ),
      };
    case 'job.progress':
      return { ...state, jobs: { ...state.jobs, [event.payload.job_id]: event.payload } };
    case 'viewer.look':
    case 'capture.upload':
      return state; // transient: handled by event listeners, not stored
    default: {
      const _exhaustive: never = event;
      return state;
    }
  }
}

export function toViewerState(v: ViewerSlice): ViewerState {
  return { mode: v.mode, room_id: v.room_id, datetime: v.datetime, sound_scenario: v.sound_scenario, shown_layout_ids: v.shown_layout_ids };
}
