"""Event envelope and v0 events (section 2.3). Owner: B. Consumer: A."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import model_validator

from .common import Model
from .reports import NoiseScenario

EVENT_VERSION = 1

EventSource = Literal["desktop", "phone", "agent", "server"]
ClientKind = Literal["desktop", "phone"]
ViewerMode = Literal["tour", "sun", "sound", "neighborhood", "design"]
JobStatus = Literal["queued", "running", "done", "failed"]


class SoundSetScenarioPayload(Model):
    room_id: str
    scenario: NoiseScenario


class ViewerState(Model):
    """Payload of ``session.state``: the full viewer state snapshot."""

    mode: ViewerMode
    room_id: str | None = None
    # I-G13.
    datetime: str
    sound_scenario: SoundSetScenarioPayload | None = None
    shown_layout_ids: list[UUID] = []


class SessionPresencePayload(Model):
    client: ClientKind


class ViewerGotoPayload(Model):
    """Exactly one of room_id / sweep_id."""

    room_id: str | None = None
    sweep_id: str | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> "ViewerGotoPayload":
        if (self.room_id is None) == (self.sweep_id is None):
            raise ValueError("exactly one of room_id / sweep_id is required")
        return self


class ViewerSetTimePayload(Model):
    # I-G13.
    datetime: str


class ViewerSetModePayload(Model):
    mode: ViewerMode


class ViewerLookPayload(Model):
    yaw_delta_deg: float
    pitch_delta_deg: float


class DesignShowVariantsPayload(Model):
    layout_ids: list[UUID]


class LayoutSelectPayload(Model):
    layout_id: UUID


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


class JobProgressPayload(Model):
    job_id: str
    kind: str
    pct: float
    status: JobStatus
    result_ref: str | None = None


# Event type -> payload model. This table is the registry the codegen reads.
EVENT_PAYLOADS: dict[str, type[Model]] = {
    "session.state": ViewerState,
    "session.joined": SessionPresencePayload,
    "session.left": SessionPresencePayload,
    "viewer.goto": ViewerGotoPayload,
    "viewer.setTime": ViewerSetTimePayload,
    "viewer.setMode": ViewerSetModePayload,
    "viewer.look": ViewerLookPayload,
    "design.showVariants": DesignShowVariantsPayload,
    "layout.select": LayoutSelectPayload,
    "sound.setScenario": SoundSetScenarioPayload,
    "capture.upload": CaptureUploadPayload,
    "chat.user": ChatUserPayload,
    "voice.transcript": VoiceTranscriptPayload,
    "agent.token": AgentTokenPayload,
    "agent.message": AgentMessagePayload,
    "agent.action": AgentActionPayload,
    "agent.error": AgentErrorPayload,
    "job.progress": JobProgressPayload,
}

# Agent UI tools use snake_case names; each maps to one event.
UI_TOOL_EVENTS: dict[str, str] = {
    "viewer_goto": "viewer.goto",
    "viewer_set_time": "viewer.setTime",
    "viewer_set_mode": "viewer.setMode",
    "design_show_variants": "design.showVariants",
    "sound_set_scenario": "sound.setScenario",
}


class EventEnvelope(Model):
    v: int = EVENT_VERSION
    id: str
    ts: str
    session_id: str
    source: EventSource
    type: str
    payload: dict[str, Any]

    def parsed_payload(self) -> Model | None:
        """The typed payload, or None for an unknown event type (I-G7)."""
        model = EVENT_PAYLOADS.get(self.type)
        return model.model_validate(self.payload) if model else None
