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
    assert types.index("viewer.goto") < types.index("agent.message")  # the view moves before the text ends
    assert "agent.token" in types and "agent.action" in types
