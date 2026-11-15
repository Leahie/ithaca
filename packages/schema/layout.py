"""Layout and in-house furniture types. Owner: B. Consumer: A. Rules: docs/specs/furniture.md."""
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
    """A proposal: an arrangement of one room someone suggested. Never furniture in the house
    until it is applied (F8)."""

    layout_id: UUID
    user_id: str  # the house user
    property_id: str
    room_id: str
    placements: list[Placement]
    score: float
    rationale: str
    status: Literal["proposed", "chosen", "rejected"]
    feedback: str | None = None
    # None until the render job finishes.
    thumbnail_url: str | None = None


class HousePiece(Model):
    """A stored piece standing in a house: one row of the in-house table."""

    item_id: UUID
    property_id: str
    # Always the room whose outline holds (position.x, position.z) on its floor (F4).
    room_id: str
    # Interior frame; position.y == the room's floor height.
    position: Vec3
    # I-G12; 0 <= yaw_deg < 360 (F5).
    yaw_deg: float
    # The person who put it there last.
    placed_by: str
    updated_at: str
    # F12. A small thing standing on a piece of furniture (a lamp on a desk): that piece. It moves,
    # turns and leaves the room with it. None for everything standing on the floor or hung on a wall.
    on_item_id: UUID | None = None
    # How far above the room's floor its underside is: the height of the piece it rests on, or how
    # high it hangs on the wall. 0 on the floor. position.y already includes it.
    lift_m: float = 0.0
    # Only in the reply to a move: things a designer would flag (a blocked door, an overlap).
    # The move was saved anyway. Never stored (F9).
    warnings: list[str] | None = None


class PlacePiece(Model):
    """Body of PUT /properties/{property_id}/furniture/{item_id}: put a piece at a spot, or move it."""

    x: float  # interior frame, meters
    z: float
    yaw_deg: float | None = None  # None: keep the piece's turn (0 for a piece new to the house)
    floor_id: str | None = None  # the floor of the spot; None: the piece's floor, else any floor
    room_id: str | None = None  # with snap: the room to look in, when the spot itself is not in it
    snap: bool = False  # True: the nearest spot within 2 m that breaks no rule; False: exactly here
