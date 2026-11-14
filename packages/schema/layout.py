"""Layout types. Owner: B. Consumer: A."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from .common import Model
from .twin import Vec3  # Vec3 lives in twin.py for now (Stream C)


class Placement(Model):
    item_id: UUID
    # Interior frame; position.y == room.floor_y.
    position: Vec3
    # I-G12.
    yaw_deg: float


class LayoutVariant(Model):
    layout_id: UUID
    user_id: str
    property_id: str
    room_id: str
    placements: list[Placement]
    score: float
    rationale: str
    status: Literal["proposed", "chosen", "rejected"]
    feedback: str | None = None
    # None until the render job finishes.
    thumbnail_url: str | None = None


class PlacementMove(Model):
    """Body of PUT /layouts/{layout_id}/placements/{item_id}: where the user dragged a piece."""

    x: float  # interior frame, meters
    z: float
    yaw_deg: float  # I-G12


class PlacementDrop(Model):
    """Body of POST /layouts/drop: the user pointed at a spot on the floor plan for one piece."""

    property_id: str
    room_id: str | None = None  # None: the server finds the room under the point on floor_id
    floor_id: str | None = None
    item_id: str
    x: float  # interior frame, meters
    z: float
    layout_id: str | None = None  # add the piece to this layout; None starts a new one for the room
