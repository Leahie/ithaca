"""Layout types. Owner: B. Consumer: A."""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from .common import Model, Vec3


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
