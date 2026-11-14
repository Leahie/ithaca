"""The furniture rules that need the house's plan or the family (docs/specs/furniture.md).

Every in-house row is built here: the room comes from the coordinates (F4), only the family's
pieces go in (F6), hand moves come back with warnings (F9), and applying a proposal replaces its
room in one go (F8). Routes and agent tools call these; FurnitureError carries the HTTP status.
"""

from __future__ import annotations

import math
import uuid

from shapely.geometry import Point, Polygon

from packages.schema.inventory import InventoryItem
from packages.schema.layout import HousePiece, LayoutVariant, Placement, PlacePiece
from packages.schema.twin import PropertyTwin, Room, Vec3
from services.api.agent import views
from services.api.furniture.store import furniture_store, norm_yaw, now
from services.api.realtime.households import households
from services.api.sim import layout_solver
from services.api.twin.service import get_twin


class FurnitureError(Exception):
    def __init__(self, status: int, detail: str | dict) -> None:
        super().__init__(str(detail))
        self.status = status
        self.detail = detail


# --- the family pool (F1, F6) -------------------------------------------------------------------


def pool(user_id: str) -> list[InventoryItem]:
    """Every piece of the user's family, each with its owner's name."""
    family = households().family_of(user_id)
    names = households().names(family)
    return [i.model_copy(update={"owner_name": names.get(i.user_id)}) for i in furniture_store().list_owned_by(family)]


def pool_item(user_id: str, item_id: str) -> InventoryItem | None:
    family = households().family_of(user_id)
    furniture_store().seed(family)
    item = furniture_store().get_item(str(item_id))
    if item is None or item.user_id not in family:
        return None
    return item.model_copy(update={"owner_name": households().names({item.user_id}).get(item.user_id)})


def may_delete(user_id: str, item: InventoryItem) -> bool:
    """F1: the owner, or the host of a household the owner belongs to."""
    return item.user_id == user_id or user_id in households().hosts_of(item.user_id)


def delete_item(user_id: str, item_id: str) -> InventoryItem:
    """Delete a piece for good (F1, F3)."""
    item = pool_item(user_id, item_id)
    if item is None:
        raise FurnitureError(404, "no such item")
    if not may_delete(user_id, item):
        raise FurnitureError(403, f"{item.label} belongs to {item.owner_name or 'someone else'}; only they can delete it")
    furniture_store().delete_item(str(item.item_id))
    return item


# --- where a spot is (F4) -----------------------------------------------------------------------


def room_by_id(twin: PropertyTwin, room_id: str | None) -> Room | None:
    return next((r for r in twin.rooms if r.room_id == room_id), None) if room_id else None


def room_at(twin: PropertyTwin, x: float, z: float, floor_id: str | None = None) -> Room | None:
    """The room whose outline holds (x, z) on that floor (any floor if None); the smallest if
    outlines nest."""
    here = Point(x, z)
    under = [r for r in twin.rooms if (floor_id is None or r.floor_id == floor_id) and Polygon(r.polygon).contains(here)]
    return min(under, key=lambda r: r.area_m2 or Polygon(r.polygon).area) if under else None


def _solver_room(twin: PropertyTwin, room: Room) -> Room:
    return room.model_copy(update={"floor_y": views.floor_height(twin, room.room_id)})  # sills from the real floor


def _solver_item(item: InventoryItem) -> layout_solver.Item:
    return layout_solver.Item(str(item.item_id), item.label, item.item_class, item.dims_m.w, item.dims_m.d, item.dims_m.h)


def _room_state(house_user_id: str, property_id: str, room_id: str, without: str | None = None):
    """The solver's view of the pieces standing in a room now."""
    items, poses = [], {}
    for p in furniture_store().house_pieces(house_user_id, property_id, room_id):
        key = str(p.item_id)
        item = furniture_store().get_item(key)
        if key != without and item is not None:
            items.append(_solver_item(item))
            poses[key] = layout_solver.Pose(p.position.x, p.position.z, p.yaw_deg)
    return items, poses


