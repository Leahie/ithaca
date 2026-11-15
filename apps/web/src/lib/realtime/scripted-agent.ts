import type { EventPayloads, EventType, NoiseScenario, PropertyTwin, Room, ViewerState } from '@ithaca/schema';
import type { ApiClient } from '@/lib/api';
import { roomName, roomNames } from '@/lib/rooms';
import { fromZonedParts, localDate, toZonedIso, zonedParts } from '@/lib/time';

/**
 * Offline stand-in for the agent (fixture mode only). It answers a fixed set of prompts by
 * streaming tokens and emitting the same UI events the real agent's tools emit, so the dock
 * and every tab can be exercised with no backend. It reads numbers from the reports (A3).
 */
type Emit = <T extends EventType>(type: T, payload: EventPayloads[T]) => void;

export interface ScriptedAgentContext {
  api: ApiClient;
  propertyId: string;
  viewer(): ViewerState;
}

const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];
const DAYPARTS: [RegExp, number][] = [
  [/sunrise|dawn|early/, 7],
  [/morning/, 9],
  [/noon|midday|lunch/, 12],
  [/afternoon/, 15],
  [/sunset|evening|dusk/, 18],
  [/night/, 21],
];

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

function findRoom(twin: PropertyTwin, text: string): Room | undefined {
  const t = text.toLowerCase();
  const names = roomNames(twin);
  // Longest name first, so "living room 2" wins over "living room".
  const rooms = [...twin.rooms].sort((a, b) => names.get(b.room_id)!.length - names.get(a.room_id)!.length);
  const exact = rooms.find((r) => t.includes(names.get(r.room_id)!.toLowerCase()));
  if (exact) return exact;
  // "the kitchen" matches "Kitchen 1": the first room with that label, in list order.
  return [...twin.rooms]
    .sort((a, b) => names.get(a.room_id)!.localeCompare(names.get(b.room_id)!, 'en', { numeric: true }))
    .find((r) => t.includes(r.label.toLowerCase()));
}

