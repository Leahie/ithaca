"""Event envelope and v0 events (WORKSTREAMS.md section 2.3). Owner: B. Consumer: A.

Every message on the session WebSocket is an EventEnvelope. `type` selects the payload model in
EVENTS. Unknown types and unknown fields are ignored, never an error (I-G7).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import model_validator

from .common import Model
from .inventory import CatalogPiece
from .session import ClientKind, SoundPlaceSourcePayload, SoundSetScenarioPayload, ViewerMode, ViewerState
from .twin import Vec3

EVENT_VERSION = 1

EventSource = Literal["desktop", "phone", "agent", "server"]
JobStatus = Literal["queued", "running", "done", "failed"]


class EventEnvelope(Model):
    v: int = EVENT_VERSION
    id: str
    ts: str
    session_id: str
    source: EventSource
    type: str
    payload: dict[str, Any]
    # Additive: which household member this is about. On a phone's event, the sender; on an
    # agent's event, whose agent is speaking. None for the desktop and for the server.
    member_id: str | None = None


# --- payloads ---------------------------------------------------------------------------------


class SessionPresencePayload(Model):
    client: ClientKind
    # Additive: the household member on that phone.
    member_id: str | None = None
    name: str | None = None


class FamilyNotePayload(Model):
    """A note one member's agent posted to the family board, for the others to weigh."""

    note_id: str
    member_id: str  # whose agent wrote it
    text: str
    room_id: str | None = None


class FamilyAskPayload(Model):
    """One member's agent asks another member's agent a question."""

    ask_id: str
    from_member_id: str
    to_member_id: str
    question: str


class FamilyAnswerPayload(Model):
    """The asked agent's reply, from its own member's memory only (B4)."""

    ask_id: str
    from_member_id: str
    to_member_id: str
    answer: str


class ViewerGotoPayload(Model):
    """Exactly one of room_id / sweep_id. With room_id, `at_sweep_id` and `look_at` (both additive
    to v0) say where to stand and what to face for the best view of the room."""

    room_id: str | None = None
    sweep_id: str | None = None
    at_sweep_id: str | None = None
    look_at: Vec3 | None = None  # interior frame

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.room_id is None) == (self.sweep_id is None):
            raise ValueError("exactly one of room_id, sweep_id")
        return self


class ViewerWalkPayload(Model):
    """Additive to v0. Walk the camera from one room to another, sweep by sweep."""

    from_room_id: str
    to_room_id: str
    sweep_ids: list[str]  # in order; the first is in from_room_id, the last in to_room_id
    look_at: Vec3  # what to face on arrival, interior frame
    distance_m: float
    duration_s: float  # at a walking pace; a guide for the animation, not a requirement


class ViewerSetTimePayload(Model):
    datetime: str  # ISO 8601 with offset (I-G13)


class ViewerSetModePayload(Model):
    mode: ViewerMode


class ViewerLookPayload(Model):
    yaw_delta_deg: float
    pitch_delta_deg: float


class DesignShowVariantsPayload(Model):
    layout_ids: list[str]


class LayoutSelectPayload(Model):
    layout_id: str


class SoundClearSourcePayload(Model):
    """Additive to v0. Removes the placed sound."""


class CatalogSuggestPayload(Model):
    """Additive to v0. The agent shows catalog pieces as picture cards in the chat; the user adds
    one by clicking it."""

    property_id: str
    room_id: str | None = None  # the room the piece is for; None = the room the user is in
    near_item_id: str | None = None  # place it next to this piece, if it is in the room
    pieces: list[CatalogPiece]


class CatalogPickedPayload(Model):
    """Additive to v0. The user clicked a suggested piece; the app added and placed it."""

    catalog_id: str
    item_id: str
    room_id: str | None = None


class CaptureUploadPayload(Model):
    upload_id: str


class ChatUserPayload(Model):
    text: str


class VoiceTranscriptPayload(Model):
    text: str
    final: bool


class AgentTokenPayload(Model):
    message_id: str
    delta: str


class AgentMessagePayload(Model):
    message_id: str
    text: str


class AgentActionPayload(Model):
    tool: str
    summary: str


class AgentErrorPayload(Model):
    message: str


class AgentTurnPayload(Model):
    """Additive to v0. Brackets one agent turn, which may produce no agent.message at all."""

    status: Literal["started", "ended"]


class JobProgressPayload(Model):
    job_id: str
    kind: str
    pct: float
    status: JobStatus
    result_ref: str | None = None