def _piece(twin: PropertyTwin, property_id: str, item_id: str, room: Room, x: float, z: float, yaw: float, by: str) -> HousePiece:
    return HousePiece(
        item_id=item_id, property_id=property_id, room_id=room.room_id,
        position=Vec3(x=round(x, 3), y=views.floor_height(twin, room.room_id), z=round(z, 3)),
        yaw_deg=norm_yaw(yaw), placed_by=by, updated_at=now(),
    )


# --- in the house -------------------------------------------------------------------------------


def place(house_user_id: str, property_id: str, item_id: str, body: PlacePiece, by: str) -> HousePiece:
    """Put a piece of the family's furniture at a spot, or move it there (F2, F4, F6, F9)."""
    twin = get_twin(property_id)
    item = pool_item(house_user_id, item_id)
    if item is None:
        raise FurnitureError(404, "no such item in the family's furniture")
    store = furniture_store()
    current = store.house_piece(house_user_id, property_id, str(item.item_id))
    hint = room_by_id(twin, body.room_id)
    if body.room_id and hint is None:
        raise FurnitureError(404, "no such room")
    floor = body.floor_id or (hint.floor_id if hint else None)
    if floor is None and current is not None:
        was = room_by_id(twin, current.room_id)
        floor = was.floor_id if was else None
    if floor is None:
        here = Point(body.x, body.z)
        under = [r for r in twin.rooms if Polygon(r.polygon).contains(here)]
        if len({r.floor_id for r in under}) > 1:
            names = ", ".join(sorted(f"{r.label} ({r.floor_id})" for r in under))
            raise FurnitureError(409, {"error": "which_floor", "violations": [f"That spot is in rooms on more than one floor: {names}. Say which floor."]})
    key = str(item.item_id)

    if body.snap:
        room = room_at(twin, body.x, body.z, floor)
        if hint is not None and (room is None or room.room_id != hint.room_id):
            room = hint
        if room is None:
            raise FurnitureError(409, {"error": "outside_room", "violations": ["That spot is not inside a room"]})
        items, poses = _room_state(house_user_id, property_id, room.room_id, without=key)
        items.append(_solver_item(item))
        pose = layout_solver.nearest_valid_pose(_solver_room(twin, room), twin.openings, items, poses, key, body.x, body.z, walls=twin.walls, sweeps=twin.sweeps)
        if pose is None:
            raise FurnitureError(409, {"error": "no_room", "violations": [f"{item.label} doesn't fit anywhere within 2 m of that spot"]})
        return store.put_piece(house_user_id, _piece(twin, property_id, key, room, pose.x, pose.z, pose.yaw, by))

    room = room_at(twin, body.x, body.z, floor)
    if room is None:
        raise FurnitureError(409, {"error": "outside_room", "violations": [f"{item.label} has to stand inside a room"]})
    yaw = body.yaw_deg if body.yaw_deg is not None else (current.yaw_deg if current else 0.0)
    items, poses = _room_state(house_user_id, property_id, room.room_id, without=key)
    items.append(_solver_item(item))
    poses[key] = layout_solver.Pose(body.x, body.z, yaw % 360)
    warnings = layout_solver.violations(_solver_room(twin, room), twin.openings, items, poses, walls=twin.walls, sweeps=twin.sweeps)
    saved = store.put_piece(house_user_id, _piece(twin, property_id, key, room, body.x, body.z, yaw, by))
    return saved.model_copy(update={"warnings": warnings or None})


def remove(house_user_id: str, property_id: str, item_id: str) -> None:
    """Take a piece out of the house (F7). It stays in storage."""
    if not furniture_store().remove_piece(house_user_id, property_id, item_id):
        raise FurnitureError(404, "that piece is not in this house")


# --- proposals ----------------------------------------------------------------------------------


