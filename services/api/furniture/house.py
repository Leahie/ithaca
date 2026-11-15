"""The furniture rules that need the house's plan or the family (docs/specs/furniture.md).

Every in-house row is built here: the room comes from the coordinates (F4), only the family's
pieces go in (F6), hand moves come back with warnings (F9), applying a proposal replaces its
room in one go (F8), and a small thing put down on a piece of furniture rests on it (F12).
Routes and agent tools call these; FurnitureError carries the HTTP status.
"""

from __future__ import annotations

import math
import uuid

from shapely.geometry import Point, Polygon

from packages.schema.inventory import InventoryItem
from packages.schema.layout import HousePiece, LayoutVariant, Placement, PlacePiece
from packages.schema.twin import PropertyTwin, Room, Vec3
from services.api.agent import views
from services.api.furniture.store import HAND, furniture_store, norm_yaw, now
from services.api.objects.classes import HANGS_FROM_CEILING, MIN_TOP_M, NO_TOP, RESTS_ON_FURNITURE, top_height
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


def delete_item(user_id: str, item_id: str, via: str = HAND) -> InventoryItem:
    """Delete a piece for good (F1, F3). `via`: by hand, or a guide's tool call (the house log, F11)."""
    item = pool_item(user_id, item_id)
    if item is None:
        raise FurnitureError(404, "no such item")
    if not may_delete(user_id, item):
        raise FurnitureError(403, f"{item.label} belongs to {item.owner_name or 'someone else'}; only they can delete it")
    furniture_store().delete_item(str(item.item_id), by=user_id, via=via)
    return item


MIN_SCALE = 0.2
MAX_SCALE = 5.0


def resize_item(user_id: str, item_id: str, scale: float) -> InventoryItem:
    """Draw a piece at `scale` times its model's own size (anyone in the family may, as they may
    move it), or anyone arranging the house it stands in. Its dims_m follow, so the plan, the
    rules and the solver see the new size."""
    item = pool_item(user_id, item_id)
    if item is None and furniture_store().stands_in_house(user_id, item_id):
        item = furniture_store().get_item(str(item_id))
    if item is None:
        raise FurnitureError(404, "no such item")
    if not (scale > 0):
        raise FurnitureError(422, "scale must be above 0")
    scale = min(MAX_SCALE, max(MIN_SCALE, scale))
    k = scale / (item.scale or 1.0)
    dims = item.dims_m.model_copy(update={"w": item.dims_m.w * k, "d": item.dims_m.d * k, "h": item.dims_m.h * k})
    stored = furniture_store().put_item(item.model_copy(update={"scale": scale, "dims_m": dims}))
    return stored.model_copy(update={"owner_name": item.owner_name})


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
    """The solver's view of the pieces standing in a room now: the ones on its floor. What rests
    on a piece or hangs on a wall takes up no floor (F12)."""
    items, poses = [], {}
    for p in furniture_store().house_pieces(house_user_id, property_id, room_id):
        key = str(p.item_id)
        item = furniture_store().get_item(key)
        if key != without and item is not None and not p.on_item_id and not p.lift_m:
            items.append(_solver_item(item))
            poses[key] = layout_solver.Pose(p.position.x, p.position.z, p.yaw_deg)
    return items, poses


def _piece(
    twin: PropertyTwin, property_id: str, item_id: str, room: Room, x: float, z: float, yaw: float, by: str,
    on_item_id: str | None = None, lift_m: float = 0.0,
) -> HousePiece:
    """A row for the house. `lift_m` is how far above the floor the piece's underside is (F12)."""
    return HousePiece(
        item_id=item_id, property_id=property_id, room_id=room.room_id,
        position=Vec3(x=round(x, 3), y=round(views.floor_height(twin, room.room_id) + lift_m, 3), z=round(z, 3)),
        yaw_deg=norm_yaw(yaw), placed_by=by, updated_at=now(), on_item_id=on_item_id, lift_m=round(lift_m, 3),
    )


# --- resting on a piece (F12) ---------------------------------------------------------------------


