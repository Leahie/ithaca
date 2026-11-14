"""Event envelope and v0 events (WORKSTREAMS.md section 2.3). Owner: B. Consumer: A.

Every message on the session WebSocket is an Envelope. `type` selects the payload model in EVENTS.
Unknown types and unknown fields are ignored, never an error (I-G7).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.schema.session import ClientKind, ViewerMode, ViewerState

Source = Literal["desktop", "phone", "agent", "server"]


class Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Envelope(Payload):
    v: int = 1
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    session_id: str
    source: Source
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


# --- payloads ---------------------------------------------------------------------------------


class SessionPresence(Payload):
    client: ClientKind


class ViewerGoto(Payload):
    room_id: str | None = None
    sweep_id: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.room_id is None) == (self.sweep_id is None):
            raise ValueError("exactly one of room_id, sweep_id")
        return self


class ViewerSetTime(Payload):
    datetime: str  # ISO 8601 with offset (I-G13)


class ViewerSetMode(Payload):
    mode: ViewerMode


class ViewerLook(Payload):
    yaw_delta_deg: float
    pitch_delta_deg: float


class DesignShowVariants(Payload):
    layout_ids: list[str]


class LayoutSelect(Payload):
    layout_id: str


class SoundSetScenario(Payload):
    room_id: str
    scenario: dict[str, Any]  # NoiseScenario; typed once C ships packages/schema/reports.py


class CaptureUpload(Payload):
    upload_id: str


class ChatUser(Payload):
    text: str


class VoiceTranscript(Payload):
    text: str
    final: bool


class AgentToken(Payload):
    message_id: str
    delta: str


class AgentMessage(Payload):
    message_id: str
    text: str


class AgentAction(Payload):
    tool: str
    summary: str


class AgentError(Payload):
    message: str


class AgentTurn(Payload):
    """Additive to v0. Brackets one agent turn, which may produce no agent.message at all."""

    status: Literal["started", "ended"]


class JobProgress(Payload):
    job_id: str
    kind: str
    pct: float
    status: Literal["queued", "running", "done", "failed"]
    result_ref: str | None = None


# type -> (payload model, who may send it)
EVENTS: dict[str, tuple[type[Payload], frozenset[str]]] = {
    "session.state": (ViewerState, frozenset({"server"})),
    "session.joined": (SessionPresence, frozenset({"server"})),
    "session.left": (SessionPresence, frozenset({"server"})),
    "viewer.goto": (ViewerGoto, frozenset({"agent", "phone", "desktop"})),
    "viewer.setTime": (ViewerSetTime, frozenset({"agent", "phone", "desktop"})),
    "viewer.setMode": (ViewerSetMode, frozenset({"agent", "phone", "desktop"})),
    "viewer.look": (ViewerLook, frozenset({"phone"})),
    "design.showVariants": (DesignShowVariants, frozenset({"agent"})),
    "layout.select": (LayoutSelect, frozenset({"phone", "desktop"})),
    "sound.setScenario": (SoundSetScenario, frozenset({"agent", "phone", "desktop"})),
    "capture.upload": (CaptureUpload, frozenset({"phone", "desktop"})),
    "chat.user": (ChatUser, frozenset({"desktop", "phone"})),
    "voice.transcript": (VoiceTranscript, frozenset({"desktop", "phone"})),
    "agent.token": (AgentToken, frozenset({"agent"})),
    "agent.message": (AgentMessage, frozenset({"agent"})),
    "agent.action": (AgentAction, frozenset({"agent"})),
    "agent.error": (AgentError, frozenset({"agent"})),
    "agent.turn": (AgentTurn, frozenset({"agent"})),
    "job.progress": (JobProgress, frozenset({"server"})),
}


def make_event(session_id: str, source: Source, type: str, payload: dict | Payload, id: str | None = None) -> Envelope:
    """Build a valid event. Raises on an unknown type or a payload that doesn't fit: for our own
    code. Input from clients goes through parse_client_event, which never raises."""
    model, senders = EVENTS[type]
    if source not in senders:
        raise ValueError(f"{source} may not send {type}")
    data = payload.model_dump() if isinstance(payload, Payload) else payload
    clean = model.model_validate(data).model_dump(exclude_none=True)
    return Envelope(session_id=session_id, source=source, type=type, payload=clean, **({"id": id} if id else {}))


def parse_client_event(raw: Any, session_id: str, source: Source) -> Envelope | str:
    """Check a message from a browser. Returns the event, or the reason it was dropped (I-G7).

    session_id and source come from the connection, never from the message.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        return "not an event"
    if raw["type"] not in EVENTS:
        return f"unknown type {raw['type']!r}"
    try:
        return make_event(session_id, source, raw["type"], raw.get("payload") or {}, id=raw.get("id"))
    except ValueError as e:  # includes pydantic.ValidationError
        return str(e).splitlines()[0]