def apply(house_user_id: str, layout_id: str, by: str) -> LayoutVariant:
    """Put a proposal in the house (F8). Pieces deleted or no longer in the family are skipped."""
    store = furniture_store()
    layout = store.get_proposal(house_user_id, layout_id)
    if layout is None:
        raise FurnitureError(404, "no such layout")
    twin = get_twin(layout.property_id)
    room = room_by_id(twin, layout.room_id)
    if room is None:
        raise FurnitureError(409, "that layout's room is not in this house any more")
    family = households().family_of(house_user_id)
    store.seed(family)
    pieces = []
    for p in layout.placements:
        item = store.get_item(str(p.item_id))
        if item is None or item.user_id not in family:
            continue
        # F4: the proposal's room, unless a nested room on the same floor holds the spot
        here = room_at(twin, p.position.x, p.position.z, room.floor_id) or room
        pieces.append(_piece(twin, layout.property_id, str(p.item_id), here, p.position.x, p.position.z, p.yaw_deg, by))
    return store.apply_proposal(house_user_id, layout, pieces)


def options(house_user_id: str, property_id: str, room_id: str, item_id: str) -> list[LayoutVariant]:
    """Up to three proposals that add one piece to a room: the room's furniture stays where it is
    and the piece stands at a different valid spot in each. Spots come from the solver, tried
    around the room's corners, walls and middle; the three kept are the ones furthest apart."""
    twin = get_twin(property_id)
    item = pool_item(house_user_id, item_id)
    if item is None:
        raise FurnitureError(404, "no such item in the family's furniture")
    room = room_by_id(twin, room_id)
    if room is None:
        raise FurnitureError(404, "no such room")
    key = str(item.item_id)
    items, poses = _room_state(house_user_id, property_id, room_id, without=key)
    items.append(_solver_item(item))
    solver_room = _solver_room(twin, room)

    shape = Polygon(room.polygon)
    cx, cz = shape.centroid.x, shape.centroid.y
    corners = list(room.polygon)
    pins = [(cx, cz)]
    for (x, z), (nx, nz) in zip(corners, corners[1:] + corners[:1]):
        for px, pz in ((x, z), ((x + nx) / 2, (z + nz) / 2)):  # each corner and each wall's middle,
            d = math.hypot(cx - px, cz - pz) or 1.0  # pulled a little way into the room
            pins.append((px + (cx - px) / d * min(0.7, d), pz + (cz - pz) / d * min(0.7, d)))
    found: list[layout_solver.Pose] = []
    for px, pz in pins:
        pose = layout_solver.nearest_valid_pose(solver_room, twin.openings, items, poses, key, px, pz, walls=twin.walls, sweeps=twin.sweeps)
        if pose is not None and all(math.hypot(pose.x - f.x, pose.z - f.z) > 0.9 for f in found):
            found.append(pose)
    if not found:
        raise FurnitureError(409, {"error": "no_room", "violations": [f"{item.label} doesn't fit anywhere in this room"]})
    picked = [found[0]]
    while len(picked) < min(3, len(found)):  # furthest from the ones already picked
        picked.append(max((f for f in found if f not in picked), key=lambda f: min(math.hypot(f.x - q.x, f.z - q.z) for q in picked)))

    def side(pose: layout_solver.Pose) -> str:  # -Z is north in the interior frame
        dx, dz = pose.x - cx, pose.z - cz
        if math.hypot(dx, dz) < 0.8:
            return "in the middle of the room"
        if abs(dx) > abs(dz):
            return "on the east side" if dx > 0 else "on the west side"
        return "on the south side" if dz > 0 else "on the north side"

    floor = views.floor_height(twin, room_id)
    kept = [Placement(item_id=p.item_id, position=p.position, yaw_deg=p.yaw_deg)
            for p in furniture_store().house_pieces(house_user_id, property_id, room_id) if str(p.item_id) != key]
    out = []
    for n, pose in enumerate(picked):
        option = LayoutVariant(
            layout_id=uuid.uuid4(), user_id=house_user_id, property_id=property_id, room_id=room_id,
            placements=kept + [Placement(item_id=key, position=Vec3(x=round(pose.x, 3), y=floor, z=round(pose.z, 3)), yaw_deg=norm_yaw(pose.yaw))],
            score=0, rationale=f"Option {'ABC'[n]}: {item.label} {side(pose)}.", status="proposed")
        furniture_store().put_proposal(option)
        out.append(option)
    return out