def can_rest_on(item: InventoryItem, support: InventoryItem) -> bool:
    """Whether `item` is the kind of thing that stands on `support`: a lamp on a desk, not a desk
    on a lamp. The thing must be small next to the piece under it."""
    if item.item_class not in RESTS_ON_FURNITURE or support.item_class in NO_TOP or support.dims_m.h < MIN_TOP_M:
        return False
    # A plant's leaves may reach past the edge of the nightstand it stands on; a wardrobe on a stool may not.
    return item.dims_m.w * item.dims_m.d <= support.dims_m.w * support.dims_m.d and max(item.dims_m.w, item.dims_m.d) <= 1.15 * max(support.dims_m.w, support.dims_m.d)


def support_at(house_user_id: str, property_id: str, room_id: str, item: InventoryItem, x: float, z: float) -> tuple[HousePiece, InventoryItem] | None:
    """The piece standing on the floor of a room whose top holds the spot (x, z) and can carry
    `item`: the smallest such, if tops overlap."""
    found = []
    for p in furniture_store().house_pieces(house_user_id, property_id, room_id):
        under = furniture_store().get_item(str(p.item_id))
        if under is None or p.on_item_id or p.lift_m or str(p.item_id) == str(item.item_id) or not can_rest_on(item, under):
            continue
        top = layout_solver.footprint(_solver_item(under), layout_solver.Pose(p.position.x, p.position.z, p.yaw_deg))
        if top.contains(Point(x, z)):
            found.append((top.area, p, under))
    return min(found, key=lambda f: f[0])[1:] if found else None


# --- in the house -------------------------------------------------------------------------------


def place(house_user_id: str, property_id: str, item_id: str, body: PlacePiece, by: str, via: str = HAND) -> HousePiece:
    """Put a piece of the family's furniture at a spot, or move it there (F2, F4, F6, F9).
    `by` is the person; `via` how it came about (by hand, or a guide's tool call), for the log (F11)."""
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

    # The server picks the spot by what the piece is: a rug under the seating, a lamp on a free
    # table, a picture over the main piece, a ceiling light overhead (furniture/dress.py).
    if body.auto:
        auto_room = hint or room_at(twin, body.x, body.z, floor)
        if auto_room is None:
            raise FurnitureError(409, {"error": "outside_room", "violations": ["That spot is not inside a room"]})
        near = store.house_piece(house_user_id, property_id, body.near_item_id) if body.near_item_id else None
        placed = place_by(house_user_id, property_id, item, auto_room, str(near.item_id), by, via) if near is not None and near.room_id == auto_room.room_id else None
        placed = placed or _auto(twin, house_user_id, property_id, item, auto_room, by, via)
        if placed is not None:
            return placed
        body = body.model_copy(update={"snap": True, "room_id": auto_room.room_id})
        hint = auto_room

    # F12. Put down on a piece of furniture, a small thing rests on it: no floor rules apply.
    spot_room = room_at(twin, body.x, body.z, floor) or hint
    resting_on = support_at(house_user_id, property_id, spot_room.room_id, item, body.x, body.z) if spot_room is not None else None
    if resting_on is not None:
        support, support_item = resting_on
        yaw = body.yaw_deg if body.yaw_deg is not None else (current.yaw_deg if current else support.yaw_deg)
        top = top_height(support_item.item_class, support_item.dims_m.h)  # the seat of a sofa, not the top of its back
        resting = _piece(twin, property_id, key, room_by_id(twin, support.room_id) or spot_room, body.x, body.z, yaw, by, str(support.item_id), top)
        return store.put_piece(house_user_id, resting, via)
    # Hanging on a wall, a thing keeps its height when it is slid along by hand.
    if current is not None and current.lift_m > 0 and not current.on_item_id and not body.snap:
        room = room_at(twin, body.x, body.z, floor)
        if room is None:
            raise FurnitureError(409, {"error": "outside_room", "violations": [f"{item.label} has to stay inside a room"]})
        yaw = body.yaw_deg if body.yaw_deg is not None else current.yaw_deg
        return store.put_piece(house_user_id, _piece(twin, property_id, key, room, body.x, body.z, yaw, by, None, current.lift_m), via)

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
        return store.put_piece(house_user_id, _piece(twin, property_id, key, room, pose.x, pose.z, pose.yaw, by), via)

    room = room_at(twin, body.x, body.z, floor)
    if room is None:
        raise FurnitureError(409, {"error": "outside_room", "violations": [f"{item.label} has to stand inside a room"]})
    yaw = body.yaw_deg if body.yaw_deg is not None else (current.yaw_deg if current else 0.0)
    items, poses = _room_state(house_user_id, property_id, room.room_id, without=key)
    items.append(_solver_item(item))
    poses[key] = layout_solver.Pose(body.x, body.z, yaw % 360)
    warnings = layout_solver.violations(_solver_room(twin, room), twin.openings, items, poses, walls=twin.walls, sweeps=twin.sweeps)
    saved = store.put_piece(house_user_id, _piece(twin, property_id, key, room, body.x, body.z, yaw, by), via)
    return saved.model_copy(update={"warnings": warnings or None})


