"""Session hub: the switchboard between desktop, phone and agent. Owner: B.

One session = one house being viewed by one user. Everything that happens is an event (see
packages/schema/events.py). The hub:
- delivers every event, in order, to every client in the session, including the sender (B9);
- keeps the latest ViewerState and sends it as `session.state` to a client when it joins (B9);
- keeps no event history;
- hands out pairing tokens: single use, 10 minutes, one session (B10).

It knows nothing about the agent. The agent bridge subscribes with `hub.subscribe()`.

State is in this process's memory. Redis pub/sub replaces the fan-out when there is more than one
API process; the methods stay the same.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from packages.schema.events import EventEnvelope, make_event
from packages.schema.session import ClientKind, Session, SoundPlaceSourcePayload, SoundSetScenarioPayload, ViewerState

log = logging.getLogger(__name__)

PAIR_TOKEN_TTL = timedelta(minutes=10)


@dataclass(eq=False)
class Connection:
    session_id: str
    client: ClientKind
    queue: asyncio.Queue[EventEnvelope] = field(default_factory=asyncio.Queue)


@dataclass
class SessionRoom:
    session_id: str
    user_id: str
    property_id: str
    state: ViewerState
    connections: list[Connection] = field(default_factory=list)
    pair_tokens: dict[str, datetime] = field(default_factory=dict)  # token -> expiry
    client_tokens: dict[str, ClientKind] = field(default_factory=dict)
    issued_pair_tokens: set[str] = field(default_factory=set)
    joined_pair_tokens: set[str] = field(default_factory=set)


def _apply(state: ViewerState, event: EventEnvelope) -> None:
    """Fold an event into the viewer state."""
    p = event.payload
    if event.type == "viewer.goto" and p.get("room_id"):
        state.room_id = p["room_id"]
    elif event.type == "viewer.walk":
        state.room_id = p["to_room_id"]
    elif event.type == "viewer.setTime":
        state.datetime = p["datetime"]
    elif event.type == "viewer.setMode":
        state.mode = p["mode"]
    elif event.type == "sound.setScenario":
        state.sound_scenario = SoundSetScenarioPayload.model_validate(p)
    elif event.type == "sound.placeSource":
        state.sound_source = SoundPlaceSourcePayload.model_validate(p)
    elif event.type == "sound.clearSource":
        state.sound_source = None
    elif event.type == "design.showVariants":
        state.shown_layout_ids = p["layout_ids"]


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[str, SessionRoom] = {}
        self._listeners: list[Callable[[SessionRoom, EventEnvelope], None]] = []

    # --- sessions and pairing (B10) -------------------------------------------------------------

    def create_session(self, user_id: str, property_id: str, timezone_name: str = "UTC") -> Session:
        """timezone_name: the property's IANA timezone, for the viewer's starting date and time."""
        now = datetime.now(ZoneInfo(timezone_name)).isoformat(timespec="seconds")
        room = SessionRoom(
            session_id=uuid.uuid4().hex, user_id=user_id, property_id=property_id, state=ViewerState(mode="tour", datetime=now)
        )
        self.rooms[room.session_id] = room
        client_token = secrets.token_urlsafe(24)
        room.client_tokens[client_token] = "desktop"
        return self.new_pair_token(room.session_id).model_copy(update={"client_token": client_token})

    def new_pair_token(self, session_id: str, now: datetime | None = None) -> Session:
        """A fresh token for the QR code. Raises KeyError for an unknown session."""
        room = self.rooms[session_id]
        expires = (now or datetime.now(timezone.utc)) + PAIR_TOKEN_TTL
        token = secrets.token_urlsafe(16)
        room.pair_tokens[token] = expires
        room.issued_pair_tokens.add(token)
        return Session(session_id=session_id, pair_token=token, expires_at=expires)

    def redeem_pair_token(self, session_id: str, pair_token: str, now: datetime | None = None) -> str | None:
        """Admit a phone: one successful join per pairing token. Returns the phone's client_token,
        or None if the token is unknown, used, expired, or for another session."""
        room = self.rooms.get(session_id)
        expires = room.pair_tokens.pop(pair_token, None) if room else None
        if expires is None or (now or datetime.now(timezone.utc)) >= expires:
            return None
        client_token = secrets.token_urlsafe(24)
        room.client_tokens[client_token] = "phone"
        room.joined_pair_tokens.add(pair_token)
        return client_token

    def authorize(self, session_id: str, client: ClientKind, token: str) -> bool:
        """May this token open a WebSocket as `client`?

        Accepts the client_token, or the pairing token itself: for the desktop any pairing token
        issued to this session, for the phone only one that has been through a successful join.
        (The web app reconnects with the pairing token; "single use" means one join, B10.)
        """
        room = self.rooms.get(session_id)
        if room is None or not token:
            return False
        if room.client_tokens.get(token) == client:
            return True
        return token in (room.issued_pair_tokens if client == "desktop" else room.joined_pair_tokens)

    # --- connections and events (B9) ------------------------------------------------------------

    def subscribe(self, listener: Callable[[SessionRoom, EventEnvelope], None]) -> None:
        """Call `listener(room, event)` for every published event, after the state is updated."""
        self._listeners.append(listener)

    def connect(self, session_id: str, client: ClientKind) -> Connection:
        room = self.rooms[session_id]
        conn = Connection(session_id, client)
        conn.queue.put_nowait(make_event(session_id, "server", "session.state", room.state))
        for other in room.connections:  # so a late joiner knows who is already here
            conn.queue.put_nowait(make_event(session_id, "server", "session.joined", {"client": other.client}))
        room.connections.append(conn)
        self.publish(make_event(session_id, "server", "session.joined", {"client": client}))
        return conn

    def disconnect(self, conn: Connection) -> None:
        room = self.rooms.get(conn.session_id)
        if room and conn in room.connections:
            room.connections.remove(conn)
            self.publish(make_event(conn.session_id, "server", "session.left", {"client": conn.client}))

    def publish(self, event: EventEnvelope) -> None:
        """Update the state and queue the event for every client. Call from the event loop only;
        because nothing here awaits, events keep the order they were published in."""
        room = self.rooms.get(event.session_id)
        if room is None:
            log.warning("event for unknown session %s dropped", event.session_id)
            return
        _apply(room.state, event)
        for conn in room.connections:
            conn.queue.put_nowait(event)
        for listener in self._listeners:
            try:
                listener(room, event)
            except Exception:
                log.exception("hub listener failed on %s", event.type)
