"""Session hub: the switchboard between desktop, phone and agent. Owner: B.

One session = one house being viewed by one user, or by one household: a family whose members
each pair a phone (realtime/households.py). A member's phone connection carries their member_id,
which the hub stamps on every event that phone sends. Everything that happens is an event (see
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
import json
import logging
import secrets
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    member_id: str | None = None  # the household member on this phone
    name: str | None = None

    def presence(self) -> dict:
        p: dict = {"client": self.client}
        if self.member_id:
            p.update(member_id=self.member_id, name=self.name)
        return p


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
    household_id: str | None = None
    member_tokens: dict[str, str] = field(default_factory=dict)  # client_token -> member_id

    @property
    def visitor(self) -> str:
        """Who is looking at the house on this screen: the family, or the user alone."""
        return self.household_id or self.user_id


def _room_of_sweep(property_id: str, sweep_id: str) -> str | None:
    from services.api.twin.service import get_twin

    try:
        return next((s.room_id for s in get_twin(property_id).sweeps if s.sweep_id == sweep_id), None)
    except Exception:  # an unknown property: the room simply stays what it was
        return None


def _apply(state: ViewerState, event: EventEnvelope, property_id: str = "") -> None:
    """Fold an event into the viewer state."""
    p = event.payload
    if event.type == "viewer.goto" and p.get("room_id"):
        state.room_id = p["room_id"]
    elif event.type == "viewer.goto" and p.get("sweep_id"):
        # walking by hand sends the scan point; between rooms (stairs) the last room is kept
        state.room_id = _room_of_sweep(property_id, p["sweep_id"]) or state.room_id
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
    elif event.type == "viewer.playback":
        state.playback = state.playback.model_copy(update={k: v for k, v in p.items() if v is not None})


class Hub:
    def __init__(self, persist: Path | None = None) -> None:
        """persist: a SQLite file that remembers which sessions exist and their tokens, so a server
        restart (every code change, with --reload) doesn't strand open browser tabs. The viewer
        state and connections are not kept: clients rebuild from session.state when they reconnect."""
        self.rooms: dict[str, SessionRoom] = {}
        self._listeners: list[Callable[[SessionRoom, EventEnvelope], None]] = []
        self._db = None
        self._places: dict[tuple[str, str], str] = {}  # (visitor, property_id) -> the room they were in last
        self._done: dict[str, float] = {}  # things done once in a while (see once): key -> when, epoch seconds
        if persist is not None:
            persist.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(persist, check_same_thread=False, isolation_level=None)
            self._db.execute("CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, created REAL, body TEXT)")
            self._db.execute("DELETE FROM sessions WHERE created < julianday('now') - 2")  # two days is plenty
            self._db.execute("CREATE TABLE IF NOT EXISTS places (visitor TEXT, property_id TEXT, room_id TEXT, PRIMARY KEY (visitor, property_id))")
            self._places = {(v, p): r for v, p, r in self._db.execute("SELECT visitor, property_id, room_id FROM places")}
            self._db.execute("CREATE TABLE IF NOT EXISTS done (key TEXT PRIMARY KEY, at REAL)")
            self._done = dict(self._db.execute("SELECT key, at FROM done"))
            for (body,) in self._db.execute("SELECT body FROM sessions"):
                saved = json.loads(body)
                room = SessionRoom(
                    session_id=saved["session_id"], user_id=saved["user_id"], property_id=saved["property_id"],
                    state=ViewerState(mode="tour", datetime=saved["datetime"]),
                    household_id=saved.get("household_id"),
                )
                room.state.room_id = self._places.get((room.visitor, room.property_id))
                room.client_tokens = saved["client_tokens"]
                room.member_tokens = saved.get("member_tokens", {})
                room.issued_pair_tokens = set(saved["issued"])
                room.joined_pair_tokens = set(saved["joined"])
                self.rooms[room.session_id] = room

    def once(self, key: str, within_s: float) -> bool:
        """True the first time `key` is asked about, and again only after `within_s` seconds. It
        survives a new session and a restart, so a greeting is not repeated on every reconnect."""
        now = time.time()
        if now - self._done.get(key, 0.0) < within_s:
            return False
        self._done[key] = now
        if self._db is not None:
            self._db.execute("INSERT OR REPLACE INTO done VALUES (?, ?)", (key, now))
        return True

    def _remember_place(self, room: SessionRoom) -> None:
        """So the next visit (a new tab, a restart) opens in the room this one was left in."""
        key = (room.visitor, room.property_id)
        if not room.state.room_id or self._places.get(key) == room.state.room_id:
            return
        self._places[key] = room.state.room_id
        if self._db is not None:
            self._db.execute("INSERT OR REPLACE INTO places VALUES (?, ?, ?)", (*key, room.state.room_id))

    def _save(self, room: SessionRoom) -> None:
        if self._db is None:
            return
        body = json.dumps({
            "session_id": room.session_id, "user_id": room.user_id, "property_id": room.property_id,
            "datetime": room.state.datetime, "client_tokens": room.client_tokens,
            "issued": sorted(room.issued_pair_tokens), "joined": sorted(room.joined_pair_tokens),
            "household_id": room.household_id, "member_tokens": room.member_tokens,
        })
        self._db.execute("INSERT OR REPLACE INTO sessions VALUES (?, julianday('now'), ?)", (room.session_id, body))

    # --- sessions and pairing (B10) -------------------------------------------------------------

    def create_session(self, user_id: str, property_id: str, timezone_name: str = "UTC", household_id: str | None = None) -> Session:
        """timezone_name: the property's IANA timezone, for the viewer's starting date and time.
        household_id: the family whose members' phones may join through the family QR code."""
        now = datetime.now(ZoneInfo(timezone_name)).isoformat(timespec="seconds")
        room = SessionRoom(
            session_id=uuid.uuid4().hex,
            user_id=user_id,
            property_id=property_id,
            state=ViewerState(mode="tour", datetime=now),
            household_id=household_id,
        )
        room.state.room_id = self._places.get((room.visitor, property_id))  # where they were last, if anywhere
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
        self._save(room)
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
        self._save(room)
        return client_token

    def mint_member_token(self, session_id: str, member_id: str) -> str:
        """A phone client_token for a household member who joined through the family QR code.
        The family invite was checked already; this only binds the token to the member. Raises
        KeyError for an unknown session."""
        room = self.rooms[session_id]
        client_token = secrets.token_urlsafe(24)
        room.client_tokens[client_token] = "phone"
        room.member_tokens[client_token] = member_id
        self._save(room)
        return client_token

    def member_for(self, session_id: str, token: str) -> str | None:
        """The household member a phone's token belongs to, if any."""
        room = self.rooms.get(session_id)
        return room.member_tokens.get(token) if room else None

    def connected_members(self, session_id: str) -> dict[str, str]:
        """member_id -> name of every household member with a phone in this session now."""
        room = self.rooms.get(session_id)
        return {c.member_id: c.name or "" for c in room.connections if c.member_id} if room else {}

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

    def connect(self, session_id: str, client: ClientKind, member_id: str | None = None, name: str | None = None) -> Connection:
        room = self.rooms[session_id]
        conn = Connection(session_id, client, member_id=member_id, name=name)
        conn.queue.put_nowait(make_event(session_id, "server", "session.state", room.state))
        for other in room.connections:  # so a late joiner knows who is already here
            conn.queue.put_nowait(make_event(session_id, "server", "session.joined", other.presence()))
        room.connections.append(conn)
        self.publish(make_event(session_id, "server", "session.joined", conn.presence()))
        return conn

    def disconnect(self, conn: Connection) -> None:
        room = self.rooms.get(conn.session_id)
        if room and conn in room.connections:
            room.connections.remove(conn)
            self.publish(make_event(conn.session_id, "server", "session.left", conn.presence()))
            # The desktop runs the clock and plays the sound: with none left, nothing is playing.
            pb = room.state.playback
            if conn.client == "desktop" and (pb.playing or pb.listening) and not any(c.client == "desktop" for c in room.connections):
                self.publish(make_event(conn.session_id, "server", "viewer.playback", {"playing": False, "listening": False}))

    def publish(self, event: EventEnvelope) -> None:
        """Update the state and queue the event for every client. Call from the event loop only;
        because nothing here awaits, events keep the order they were published in."""
        room = self.rooms.get(event.session_id)
        if room is None:
            log.warning("event for unknown session %s dropped", event.session_id)
            return
        _apply(room.state, event, room.property_id)
        self._remember_place(room)
        for conn in room.connections:
            conn.queue.put_nowait(event)
        for listener in self._listeners:
            try:
                listener(room, event)
            except Exception:
                log.exception("hub listener failed on %s", event.type)