export async function scriptedAgentTurn(text: string, emit: Emit, ctx: ScriptedAgentContext): Promise<void> {
  const twin = await ctx.api.getTwin(ctx.propertyId);
  const t = text.toLowerCase();
  const room = findRoom(twin, text);
  const nameOf = (r: Room) => roomName(twin, r.room_id);
  const messageId = crypto.randomUUID();

  const say = async (reply: string) => {
    for (const word of reply.split(/(?<=\s)/)) {
      emit('agent.token', { message_id: messageId, delta: word });
      await sleep(22);
    }
    emit('agent.message', { message_id: messageId, text: reply });
  };
  const goto = (r: Room) => {
    emit('agent.action', { tool: 'viewer_goto', summary: `Moved you to ${nameOf(r)}` });
    emit('viewer.goto', { room_id: r.room_id });
  };
  const setMode = (mode: ViewerState['mode'], label: string) => {
    if (ctx.viewer().mode === mode) return;
    emit('agent.action', { tool: 'viewer_set_mode', summary: `Opened ${label}` });
    emit('viewer.setMode', { mode });
  };

  const monthIndex = MONTHS.findIndex((m) => t.includes(m) || t.includes(m.slice(0, 3) + ' '));
  const daypart = DAYPARTS.find(([re]) => re.test(t));
  const wantsSun = /sun|light|bright|shadow/.test(t);

  if (/lay ?out|layout|arrange|furnish|where .* (sofa|couch)/.test(t)) {
    const layouts = await ctx.api.listLayouts(ctx.propertyId, room?.room_id);
    if (!layouts.length) return say(`I have no saved layouts${room ? ` for ${nameOf(room)}` : ''} yet. Upload a piece of furniture on the Me page and ask again.`);
    const target = twin.rooms.find((r) => r.room_id === layouts[0]!.room_id);
    setMode('design', 'the Design tab');
    if (target) goto(target);
    emit('agent.action', { tool: 'design_show_variants', summary: `Showing ${layouts.length} layouts` });
    emit('design.showVariants', { layout_ids: layouts.map((l) => l.layout_id) });
    return say(`Here are ${layouts.length} ways to arrange ${target ? nameOf(target) : 'the room'}. My pick is the first one: ${layouts[0]!.rationale} Choose one, or tell me what to change.`);
  }

  if (/noise|loud|quiet|sound|traffic|hear/.test(t)) {
    const target = room ?? twin.rooms.find((r) => /bedroom/i.test(r.label)) ?? twin.rooms[0];
    if (!target) return say('This home has no rooms in its twin yet.');
    const scenario: NoiseScenario = {
      glazing: /triple/.test(t) ? 'triple' : /double/.test(t) ? 'double' : /single/.test(t) ? 'single' : null,
      add_carpet: /carpet|rug/.test(t),
      include_furniture: true,
    };
    const report = await ctx.api.getNoise(ctx.propertyId, target.room_id, scenario);
    const peak = report.hourly.reduce((a, b) => (b.laeq_dba > a.laeq_dba ? b : a));
    const calm = report.hourly.reduce((a, b) => (b.laeq_dba < a.laeq_dba ? b : a));
    setMode('sound', 'the Sound tab');
    emit('agent.action', { tool: 'sound_set_scenario', summary: `Set the sound scenario for ${nameOf(target)}` });
    emit('sound.setScenario', { room_id: target.room_id, scenario });
    return say(
      `${nameOf(target)} peaks at ${peak.laeq_dba} dBA around ${peak.hour}:00 and drops to ${calm.laeq_dba} dBA around ${calm.hour}:00. Its reverberation time is ${report.rt60_s} s. Press play on the Sound tab to hear it.`,
    );
  }

  if (monthIndex >= 0 || daypart || wantsSun) {
    const now = zonedParts(new Date(ctx.viewer().datetime), twin.timezone);
    const when = fromZonedParts(
      { year: now.year, month: monthIndex >= 0 ? monthIndex + 1 : now.month, day: monthIndex >= 0 ? 21 : now.day, hour: daypart ? daypart[1] : monthIndex >= 0 ? 9 : now.hour, minute: 0 },
      twin.timezone,
    );
    const iso = toZonedIso(when, twin.timezone);
    setMode('tour', 'the Tour');
    if (room) goto(room);
    emit('agent.action', { tool: 'viewer_set_time', summary: `Set the time to ${iso.slice(0, 16).replace('T', ' ')}` });
    emit('viewer.setTime', { datetime: iso });
    if (room) {
      const date = localDate(iso, twin.timezone);
      const report = await ctx.api.getSun(ctx.propertyId, room.room_id, date, date);
      const hours = report.daily[0]?.direct_sun_hours;
      const caveat = twin.quality.alignment === 'gps_only' ? ' The compass alignment of this twin is GPS only, so treat the direction as approximate.' : '';
      return say(hours === undefined ? `Here is ${nameOf(room)} at that time.` : `${nameOf(room)} gets about ${hours} hours of direct sun on ${date}.${caveat}`);
    }
    return say('Here is the house at that time. Press play on the bar below to watch the light move through the day.');
  }

  if (/neighbou?rhood|area|nearby|comps?|school|grocery|park/.test(t)) {
    const area = await ctx.api.getArea(ctx.propertyId);
    setMode('neighborhood', 'the Neighborhood tab');
    const nearest = area.amenities[0];
    return say(`I found ${area.amenities.length} places nearby and ${area.comps.length} comparable sales.${nearest ? ` The closest is ${nearest.name}, ${nearest.distance_m} m away.` : ''}`);
  }

  if (room) {
    setMode('tour', 'the Tour');
    goto(room);
    return say(`This is ${nameOf(room)}.`);
  }

  return say(
    'I am the scripted offline agent. Try: "take me to the kitchen", "show me December morning", "show layouts", "how loud is the bedroom with single-pane windows", or "what is nearby".',
  );
}
