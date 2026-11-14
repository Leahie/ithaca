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
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable

from packages.schema.events import Envelope, make_event
from services.api.agent.context import AgentContext
from services.api.agent.provider import TurnRunner
from services.api.agent.runner import event_message
from services.api.agent.tour import TOUR_LOGS, TourLog
from services.api.realtime.hub import Hub, SessionRoom

log = logging.getLogger(__name__)

PROACTIVE_COOLDOWN_S = 60.0
OBSERVE_EVERY_SIGNALS = 5
OBSERVE_MIN_INTERVAL_S = 120.0

# Runs the observer once for a session; returns a note for the guide or None. Blocking.
Observe = Callable[[AgentContext], "str | None"]


def action_summary(tool: str, args: dict) -> str:
    """Short text for the UI chip of one tool call."""
    path = str(args.get("file_path", ""))
    if tool in ("write_file", "edit_file") and path.startswith("/memories/"):
        return f"Updated memory: {path.removeprefix('/memories/')}"
    return {
        "viewer_goto": "Moving the view",
        "viewer_set_mode": f"Switching to the {args.get('mode', '')} view",
        "viewer_set_time": "Setting the time of day",
        "sun_by_room": "Checking where the sun falls",
        "sun_report": "Adding up sun hours",
        "daylight_at": "Checking how bright the rooms are",
        "design_show_variants": "Showing layouts",
        "get_room_geometry": "Measuring the room",
        "list_rooms": "Looking over the rooms",
    }.get(tool, tool.replace("_", " "))


class AgentBridge:
    def __init__(self, hub: Hub, runner: TurnRunner, observe: Observe | None = None) -> None:
        self.hub = hub
        self.runner = runner
        self.observe = observe
        self._signals: dict[str, int] = {}  # user actions since the observer last ran
        self._last_observed: dict[str, float] = {}
        self._observing: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._greeted: set[str] = set()
        self._last_room: dict[str, str | None] = {}
        self._last_proactive: dict[str, float] = {}
        self._tasks: set[asyncio.Task] = set()
        hub.subscribe(self.on_event)

    def on_event(self, room: SessionRoom, event: Envelope) -> None:
        sid, p = room.session_id, event.payload
        by_user = event.source in ("desktop", "phone")
        log_ = TOUR_LOGS.setdefault(sid, TourLog())

        if event.type == "chat.user" or (event.type == "voice.transcript" and p.get("final")):
            log_.heard(p["text"])
            self._signal(room)
            self._start(room, p["text"], proactive=False)
        elif event.type == "session.joined" and p.get("client") == "desktop" and sid not in self._greeted:
            self._greeted.add(sid)
            self._start(room, event_message("property_opened"), proactive=False)
        elif event.type == "viewer.goto" and p.get("room_id"):
            changed = self._last_room.get(sid) != p["room_id"]
            self._last_room[sid] = p["room_id"]
            log_.enter_room(p["room_id"], by_user=by_user)
            if changed and by_user:
                self._signal(room)
                self._start(room, event_message("room_entered"), proactive=True)
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
        context = AgentContext(user_id=room.user_id, property_id=room.property_id, session_id=sid)
        try:
            note = await asyncio.to_thread(self.observe, context)
        except Exception:
            log.exception("observer run failed")  # the tour goes on without it
            return
        finally:
            self._observing.discard(sid)
        if note and deliver:
            self._last_proactive[sid] = time.monotonic()  # counts as the guide's proactive turn
            self._start(room, event_message("observer_note", note), proactive=False)

    def _keep(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _start(self, room: SessionRoom, text: str, proactive: bool) -> None:
        sid = room.session_id
        lock = self._locks.setdefault(sid, asyncio.Lock())
        if proactive:
            if lock.locked() or time.monotonic() - self._last_proactive.get(sid, -1e9) < PROACTIVE_COOLDOWN_S:
                return
            self._last_proactive[sid] = time.monotonic()
        self._keep(asyncio.create_task(self._run_turn(room, text, lock)))

    async def _run_turn(self, room: SessionRoom, text: str, lock: asyncio.Lock) -> None:
        sid = room.session_id

        def emit(type: str, payload: dict) -> None:
            try:
                self.hub.publish(make_event(sid, "agent", type, payload))
            except (KeyError, ValueError) as e:  # an event the schema doesn't know or accept
                log.warning("agent event %s dropped: %s", type, e)

        async with lock:
            s = room.state
            where = f'<viewer mode="{s.mode}" room_id="{s.room_id or ""}" datetime="{s.datetime or ""}"/>\n'
            context = AgentContext(user_id=room.user_id, property_id=room.property_id, session_id=sid)
            loop, queue = asyncio.get_running_loop(), asyncio.Queue()

            def work() -> None:  # the runner is blocking, so it gets its own thread
                try:
                    for item in self.runner(where + text, context, sid):  # thread_id = session_id
                        loop.call_soon_threadsafe(queue.put_nowait, item)
                except Exception as e:
                    log.exception("agent turn failed")
                    loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            emit("agent.turn", {"status": "started"})
            worker = asyncio.create_task(asyncio.to_thread(work))
            message_id = uuid.uuid4().hex
            while (item := await queue.get()) is not None:
                kind, *rest = item
                if kind == "token":
                    emit("agent.token", {"message_id": message_id, "delta": rest[0]})
                elif kind == "action":
                    emit("agent.action", {"tool": rest[0], "summary": action_summary(rest[0], rest[1])})
                elif kind == "ui":
                    emit(rest[0]["type"], rest[0]["payload"])
                elif kind == "message":
                    emit("agent.message", {"message_id": message_id, "text": rest[0]})
                elif kind == "error":
                    emit("agent.error", {"message": rest[0]})
            await worker
            emit("agent.turn", {"status": "ended"})
