"""Hub rules (B9, B10) and one full WebSocket turn with the scripted agent."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from packages.schema.events import make_event
from services.api.realtime.hub import Hub

PROPERTY = "onXQLXhfyQv"


def drain(conn) -> list[tuple[str, str]]:
    out = []
    while not conn.queue.empty():
        e = conn.queue.get_nowait()
        out.append((e.type, e.source))
    return out


def test_hub_delivers_in_order_to_everyone_and_sends_state_on_join():
    async def scenario():
        hub = Hub()
        sid = hub.create_session("demo", PROPERTY).session_id
        desktop = hub.connect(sid, "desktop")
        hub.publish(make_event(sid, "desktop", "viewer.setMode", {"mode": "sun"}))
        hub.publish(make_event(sid, "desktop", "viewer.goto", {"room_id": "r1"}))
        # the sender gets its own events back, in order (B9)
        assert drain(desktop) == [
            ("session.state", "server"), ("session.joined", "server"),
            ("viewer.setMode", "desktop"), ("viewer.goto", "desktop"),
        ]
        # a late joiner gets the latest state first, not the history
        phone = hub.connect(sid, "phone")
        first = phone.queue.get_nowait()
        assert first.type == "session.state" and first.payload["mode"] == "sun" and first.payload["room_id"] == "r1"
        assert drain(phone) == [("session.joined", "server"), ("session.joined", "server")]  # desktop, then itself

    asyncio.run(scenario())


def test_pair_token_is_single_use_expires_and_is_bound_to_its_session():
    hub = Hub()
    a, b = hub.create_session("demo", PROPERTY), hub.create_session("demo", PROPERTY)
    assert hub.redeem_pair_token(b.session_id, a.pair_token) is None  # other session
    assert not hub.authorize(a.session_id, "phone", a.pair_token)  # not joined yet
    phone_token = hub.redeem_pair_token(a.session_id, a.pair_token)
    assert hub.authorize(a.session_id, "phone", phone_token) and hub.authorize(a.session_id, "phone", a.pair_token)
    assert not hub.authorize(b.session_id, "phone", a.pair_token)
    assert hub.redeem_pair_token(a.session_id, a.pair_token) is None  # second use
    late = datetime.now(timezone.utc) + timedelta(minutes=11)
    assert hub.redeem_pair_token(b.session_id, b.pair_token, now=late) is None  # expired


def test_websocket_turn_with_scripted_agent(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "scripted")
    monkeypatch.setenv("AGENT_STORAGE", "memory")  # no files on disk
    from services.api.agent import provider

    provider._storage.cache_clear()
    from services.api.main import app

    def read_until_turn_ends(ws):
        events = []
        while not (events and events[-1]["type"] == "agent.turn" and events[-1]["payload"]["status"] == "ended"):
            events.append(ws.receive_json())
        return events

    with TestClient(app) as client:
        session = client.post("/api/sessions", json={"property_id": PROPERTY}).json()
        url = f"/api/ws/sessions/{session['session_id']}?client=desktop&token={session['client_token']}"
        with client.websocket_connect(url) as ws:
            read_until_turn_ends(ws)  # greeting on first join
            ws.send_json({"type": "made.up", "payload": {}})  # unknown: dropped, connection survives (I-G7)
            ws.send_json({"type": "chat.user", "payload": {"text": "take me to the kitchen"}})
            events = read_until_turn_ends(ws)

    types = [e["type"] for e in events]
    assert types[0] == "chat.user" and "made.up" not in types
    goto = next(e for e in events if e["type"] == "viewer.goto")
    assert goto["source"] == "agent" and goto["payload"]["room_id"]
    last_message = len(types) - 1 - types[::-1].index("agent.message")
    assert types.index("agent.message") < types.index("viewer.goto") < last_message  # a spoken cue, the move, then the answer
    assert "agent.token" in types and "agent.action" in types


def test_sessions_survive_a_server_restart(tmp_path):
    first = Hub(persist=tmp_path / "sessions.sqlite")
    session = first.create_session("demo", PROPERTY)
    phone_token = first.redeem_pair_token(session.session_id, session.pair_token)

    restarted = Hub(persist=tmp_path / "sessions.sqlite")  # what --reload does on every code change
    assert restarted.authorize(session.session_id, "desktop", session.pair_token)  # the open tab reconnects
    assert restarted.authorize(session.session_id, "phone", phone_token)
    assert restarted.rooms[session.session_id].user_id == "demo"


def test_trying_a_layout_tells_the_agent_nothing_and_keeping_it_does(monkeypatch):
    """Human in the loop: only an explicit "keep" reaches the agent (and from there, memory)."""
    from services.api.agent.scripted import demo_layout_ids
    from services.api.furniture import store
    from services.api.realtime.agent_bridge import AgentBridge

    monkeypatch.setattr(store, "_default", store.FurnitureStore(":memory:"))
    turns = []

    def runner(text, context, thread_id):
        turns.append(text)
        return iter(())

    async def scenario():
        hub = Hub()
        AgentBridge(hub, runner)
        sid = hub.create_session("demo", PROPERTY).session_id
        layout_id = demo_layout_ids(PROPERTY)[0]
        sent = []
        hub.subscribe(lambda _room, event: sent.append(event))
        hub.publish(make_event(sid, "desktop", "layout.select", {"layout_id": layout_id}))  # clicking through options
        await asyncio.sleep(0.05)
        assert turns == [] and store.furniture_store().house_pieces("demo", PROPERTY) == []  # a preview changes nothing
        hub.publish(make_event(sid, "desktop", "layout.confirm", {"layout_id": layout_id}))  # "Keep this layout"
        await asyncio.sleep(0.05)
        assert len(turns) == 1 and '<event type="layout_chosen">' in turns[0] and layout_id in turns[0]
        assert store.furniture_store().house_pieces("demo", PROPERTY)  # kept: now it is in the house (F8)
        assert any(e.type == "layout.changed" and e.source == "server" for e in sent)  # and every screen reloads

    asyncio.run(scenario())


def test_spoken_cue_names_the_room_and_stays_quiet_for_quick_tools():
    from services.api.realtime.agent_bridge import spoken_cue
    from services.api.twin.service import get_twin

    twin = get_twin("onXQLXhfyQv")
    kitchen = next(r for r in twin.rooms if r.label == "Kitchen")
    assert spoken_cue("viewer_walk", {"from_room_id": "x", "to_room_id": kitchen.room_id}, twin.property_id).startswith("Walking you to the Kitchen")
    assert spoken_cue("task", {"subagent_type": "designer"}, twin.property_id)
    assert spoken_cue("list_rooms", {}, twin.property_id) is None


def test_a_new_message_cuts_off_the_turn_in_flight_and_the_designer_reports_back():
    import time

    from services.api.agent.design_tools import BACKGROUND_DESIGN
    from services.api.realtime.agent_bridge import AgentBridge

    def runner(text, context, thread_id):
        if "long story" in text:
            yield ("token", "Once upon a time ")
            time.sleep(0.5)  # still writing when the user speaks again
            yield ("message", "Once upon a time there was a very long answer.")
        elif "lay out" in text:
            yield ("ui", {"type": BACKGROUND_DESIGN, "payload": {"room_id": "r1", "brief": "whatever fits"}})
            yield ("message", "Working on it.")
        else:
            yield ("message", f"answer to: {text}")

    async def scenario():
        hub = Hub()
        AgentBridge(hub, runner, design=lambda context, room_id, brief: f"layout_ids for {room_id}: a, b, c")
        sid = hub.create_session("demo", PROPERTY).session_id
        said = []
        hub.subscribe(lambda _room, e: said.append(e.payload["text"]) if e.type == "agent.message" else None)
        hub.publish(make_event(sid, "phone", "chat.user", {"text": "tell me a long story"}))
        await asyncio.sleep(0.15)
        hub.publish(make_event(sid, "phone", "chat.user", {"text": "stop, take me to the kitchen"}))
        await asyncio.sleep(1.0)
        assert not any("very long answer" in t for t in said) and any("kitchen" in t for t in said)  # cut off, and the new one answered
        hub.publish(make_event(sid, "phone", "chat.user", {"text": "lay out the bedroom"}))
        await asyncio.sleep(0.6)
        assert "Working on it." in said  # the guide did not wait for the designer
        assert any("layouts_ready" in t and "a, b, c" in t for t in said)  # and got its report as an event turn

    asyncio.run(scenario())
