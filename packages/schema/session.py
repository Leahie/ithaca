"""Sessions, viewer state and small REST payloads (WORKSTREAMS.md section 2.2). Owner: B. Consumer: A."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .common import Model
from .reports import NoiseScenario

ViewerMode = Literal["tour", "sun", "sound", "neighborhood", "design"]
ClientKind = Literal["desktop", "phone"]


class Session(Model):
    session_id: str
    pair_token: str  # single use, 10 minutes: the phone trades it for a client_token (B10)
    expires_at: datetime  # when pair_token expires
    # Additive to v0. Sent on the WebSocket URL; lets a client reconnect.
    client_token: str | None = None


class SoundSetScenarioPayload(Model):
    room_id: str
    scenario: NoiseScenario


class SoundPlaceSourcePayload(Model):
    """A sound placed in the house (C's sound_field computes what each room hears). Additive to v0."""

    room_id: str
    x: float  # interior frame, meters
    z: float
    preset_id: str  # from GET /sound/presets; "recording" = the user's own clip
    level_dba: float | None = None  # required with preset_id "recording"
    upload_id: str | None = None


class ViewerState(Model):
    """Payload of ``session.state``: what the desktop is showing. The hub keeps the latest copy
    and sends it on join (B9)."""

    mode: ViewerMode
    room_id: str | None = None
    datetime: str  # ISO 8601 with the property's offset (I-G13)
    sound_scenario: SoundSetScenarioPayload | None = None
    sound_source: SoundPlaceSourcePayload | None = None  # additive to v0
    shown_layout_ids: list[str] = []


class CreateSessionRequest(Model):
    property_id: str


class JoinRequest(Model):
    pair_token: str


class Ok(Model):
    ok: bool


class JoinResult(Ok):
    client_token: str  # additive to v0: goes on the phone's WebSocket URL


class UploadResult(Model):
    upload_id: str
    url: str


class ObjectIngestRequest(Model):
    upload_id: str


class JobRef(Model):
    job_id: str


class LayoutFeedbackRequest(Model):
    feedback: str
