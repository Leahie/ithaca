"""Connects the hub to the agent. Owner: B.

Listens to hub events and decides when the agent gets a turn:
- `chat.user`, and `voice.transcript` with final=true  -> a normal turn
- desktop joins a session for the first time            -> <event type="property_opened">
- a client moves to a different room                    -> <event type="room_entered"> (proactive)

Then it runs the turn and publishes what the agent does as `agent.*` and UI events.

It also keeps the tour log (agent/tour.py) and runs the observer in the background: after every
OBSERVE_EVERY_SIGNALS things the user does (room changes they make, messages they send), at most
once per OBSERVE_MIN_INTERVAL_S, and once more when the desktop leaves. If the observer returns a
note, the guide gets an <event type="observer_note"> turn.

Proactive turns are skipped while the agent is busy and are limited to one per
PROACTIVE_COOLDOWN_S, so the agent doesn't talk over the user. One turn runs at a time per session.

Households: a message from a member's phone (the event carries member_id) runs that member's own
agent, with their user_id, their memory and their own thread, and everything it says is stamped
with their member_id. Members' agents run side by side (one lock per member). A member who
connects gets one short greeting from their agent. Proactive tour turns and the observer stay
with the session's own (desktop) agent, so a family of five agents doesn't talk at once.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from packages.schema.events import EventEnvelope, make_event
from services.api.agent import design_gate, family, house_plans
from services.api.agent.context import AgentContext
from services.api.agent.design_tools import BACKGROUND_DESIGN
from services.api.agent.provider import TurnRunner
from services.api.agent.runner import event_message
from services.api.agent.tour import TOUR_LOGS, TourLog
from services.api.realtime.hub import Hub, SessionRoom

from services.api.twin.service import get_twin
from services.api.agent import prompts
log = logging.getLogger(__name__)

PROACTIVE_COOLDOWN_S = 60.0
OBSERVE_EVERY_SIGNALS = 5
OBSERVE_MIN_INTERVAL_S = 120.0

# Runs the observer once for a session; returns a note for the guide or None. Blocking.
Observe = Callable[[AgentContext], "str | None"]
# Runs the interior designer once for a room; returns its report (layout ids and notes). Blocking.
# The last argument hears it as it works: ("thought", text) and ("ui", {"type", "payload"}).
Design = Callable[[AgentContext, str, str, Callable[[str, Any], None]], str]


def action_summary(tool: str, args: dict) -> str:
    """Short text for the UI chip of one tool call."""
    path = str(args.get("file_path", ""))
    if tool in ("write_file", "edit_file") and path.startswith("/memories/"):
        return f"Updated memory: {path.removeprefix('/memories/')}"
    return {
        "viewer_goto": "Moving the view",
        "viewer_walk": "Walking you there",
        "viewer_step": "Moving a little",
        "viewer_turn": "Turning the view",
        "viewer_zoom": "Zooming",
        "design_in_background": "Starting on the room layout",
        "catalog_sets": "Looking at the furniture sets",
        "furnish_with_set": "Furnishing the room with a set",
        "task": f"Asking the {args.get('subagent_type', 'helper')}",
        "inventory_search": "Looking at your furniture",
        "propose_layouts": "Arranging the room",
        "catalog_search": "Looking through the furniture catalog",
        "catalog_add": "Adding a piece to your furniture",
        "catalog_suggest": "Showing you some pieces",
        "remove_from_room": "Taking a piece out of the room",
        "delete_from_inventory": "Deleting a piece from your furniture",
        "design_apply_layout": "Placing that option in the room",
        "design_show_variants": "Showing layouts",
        "area_profile": "Looking up the listing and nearby homes",
        "area_search": f"Searching nearby for {args.get('query', 'places')}",
        "sound_heard_from": "Working out how far the sound carries",
        "sound_place_source": "Placing a sound in the room",
        "viewer_set_mode": f"Switching to the {args.get('mode', '')} view",
        "viewer_set_time": "Setting the time of day",
        "sun_by_room": "Checking where the sun falls",
        "sun_report": "Adding up sun hours",
        "daylight_at": "Checking how bright the rooms are",
        "get_room_geometry": "Measuring the room",
        "list_rooms": "Looking over the rooms",
        "family_list_members": "Checking who is here",
        "family_post_note": "Posting to the family board",
        "family_read_board": "Reading the family board",
        "family_ask_member": f"Asking {args.get('name', 'a family member')}'s agent",
    }.get(tool, tool.replace("_", " "))


def others_plans(context: AgentContext) -> str:
    """What this agent may know of the others arranging the same house: who has plans for which
    room, and nothing more (agent/house_plans.py)."""
    plans = house_plans.of_others(context)
    if not plans:
        return ""
    names = prompts.room_short_names(get_twin(context.property_id))
    lines = "".join(f"\n- {p.who} {p.status} for {names.get(p.room_id, p.room_id)} (room_id {p.room_id})" for p in plans)
    return f"<others_plans>{lines}\n</others_plans>\n"


GREET_AGAIN_S = 3 * 3600  # a welcome is not repeated within this long, whatever reconnects


def room_furniture(room: SessionRoom, context: AgentContext) -> str:
    """For every turn: what stands in the room the user is in, and whether layout options are
    waiting there. What the guide remembers from earlier turns goes stale the moment the user
    keeps a layout or moves a piece on their phone."""
    from services.api.furniture.store import furniture_store

    here = room.state.room_id
    if not here:
        return ""
    count = len(furniture_store().house_pieces(context.furniture_user, context.property_id, here))
    waiting = design_gate._review.get(design_gate.key(room.session_id, context.user_id)) == here
    state = "layout options are on screen, not kept yet" if waiting else "no layout options are waiting here"
    return f'<this_room pieces_in_house="{count}" state="{state}"/>\n'


def design_queue(conversation: str, room_id: str | None, property_id: str) -> str:
    """For a layouts_ready turn: the other rooms the user asked for and how far each is, so the
    guide can say what comes next and not forget one."""
    busy, held = design_gate.others(conversation, room_id or "")
    if not busy and not held:
        return ""
    names = prompts.room_short_names(get_twin(property_id))
    lines = [f"\n- {names.get(r, r)} (room_id {r}): finished, comes up as soon as they have decided on this room" for r in held]
    lines += [f"\n- {names.get(r, r)} (room_id {r}): still being arranged" for r in busy]
    return f"\n<other_rooms>{''.join(lines)}\n</other_rooms>"


def spoken_cue(tool: str, args: dict, property_id: str) -> str | None:
    """One short line to say aloud the moment a tool starts, so an action in the house (or a long
    wait) is never silent. None for tools that are quick and change nothing the user can see."""
    if tool in ("viewer_walk", "viewer_goto"):
        room_id = args.get("to_room_id") or args.get("room_id")
        try:
            name = prompts.room_short_names(get_twin(property_id)).get(room_id)
        except Exception:  # no twin for this property: say it without the name
            name = None
        if tool == "viewer_walk":
            return f"Walking you to the {name}." if name else "Walking you there."
        return f"Here is the {name}." if name else None
    if tool == "task":
        return "Give me a moment while I work that out."
    if tool in ("area_profile", "area_search"):
        return "Let me look that up."
    return None


class AgentBridge:
    def __init__(self, hub: Hub, runner: TurnRunner, observe: Observe | None = None, design: Design | None = None) -> None:
        self.hub = hub
        self.runner = runner
        self.observe = observe
        self.design = design
        self._running: dict[str, threading.Event] = {}  # turn key -> "stop, the user has spoken"
        self._design_runs: dict[tuple[str, str], int] = {}  # (turn key, room_id) -> the designer run that counts
        self._phone_screen: dict[tuple[str, str | None], str] = {}  # (session, member) -> the screen their phone is on
        self._signals: dict[str, int] = {}  # user actions since the observer last ran
        self._last_observed: dict[str, float] = {}
        self._observing: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_room: dict[str, str | None] = {}
        self._last_proactive: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()
        family.set_runner(runner)  # so one member's agent can ask another's
        hub.subscribe(self.on_event)

    def on_event(self, room: SessionRoom, event: EventEnvelope) -> None:
        sid, p = room.session_id, event.payload
        by_user = event.source in ("desktop", "phone")
        log_ = TOUR_LOGS.setdefault(sid, TourLog())

        if event.type == "voice.transcript" and p.get("final") and not p.get("text", "").strip():
            return  # the microphone closed without hearing anything: not a turn
        waiting, who = None, None
        if event.type == "chat.user" or (event.type == "voice.transcript" and p.get("final")) or (event.type == "layout.confirm" and by_user):
            # The user has spoken or kept a layout: the room that was waiting for them is settled,
            # and the guide may go on to another one (agent/design_gate.py).
            who = event.member_id if room.household_id else None
            waiting = design_gate.user_spoke(design_gate.key(sid, self._context(room, who).user_id))
        if event.type == "chat.user" or (event.type == "voice.transcript" and p.get("final")):
            log_.heard(p["text"])
            self._signal(room)
            self._start(room, p["text"], proactive=False, member_id=event.member_id, interrupts=True)
        if (event.type == "chat.user" or (event.type == "voice.transcript" and p.get("final")) or (event.type == "layout.confirm" and by_user)) and waiting:
            # They have judged one room: the next one that was planned meanwhile comes up now.
            conversation = design_gate.key(sid, self._context(room, who).user_id)
            waiting += design_queue(conversation, design_gate._review.get(conversation), room.property_id)
            asyncio.get_running_loop().call_soon(lambda: self._start(room, waiting, proactive=False, member_id=who))
        elif event.type == "session.joined" and p.get("member_id") and room.household_id:
            # Once per visit, not per connection or session: a phone reconnects often (a locked
            # screen, a reload, a new session on the big screen), and each must not be welcomed again.
            if self.hub.first_greeting(sid, p["member_id"]) and self.hub.once(f"hello:{p['member_id']}:{room.property_id}", GREET_AGAIN_S):
                self._start(room, event_message("member_joined"), proactive=False, member_id=p["member_id"])
        elif event.type == "session.joined" and p.get("client") == "desktop":
            if self.hub.first_greeting(sid, "desktop") and self.hub.once(f"hello:{self._context(room, None).memory_user}:{room.property_id}", GREET_AGAIN_S):
                self._start(room, event_message("property_opened"), proactive=False)
        elif event.type == "viewer.goto" and p.get("room_id"):
            changed = self._last_room.get(sid) != p["room_id"]
            self._last_room[sid] = p["room_id"]
            log_.enter_room(p["room_id"], by_user=by_user)
            if changed and by_user:
                self._signal(room)
                self._start(room, event_message("room_entered"), proactive=True)
        elif event.type == "viewer.walk":
            self._last_room[sid] = p["to_room_id"]
            log_.enter_room(p["to_room_id"], by_user=False)
        elif event.type == "layout.confirm" and by_user:  # a click on an option only previews it (layout.select)
            from services.api.agent.design_tools import describe_layout
            from services.api.furniture import house
            from services.api.furniture.store import furniture_store

            try:  # the kept layout goes into the house (F8), and every screen reloads
                house.apply(room.user_id, p["layout_id"], event.member_id or room.user_id)
            except house.FurnitureError as e:
                log.warning("layout %s not applied: %s", p["layout_id"], e.detail)
            else:
                asyncio.get_running_loop().call_soon(self.hub.publish, make_event(sid, "server", "layout.changed", {"layout_id": p["layout_id"]}))
            kept = furniture_store().get_proposal(room.user_id, p["layout_id"])
            if kept is not None:  # the others' agents hear that this room is taken, and by whom
                house_plans.note(self._context(room, event.member_id if room.household_id else None), kept.room_id, house_plans.KEPT)
            picked = describe_layout(room.user_id, room.property_id, p["layout_id"])
            if picked:
                self._signal(room)
                # To the agent of whoever kept it: theirs is the conversation that showed the options.
                self._start(room, event_message("layout_chosen", picked), proactive=False, member_id=event.member_id if room.household_id else None)
        elif event.type == "catalog.picked" and by_user:
            from services.api.furniture.store import furniture_store

            item = furniture_store().get_item(p["item_id"])
            if item:
                self._signal(room)
                note = f"{item.label} ({item.item_class}), item_id {item.item_id}, room_id {p.get('room_id') or 'the room they are in'}"
                self._start(room, event_message("catalog_picked", note), proactive=False)
        elif event.type == "remote.view":
            self._phone_screen[(sid, event.member_id)] = p.get("view", "")
        elif event.type == "viewer.setMode":
            log_.set_view(p["mode"])
        elif event.type == "viewer.setTime" and by_user:
            log_.set_time(p["datetime"])
        elif event.type == "session.left" and p.get("client") == "desktop":
            self._maybe_observe(room, final=True)

    # --- observer -------------------------------------------------------------------------------

    def _signal(self, room: SessionRoom) -> None:
        self._signals[room.session_id] = self._signals.get(room.session_id, 0) + 1
        self._maybe_observe(room)

    def _maybe_observe(self, room: SessionRoom, final: bool = False) -> None:
        sid = room.session_id
        if self.observe is None or sid in self._observing:
            return
        signals = self._signals.get(sid, 0)
        waited = time.monotonic() - self._last_observed.get(sid, -1e9) >= OBSERVE_MIN_INTERVAL_S
        if not ((final and signals >= 2) or (signals >= OBSERVE_EVERY_SIGNALS and waited)):
            return
        self._signals[sid] = 0
        self._last_observed[sid] = time.monotonic()
        self._observing.add(sid)
        self._keep(asyncio.create_task(self._run_observer(room, deliver=not final)))

    async def _run_observer(self, room: SessionRoom, deliver: bool) -> None:
        sid = room.session_id
        context = self._context(room, None)  # the guide's own memory: the family's in a family session
        try:
            note = await asyncio.to_thread(self.observe, context)
        except Exception:
            log.exception("observer run failed")  # the tour goes on without it
            return
        else:
            log.info("observer ran for session %s: %s", sid, f"note: {note}" if note else "nothing to tell the guide")
        finally:
            self._observing.discard(sid)
        if note and deliver:
            self._last_proactive[sid] = time.monotonic()  # counts as the guide's proactive turn
            self._start(room, event_message("observer_note", note), proactive=False)

    def _keep(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _context(self, room: SessionRoom, member_id: str | None) -> AgentContext:
        """Whose agent runs: a member's own (their user_id and memory), or the session's."""
        if member_id and room.household_id:
            from services.api.realtime.households import households

            m = households().member(room.household_id, member_id)
            return AgentContext(
                user_id=member_id,
                property_id=room.property_id,
                session_id=room.session_id,
                household_id=room.household_id,
                member_name=m.name if m else None,
                house_user_id=room.user_id,
            )
        return AgentContext(
            user_id=room.user_id,
            property_id=room.property_id,
            session_id=room.session_id,
            household_id=room.household_id,
        )

    def _start(self, room: SessionRoom, text: str, proactive: bool, member_id: str | None = None, interrupts: bool = False) -> None:
        sid = room.session_id
        if not room.household_id:
            member_id = None  # outside a household a phone speaks for the session's user, as before
        key = f"{sid}:{member_id}" if member_id else sid
        lock = self._locks.setdefault(key, asyncio.Lock())
        if interrupts and (running := self._running.get(key)) is not None:
            running.set()  # the user has spoken: the turn in flight stops talking and steps aside
        if proactive:
            if lock.locked() or time.monotonic() - self._last_proactive.get(sid, -1e9) < PROACTIVE_COOLDOWN_S:
                return
            self._last_proactive[sid] = time.monotonic()
        self._keep(asyncio.create_task(self._run_turn(room, text, lock, member_id)))

    async def _run_turn(self, room: SessionRoom, text: str, lock: asyncio.Lock, member_id: str | None = None) -> None:
        sid = room.session_id
        turn_key = f"{sid}:{member_id}" if member_id else sid

        def emit(type: str, payload: dict) -> None:
            try:
                self.hub.publish(make_event(sid, "agent", type, payload, member_id=member_id))
            except (KeyError, ValueError) as e:  # an event the schema doesn't know or accept
                log.warning("agent event %s dropped: %s", type, e)

        key = turn_key
        async with lock:
            s = room.state
            phone = self._phone_screen.get((sid, member_id), "")
            where = f'<viewer mode="{s.mode}" room_id="{s.room_id or ""}" datetime="{s.datetime or ""}" phone="{phone}"/>\n'
            context = self._context(room, member_id)
            where += others_plans(context) + room_furniture(room, context)
            # The conversation belongs to the person (or the family's guide) and the house, not to
            # this browser session: a new visit goes on from the last one.
            thread_id = context.thread_id
            loop, queue = asyncio.get_running_loop(), asyncio.Queue()
            stop = threading.Event()
            self._running[key] = stop

            def work() -> None:  # the runner is blocking, so it gets its own thread
                turn = self.runner(where + text, context, thread_id)
                try:
                    for item in turn:
                        if stop.is_set():
                            break  # nobody is listening any more: stop at this step
                        loop.call_soon_threadsafe(queue.put_nowait, item)
                except Exception as e:
                    log.exception("agent turn failed")
                    loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))
                finally:
                    close = getattr(turn, "close", None)
                    if close:
                        close()
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            emit("agent.turn", {"status": "started"})
            worker = asyncio.create_task(asyncio.to_thread(work))
            message_id = uuid.uuid4().hex
            quiet = True  # the guide has said nothing since the turn began or since its last tool call
            cued: set[str] = set()
            interrupted = False
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    if stop.is_set():
                        interrupted = True
                        break
                    continue
                if item is None:
                    break
                if stop.is_set():
                    interrupted = True
                    break
                kind, *rest = item
                if kind == "token":
                    quiet = False
                    emit("agent.token", {"message_id": message_id, "delta": rest[0]})
                elif kind == "action":
                    # Said at once, as its own message, unless the guide has just spoken itself or
                    # this turn already had this kind of line.
                    cue = spoken_cue(rest[0], rest[1], room.property_id)
                    if cue and quiet and rest[0] not in cued:
                        cued.add(rest[0])
                        emit("agent.message", {"message_id": uuid.uuid4().hex, "text": cue})
                    quiet = True
                    emit("agent.action", {"tool": rest[0], "summary": action_summary(rest[0], rest[1])})
                elif kind == "ui":
                    if rest[0]["type"] == BACKGROUND_DESIGN:
                        self._design_in_background(room, member_id, key, **rest[0]["payload"])
                    else:
                        emit(rest[0]["type"], rest[0]["payload"])
                elif kind == "message":
                    emit("agent.message", {"message_id": message_id, "text": rest[0]})
                elif kind == "error":
                    emit("agent.error", {"message": rest[0]})
            if self._running.get(key) is stop:
                del self._running[key]
            if interrupted:
                # The thread finishes the step it is in (a model call or a tool) and then stops;
                # the next turn does not wait for it.
                self._keep(worker)
            else:
                await worker
            emit("agent.turn", {"status": "ended"})

    def _design_in_background(self, room: SessionRoom, member_id: str | None, key: str, room_id: str, brief: str) -> None:
        """Run the designer off to the side. While it works, its short thoughts go to the user as
        guide messages and each set of layouts it solves is shown at once; when it is done the
        guide gets an event turn with its report, queued like any other turn. A new request for the
        same room (say, after the user answered a question) replaces a run still going: the old
        run's thoughts, layouts and report are dropped."""
        if self.design is None:
            design_gate.dropped(design_gate.key(room.session_id, self._context(room, member_id).user_id), room_id)
            self._start(room, event_message("layouts_failed", "The designer is not available in this setup."), proactive=False, member_id=member_id)
            return
        job = (key, room_id)
        run_no = self._design_runs[job] = self._design_runs.get(job, 0) + 1
        context = self._context(room, member_id)
        loop = asyncio.get_running_loop()

        def current() -> bool:
            return self._design_runs.get(job) == run_no

        def publish(type: str, payload: dict) -> None:
            if not current():
                return
            try:
                self.hub.publish(make_event(room.session_id, "agent", type, payload, member_id=member_id))
            except (KeyError, ValueError) as e:
                log.warning("designer event %s dropped: %s", type, e)

        said: list[str] = []

        def progress(kind: str, data: Any) -> None:  # called from the designer's thread
            if kind == "thought":
                said.append(data)
                loop.call_soon_threadsafe(publish, "agent.message", {"message_id": uuid.uuid4().hex, "text": data})
            elif kind == "ui":
                loop.call_soon_threadsafe(publish, data["type"], data["payload"])

        async def run() -> None:
            conversation = design_gate.key(room.session_id, context.user_id)
            try:
                report = await asyncio.to_thread(self.design, context, room_id, brief, progress)
            except Exception as e:
                log.exception("background designer failed")
                if not current():
                    return  # a newer run for this room took over, gate and all
                message = event_message("layouts_failed", f"room_id: {room_id}\n{e}")
                design_gate.dropped(conversation, room_id)
            else:
                if not current():
                    return
                # The thoughts reached the user as the guide's own words; this is the guide's only record of them.
                aloud = "".join(f"\n- {t}" for t in said)
                aloud = f"\nWhile it worked, you already told the user:{aloud}\n" if aloud else ""
                message = event_message("layouts_ready", f"room_id: {room_id}{aloud}\n{report}")
                house_plans.note(context, room_id, house_plans.CHOOSING)
                if not design_gate.ready(conversation, room_id, message):
                    return  # another room is waiting for the user: this one is shown after it
                message += design_queue(conversation, room_id, room.property_id)
            self._start(room, message, proactive=False, member_id=member_id)

        self._keep(asyncio.create_task(run()))
