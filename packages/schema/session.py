"""Sessions and viewer state (WORKSTREAMS.md section 2.2). Owner: B. Consumer: A."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ViewerMode = Literal["tour", "sun", "sound", "neighborhood", "design"]
ClientKind = Literal["desktop", "phone"]


class Session(BaseModel):
    session_id: str
    pair_token: str  # single use, 10 minutes: the phone trades it for a client_token (B10)
    expires_at: datetime  # when pair_token expires
    # Additive to v0. Sent on the WebSocket URL; lets a client reconnect.
    client_token: str | None = None


class ViewerState(BaseModel):
    """What the desktop is showing. The hub keeps the latest copy and sends it on join (B9)."""

    mode: ViewerMode = "tour"
    room_id: str | None = None
    datetime: str | None = None  # ISO 8601 with offset (I-G13); None = the viewer's default
    sound_scenario: dict | None = None  # {room_id, scenario}
    shown_layout_ids: list[str] = Field(default_factory=list)