def _auto(twin: PropertyTwin, house_user_id: str, property_id: str, item: InventoryItem, room: Room, by: str, via: str) -> HousePiece | None:
    """Put a piece into a room at the spot its kind calls for (PlacePiece.auto). None for floor
    furniture, and for a thing with no such spot: the caller then stands it at the nearest free
    place, as before."""
    from services.api.furniture import dress

    if dress.kind_of(item) == "solve":
        return _auto_floor(twin, house_user_id, property_id, item, room, by, via)
    key = str(item.item_id)
    now = dress.in_house(house_user_id, property_id, twin, room, without=key)
    if not now.place(item):
        if item.item_class in HANGS_FROM_CEILING:  # a chandelier on the floor is worse than a refusal
            raise FurnitureError(409, {"error": "no_room", "violations": [now.left[-1].split(": ", 1)[-1].capitalize()]})
        return None
    spot = now.spots[key]
    return furniture_store().put_piece(house_user_id, _piece(twin, property_id, key, room, spot.x, spot.z, spot.yaw, by, spot.on_item_id, spot.lift), via)


def place_by(house_user_id: str, property_id: str, item: InventoryItem, room: Room, target_item_id: str, by: str, via: str = HAND) -> HousePiece | None:
    """Put a thing with a piece standing in the room, as its kind calls for: a lamp or a vase on
    it, a picture or a mirror on the wall over it, a rug under it, a floor lamp at its end, a
    ceiling light above it. None for floor furniture (the caller stands it next to the piece),
    and when there is no such place by that piece."""
    from services.api.furniture import dress

    if dress.kind_of(item) in ("solve", "corner", "lean"):
        return None
    key = str(item.item_id)
    now = dress.in_house(house_user_id, property_id, get_twin(property_id), room, without=key)
    if target_item_id not in now.poses or not now.place(item, target_item_id) or key not in now.spots:
        return None
    spot = now.spots[key]
    return furniture_store().put_piece(house_user_id, _piece(get_twin(property_id), property_id, key, room, spot.x, spot.z, spot.yaw, by, spot.on_item_id, spot.lift), via)