# type -> (payload model, who may send it)
EVENTS: dict[str, tuple[type[Model], frozenset[str]]] = {
    "session.state": (ViewerState, frozenset({"server"})),
    "session.joined": (SessionPresencePayload, frozenset({"server"})),
    "session.left": (SessionPresencePayload, frozenset({"server"})),
    "viewer.goto": (ViewerGotoPayload, frozenset({"agent", "phone", "desktop"})),
    "viewer.walk": (ViewerWalkPayload, frozenset({"agent"})),
    "viewer.setTime": (ViewerSetTimePayload, frozenset({"agent", "phone", "desktop"})),
    "viewer.setMode": (ViewerSetModePayload, frozenset({"agent", "phone", "desktop"})),
    "viewer.look": (ViewerLookPayload, frozenset({"phone"})),
    "design.showVariants": (DesignShowVariantsPayload, frozenset({"agent"})),
    "layout.select": (LayoutSelectPayload, frozenset({"agent", "phone", "desktop"})),
    "sound.setScenario": (SoundSetScenarioPayload, frozenset({"agent", "phone", "desktop"})),
    "sound.placeSource": (SoundPlaceSourcePayload, frozenset({"agent", "phone", "desktop"})),
    "sound.clearSource": (SoundClearSourcePayload, frozenset({"agent", "phone", "desktop"})),
    "catalog.suggest": (CatalogSuggestPayload, frozenset({"agent"})),
    "catalog.picked": (CatalogPickedPayload, frozenset({"phone", "desktop"})),
    "capture.upload": (CaptureUploadPayload, frozenset({"phone", "desktop"})),
    "chat.user": (ChatUserPayload, frozenset({"desktop", "phone"})),
    "voice.transcript": (VoiceTranscriptPayload, frozenset({"desktop", "phone"})),
    "agent.token": (AgentTokenPayload, frozenset({"agent"})),
    "agent.message": (AgentMessagePayload, frozenset({"agent"})),
    "agent.action": (AgentActionPayload, frozenset({"agent"})),
    "agent.error": (AgentErrorPayload, frozenset({"agent"})),
    "agent.turn": (AgentTurnPayload, frozenset({"agent"})),
    "job.progress": (JobProgressPayload, frozenset({"server"})),
    "family.note": (FamilyNotePayload, frozenset({"agent"})),
    "family.ask": (FamilyAskPayload, frozenset({"agent"})),
    "family.answer": (FamilyAnswerPayload, frozenset({"agent"})),
}

# Event type -> payload model. The TypeScript codegen reads this.
EVENT_PAYLOADS: dict[str, type[Model]] = {t: model for t, (model, _) in EVENTS.items()}

# Agent UI tools use snake_case names; each maps to one event.
UI_TOOL_EVENTS: dict[str, str] = {
    "viewer_goto": "viewer.goto",
    "viewer_walk": "viewer.walk",
    "viewer_set_time": "viewer.setTime",
    "viewer_set_mode": "viewer.setMode",
    "design_show_variants": "design.showVariants",
    "sound_set_scenario": "sound.setScenario",
    "sound_place_source": "sound.placeSource",
}


def make_event(session_id: str, source: EventSource, type: str, payload: dict | Model, id: str | None = None, member_id: str | None = None) -> EventEnvelope:
    """Build a valid event. Raises on an unknown type or a payload that doesn't fit: for our own
    code. Input from clients goes through parse_client_event, which never raises."""
    model, senders = EVENTS[type]
    if source not in senders:
        raise ValueError(f"{source} may not send {type}")
    data = payload.model_dump() if isinstance(payload, Model) else payload
    clean = model.model_validate(data).model_dump(exclude_none=True)
    return EventEnvelope(
        id=id or uuid.uuid4().hex,
        ts=datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        session_id=session_id,
        source=source,
        type=type,
        payload=clean,
        member_id=member_id,
    )


def parse_client_event(raw: Any, session_id: str, source: EventSource, member_id: str | None = None) -> EventEnvelope | str:
    """Check a message from a browser. Returns the event, or the reason it was dropped (I-G7).

    session_id, source and member_id come from the connection, never from the message.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        return "not an event"
    if raw["type"] not in EVENTS:
        return f"unknown type {raw['type']!r}"
    try:
        return make_event(session_id, source, raw["type"], raw.get("payload") or {}, id=raw.get("id"), member_id=member_id)
    except ValueError as e:  # includes pydantic.ValidationError
        return str(e).splitlines()[0]
