"""Sessions, viewer state and small REST payloads (WORKSTREAMS.md section 2.2). Owner: B. Consumer: A."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .common import Model
from .reports import NoiseScenario

ViewerMode = Literal["tour", "sound", "neighborhood", "design", "family"]
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


# The outdoor sounds one can choose to hear, each a group of soundscape sources (sim/soundscape.py).
SoundLayer = Literal["highway", "streets", "birds", "insects", "frogs", "dogs"]


class ViewerPlaybackPayload(Model):
    """Additive to v0. What the big screen is playing: the day passing, the outside sounds (and
    which of them), and the guide's voice. Each field is optional; one event changes only the
    fields it names. The desktop runs the clock while ``playing``, so a phone and a desktop never
    both advance time."""

    playing: bool | None = None
    listening: bool | None = None
    sounds: list[SoundLayer] | None = None
    voice: Literal["female", "male"] | None = None


class ViewerPlayback(Model):
    playing: bool = False
    listening: bool = False
    sounds: list[SoundLayer] = ["highway", "streets", "birds", "insects", "frogs"]
    voice: Literal["female", "male"] = "female"


class ViewerState(Model):
    """Payload of ``session.state``: what the desktop is showing. The hub keeps the latest copy
    and sends it on join (B9)."""

    mode: ViewerMode
    room_id: str | None = None
    datetime: str  # ISO 8601 with the property's offset (I-G13)
    sound_scenario: SoundSetScenarioPayload | None = None
    sound_source: SoundPlaceSourcePayload | None = None  # additive to v0
    shown_layout_ids: list[str] = []
    playback: ViewerPlayback = ViewerPlayback()  # additive to v0


class CreateSessionRequest(Model):
    property_id: str
    # Additive: the family whose phones join this session. The session becomes the household's
    # active house, so a member who scans the family QR lands here.
    household_id: str | None = None


class JoinRequest(Model):
    pair_token: str


class Ok(Model):
    ok: bool


class JoinResult(Ok):
    client_token: str  # additive to v0: goes on the phone's WebSocket URL


# --- Households: a family looking at houses together --------------------------------------------
# Each member is a user in their own right: member_id is their user_id, so their agent has its
# own memory namespace (B4). The family QR carries invite_token, reusable until it expires.


class Member(Model):
    member_id: str  # also this member's user_id (X-User-Id, memory namespace)
    name: str


class Household(Model):
    household_id: str
    name: str
    invite_token: str
    invite_expires_at: datetime
    members: list[Member] = []
    # The house session the family's desktop has open, if any.
    active_session_id: str | None = None


class CreateHouseholdRequest(Model):
    name: str
    # True when the user asked for a new family: the host's current one is set aside, not reused.
    fresh: bool = False


class JoinHouseholdRequest(Model):
    invite_token: str
    name: str
    # A phone that joined before sends its member_id to come back as the same person.
    member_id: str | None = None


class JoinHouseholdResult(Model):
    household: Household
    member: Member
    # The family's open house, if the desktop has one: the phone connects to it with client_token.
    session_id: str | None = None
    client_token: str | None = None


class UploadResult(Model):
    upload_id: str
    url: str


class PhotoPoint(Model):
    """A spot on an image, as fractions of its width and height (0 to 1, from the top left)."""

    x: float
    y: float


class ObjectIngestRequest(Model):
    upload_id: str
    # Additive to v0. The spot the user tapped on the photo: only the thing there becomes a piece.
    # Without it, up to 4 pieces are read from the image.
    at: PhotoPoint | None = None


class RoomFromPhotoRequest(Model):
    """Body of POST /properties/{property_id}/furniture/from-photo: furnish a room of this house
    with everything in a photo of the person's own room (their furniture, belongings and
    decorations, each cut out and rebuilt in 3D)."""

    upload_id: str  # the photo, from POST /uploads
    room_id: str  # the room of this house to furnish with it


class JobRef(Model):
    job_id: str


class LayoutFeedbackRequest(Model):
    feedback: str


# --- Reset everything: delete all of the user's data and start from scratch ---------------------


class ResetCounts(Model):
    """How much there is to delete (GET /reset/preview), or how much was deleted (POST /reset)."""

    furniture: int  # pieces of furniture, everyone's
    in_houses: int  # pieces standing in a house (one that stands in two houses counts twice)
    layouts: int  # saved layouts
    memory_files: int  # what the guides remember, of every person, family and house
    conversations: int  # one per person (or family guide) and house
    families: int
    members: int  # people who joined a family from a phone
    files: int  # uploaded photos and videos, and the pictures and 3D models made from them


class ResetRequest(Model):
    """Body of POST /reset. `confirm` must be the word "reset": a call made by mistake deletes nothing."""

    confirm: str


class ResetFailure(Model):
    """One part of a reset that did not go through. The other parts were still deleted."""

    # "sessions", "guide", "jobs", "work in flight", "furniture", "families", "memory",
    # "conversations", "files", "speech log"
    part: str
    error: str


class ResetResult(Model):
    """Answer of POST /reset."""

    ok: bool  # True when every part was deleted
    deleted: ResetCounts
    failed: list[ResetFailure] = []