def _auto_floor(twin: PropertyTwin, house_user_id: str, property_id: str, item: InventoryItem, room: Room, by: str, via: str) -> HousePiece | None:
    """Stand a piece of floor furniture where such a piece goes in this room as it is furnished
    now: a coffee table before the sofa, a bedside table beside the bed, a chair at the desk or
    the dining table, a sofa or a bookcase with its back on the longest free stretch of solid
    wall. None when none of those spots is free."""
    from services.api.furniture import arrange

    key = str(item.item_id)
    items, poses = _room_state(house_user_id, property_id, room.room_id, without=key)
    standing = {k: i for k in poses if (i := furniture_store().get_item(k)) is not None}
    mine = [w for w in arrange.auto_wishes([*standing.values(), item], []) if w.item == key]
    faces = {w.target for w in mine if w.kind == "faces"}
    tries: list[tuple[float, float, float | None, float]] = []  # x, z, the way to face (None: any), how far to look around
    for w in mine:
        if w.target not in poses:
            continue
        at, other = poses[w.target], standing[w.target]
        (fx, fz), (rx, rz) = at.front, at.right
        if w.kind == "in_front_of":
            gap = 0.1 if item.item_class in ("chair", "stool") else 0.4  # a chair is pulled up to its desk; a table leaves room for legs
            out = other.dims_m.d / 2 + item.dims_m.d / 2 + gap
            tries.append((at.x + fx * out, at.z + fz * out, (at.yaw + 180) % 360 if w.target in faces else at.yaw, 0.8))
        elif w.kind == "beside":
            back = other.dims_m.d / 2 - item.dims_m.d / 2  # their backs in one line
            tries += [
                (at.x + side * rx * (other.dims_m.w / 2 + item.dims_m.w / 2 + 0.08) - fx * back, at.z + side * rz * (other.dims_m.w / 2 + item.dims_m.w / 2 + 0.08) - fz * back, at.yaw, 0.6)
                for side in (1, -1)
            ]
        elif w.kind == "near":
            tries.append((at.x, at.z, None, 2.0))
    table = next((k for k, i in standing.items() if i.item_class == "dining_table"), None)
    if item.item_class == "chair" and table:  # a free seat at the table: along its long sides, then its ends
        at, other = poses[table], standing[table]
        (fx, fz), (rx, rz) = at.front, at.right
        per_side = max(1, int(other.dims_m.w // (item.dims_m.w + 0.08)))
        out, end = other.dims_m.d / 2 + item.dims_m.d / 2 + 0.06, other.dims_m.w / 2 + item.dims_m.d / 2 + 0.06
        for n in range(per_side):
            along = (n - (per_side - 1) / 2) * (other.dims_m.w / per_side)
            tries += [(at.x + fx * out + rx * along, at.z + fz * out + rz * along, (at.yaw + 180) % 360, 0.0),
                      (at.x - fx * out + rx * along, at.z - fz * out + rz * along, at.yaw, 0.0)]
        tries += [(at.x + rx * end, at.z + rz * end, (at.yaw - 90) % 360, 0.0), (at.x - rx * end, at.z - rz * end, (at.yaw + 90) % 360, 0.0)]
    solver_room = _solver_room(twin, room)
    if any(w.kind == "against_wall" for w in mine) or item.item_class in layout_solver.WALL_PIECES:
        scene = layout_solver._scene(solver_room, twin.openings, twin.walls, twin.sweeps)
        for wall, part in sorted(scene.solid, key=lambda wp: -wp[1].length):
            if part.length >= item.dims_m.w:
                middle = part.interpolate(0.5, normalized=True)
                off = item.dims_m.d / 2 + layout_solver.WALL_GAP_M + 0.02
                tries.append((middle.x + wall.normal[0] * off, middle.y + wall.normal[1] * off, None, 1.2))
    me = _solver_item(item)
    for x, z, yaw, reach in tries:
        pose = layout_solver.nearest_valid_pose(solver_room, twin.openings, items + [me], poses, key, x, z, walls=twin.walls, reach_m=max(reach, 0.15), sweeps=twin.sweeps, yaw=yaw)
        if pose is not None:
            return furniture_store().put_piece(house_user_id, _piece(twin, property_id, key, room, pose.x, pose.z, pose.yaw, by), via)
    return None


def remove(house_user_id: str, property_id: str, item_id: str, by: str | None = None) -> None:
    """Take a piece out of the house (F7). It stays in storage."""
    if not furniture_store().remove_piece(house_user_id, property_id, item_id, by=by):
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
        pieces.append(_piece(
            twin, layout.property_id, str(p.item_id), here, p.position.x, p.position.z, p.yaw_deg, by,
            str(p.on_item_id) if p.on_item_id else None, p.lift_m,
        ))
    # What stands on the floor first: a lamp's table must be in the house before the lamp is put on it.
    pieces.sort(key=lambda piece: bool(piece.on_item_id or piece.lift_m))
    return store.apply_proposal(house_user_id, layout, pieces, by)


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
            for p in furniture_store().house_pieces(house_user_id, property_id, room_id)
            if str(p.item_id) != key and not p.on_item_id and not p.lift_m]  # F12: what rests on a piece stays with it
    out = []
    for n, pose in enumerate(picked):
        option = LayoutVariant(
            layout_id=uuid.uuid4(), user_id=house_user_id, property_id=property_id, room_id=room_id,
            placements=kept + [Placement(item_id=key, position=Vec3(x=round(pose.x, 3), y=floor, z=round(pose.z, 3)), yaw_deg=norm_yaw(pose.yaw))],
            score=0, rationale=f"Option {'ABC'[n]}: {item.label} {side(pose)}.", status="proposed")
        furniture_store().put_proposal(option)
        out.append(option)
    return out
