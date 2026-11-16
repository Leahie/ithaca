from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from services.api.furniture.scans.mesh import Model


@dataclass(frozen=True)
class Thing:
    """One thing seen in a scanned room photo."""

    key: str
    label: str
    item_class: str  # from objects/classes.py
    group: str  # how the page lists it: furniture, floor, bed, desk, shelf, wall, window
    box_px: tuple[int, int, int, int]  # where it is in the photo: left, top, right, bottom
    build: Callable[[], Model]
    rests_on: str | None = None  # the key of the piece it stands on
    offset: tuple[float, float] | None = None  # on that piece: metres toward its front, toward its right
    turn: float | None = None  # degrees, from that piece's front toward its right
    beside: str | None = None  # on the floor: the key of the piece it goes with
    on_wall: bool = False
    anchor: str | None = None  # on a wall: the key of the piece it hangs over
    along: float = 0.0  # how far along that piece, toward its right, it hangs
    hang: float | None = None  # how high its underside is above the floor
    window: Literal["cover", "beside"] | None = None  # a curtain: over a window, or beside one
    color: str | None = None
    material: str | None = None
    note: str = ""
