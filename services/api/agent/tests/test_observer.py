"""The tour observer: the log it reads, the memory it writes, and when the bridge runs it."""

from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore

from packages.schema.events import make_event
from services.api.agent.context import AgentContext
from services.api.agent.graph import memory_namespace
from services.api.agent.observer import build_observer, run_observer
from services.api.agent.tests.test_graph import PROPERTY, ScriptedModel, call
from services.api.agent.tour import TOUR_LOGS, TourLog
from services.api.realtime import agent_bridge
from services.api.realtime.hub import Hub
from services.api.twin.service import get_twin

KITCHEN, BEDROOM = "xy2x8mpaaqknp7q2c3nnbw1ma", "peina19mpqedsku6a3huu38md"


def test_tour_log_adds_up_time_per_room():
    log = TourLog(started=0)
    log.enter_room(KITCHEN, by_user=True, now=0)
    log.set_view("sound")
    log.enter_room(BEDROOM, by_user=False, now=90)  # the guide led this one
    log.enter_room(KITCHEN, by_user=True, now=100)
    log.heard("is it bright in the morning?")
    text = log.summary(get_twin(PROPERTY), now=130)  # 30 s into the visit still in progress

    kitchen_line = next(line for line in text.splitlines() if KITCHEN in line)
    assert "2 visits" in kitchen_line and "2m00s" in kitchen_line and "sound" in kitchen_line
    assert "1 led by the guide" in next(line for line in text.splitlines() if BEDROOM in line)
    assert log.user_moves == 2 and "is it bright in the morning?" in text


def test_observer_reads_the_log_writes_inferred_memory_and_returns_a_note():
    store, ctx = InMemoryStore(), AgentContext(user_id="alice", property_id=PROPERTY, session_id="s1")
    TOUR_LOGS["s1"] = TourLog()
    TOUR_LOGS["s1"].enter_room(KITCHEN, by_user=True)
    finding = "- Drawn to morning sun (inferred; kitchen visited 3x, sun view opened there)"
    model = ScriptedModel(
        script=[
            call("get_tour_log"),
            call("sun_by_room", day="2026-12-21", room_id=KITCHEN),  # C's sun engine, for real
            call("write_file", file_path="/memories/preferences.md", content=f"## Soft\n{finding}\n"),
            call("write_file", file_path="/memories/profile.md", content="guess"),  # not allowed
            AIMessage(content="They keep returning to the kitchen. Offer the breakfast corner at 9 am."),
        ]
    )
    note = run_observer(build_observer(PROPERTY, store=store, model=model), ctx)

    assert KITCHEN in model.tool_results[0]  # it saw the tour log
    assert "direct sun on the floor" in model.tool_results[1]
    assert "permission denied" in model.tool_results[3].lower()
    assert note.startswith("They keep returning")
    saved = store.get(memory_namespace(ctx.memory_user), "/preferences.md").value["content"]
    assert finding in (saved if isinstance(saved, str) else "\n".join(saved))


def test_bridge_runs_the_observer_after_enough_user_activity_and_passes_its_note_to_the_guide(monkeypatch):
    monkeypatch.setattr(agent_bridge, "PROACTIVE_COOLDOWN_S", 1e9)  # keep room_entered turns out of the way
    turns, observed = [], []

    def runner(text, context, thread_id):
        turns.append(text)
        return iter(())

    def observe(context):
        observed.append(context.session_id)
        return "They skip every upstairs room."

    async def scenario():
        hub = Hub()
        agent_bridge.AgentBridge(hub, runner, observe=observe)
        sid = hub.create_session("demo", PROPERTY).session_id
        rooms = [r.room_id for r in get_twin(PROPERTY).rooms]
        for i in range(agent_bridge.OBSERVE_EVERY_SIGNALS - 1):
            hub.publish(make_event(sid, "desktop", "viewer.goto", {"room_id": rooms[i]}))
        await asyncio.sleep(0.05)
        assert observed == []  # not enough activity yet
        hub.publish(make_event(sid, "agent", "viewer.goto", {"room_id": rooms[10]}))  # guide-led: doesn't count
        hub.publish(make_event(sid, "desktop", "viewer.goto", {"room_id": rooms[11]}))
        await asyncio.sleep(0.2)
        assert observed == [sid]
        assert any('<event type="observer_note">They skip every upstairs room.</event>' in t for t in turns)

    asyncio.run(scenario())
