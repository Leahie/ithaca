"""A person's own room, set up in a room of the house (docs/specs/furniture.md, "From a photo").

objects/pipeline.py turns one photo of someone's room into pieces, and says how they stood: which
were on the floor, which stood on another piece and where on it, which hung on a wall and how high.
`furnish` puts that into a room of the house:

- what stood on the floor is arranged by the layout solver, so it fits this room's walls, doors
  and windows, with the wishes any designer would have (the bed on a solid wall, the nightstand
  beside it, the chair at the desk) and the pairings seen in the photo;
- what stood on a piece is put on top of that piece, where it stood on it (F12);
- what hung on a wall is hung on the wall behind the piece it hung nearest, at the height it hung.

The room becomes exactly that: what stood in it before goes back to storage (store.set_room).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon

from packages.schema.inventory import InventoryItem
from packages.schema.layout import HousePiece
from services.api.furniture import house
from services.api.furniture.house import FurnitureError
from services.api.furniture.store import PHOTO, furniture_store
from services.api.objects.pipeline import RoomScene, SceneThing
from services.api.sim import layout_solver
from services.api.sim.layout_solver import Constraint, Pose
from services.api.twin.service import get_twin

MAX_FLOOR_SHARE = 0.42  # of the room's floor that furniture may cover before the least important pieces are left out
M2_PER_PIECE = 2.2  # a room holds about one piece on the floor per this much floor: more, and no arrangement keeps the ways through open
SOLVER_TRIES = 3
HANG_M = {"wall_art": 1.35, "mirror": 1.0}  # how high a thing hangs when the photo did not say
DEFAULT_HANG_M = 1.4
EDGE_M = 0.03  # between a thing and the edge of the top it stands on, or the wall it hangs on


@dataclass
class Outcome:
    """What came of furnishing a room from a photo."""

    room_id: str
    room_name: str
    placed: list[InventoryItem] = field(default_factory=list)
    left: list[str] = field(default_factory=list)  # "label: why it is not in the room" (it is in storage)
    skipped: list[str] = field(default_factory=list)  # "label: why it could not be made at all"

    def summary(self) -> str:
        """For the guide: what is in the room now, and what is not."""
        if not self.placed:
            return f"Nothing from the photo could be put in {self.room_name}." + "".join(f" Could not make {s}." for s in self.skipped)
        names = ", ".join(f"#{i.tag} {i.label}" for i in self.placed)
        text = f"{self.room_name} (room_id {self.room_id}) is now furnished with {len(self.placed)} things from their own room: {names}."
        if self.left:
            text += " In storage, not in the room: " + "; ".join(self.left) + "."
        if self.skipped:
            text += " Could not be made from the photo: " + "; ".join(self.skipped) + "."
        return text


def _wishes(floor: list[SceneThing]) -> list[Constraint]:
    """What a designer would ask of these pieces, plus what went with what in the photo."""
    by_class: dict[str, list[SceneThing]] = {}
    for t in floor:
        by_class.setdefault(t.item.item_class, []).append(t)
    ids = {str(t.item.item_id) for t in floor}
    first = lambda *classes: next((str(t.item.item_id) for c in classes for t in by_class.get(c, [])), None)  # noqa: E731
    bed, desk, seat = first("bed", "crib"), first("desk"), first("sofa", "sectional", "loveseat", "armchair")
    wishes: list[Constraint] = []
    for t in floor:
        me, cls = str(t.item.item_id), t.item.item_class
        paired = t.beside if t.beside in ids else None
        if cls in ("bed", "crib"):
            wishes += [Constraint(kind="against_wall", item=me, target="solid", weight=3), Constraint(kind="away_from", item=me, target="door", weight=1)]
        elif cls == "nightstand" and (paired or bed):
            wishes.append(Constraint(kind="beside", item=me, target=paired or bed, weight=2))
        elif cls in ("chair", "stool") and (paired or desk):
            wishes += [Constraint(kind="in_front_of", item=me, target=paired or desk, weight=2), Constraint(kind="faces", item=me, target=paired or desk, weight=1)]
        elif cls in ("dresser", "wardrobe", "bookshelf", "cabinet", "tv_stand", "console_table", "desk"):
            wishes.append(Constraint(kind="against_wall", item=me, target="solid", weight=2))
        elif cls == "rug" and (bed or seat):
            wishes.append(Constraint(kind="near", item=me, target=bed or seat, weight=1))
        elif cls == "bench" and bed:
            wishes.append(Constraint(kind="in_front_of", item=me, target=bed, weight=1.5))
        elif paired:
            wishes.append(Constraint(kind="beside", item=me, target=paired, weight=1.5))
        elif cls in ("armchair", "floor_lamp", "plant", "mirror", "other", "decor"):
            wishes.append(Constraint(kind="against_wall", item=me, target="any", weight=1))
    return wishes


def _axes(yaw_deg: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """A piece's front and right in the room's (x, z), as sim/layout_solver.Pose has them."""
    r = math.radians(yaw_deg)
    return (math.sin(r), math.cos(r)), (math.cos(r), -math.sin(r))


def _on_top(thing: SceneThing, under: InventoryItem, at: Pose, taken: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    """Where a thing stands on the top of the piece under it: (x, z, yaw). At its place in the
    photo when that is known, kept inside the top's edges, and clear of what already stands there."""
    front, right = _axes(at.yaw)
    half_d = max(under.dims_m.d / 2 - thing.item.dims_m.d / 2 - EDGE_M, 0.0)
    half_w = max(under.dims_m.w / 2 - thing.item.dims_m.w / 2 - EDGE_M, 0.0)
    if thing.offset_m is not None:
        toward_front, toward_right = thing.offset_m
    else:
        toward_front, toward_right = -half_d / 2, 0.0  # a little back from the middle, as a lamp stands on a table
    toward_front = min(max(toward_front, -half_d), half_d)
    toward_right = min(max(toward_right, -half_w), half_w)
    radius = max(thing.item.dims_m.w, thing.item.dims_m.d) / 2
    # Two things that would stand in each other: the later one moves along the top to the nearest free place.
    for step in range(13):
        shift = 0.0 if step == 0 else math.ceil(step / 2) * 0.12 * (1 if step % 2 else -1)
        along = min(max(toward_right + shift, -half_w), half_w)
        x = at.x + front[0] * toward_front + right[0] * along
        z = at.z + front[1] * toward_front + right[1] * along
        if all(math.hypot(x - tx, z - tz) >= radius + tr for tx, tz, tr in taken):
            break
    taken.append((x, z, radius))
    return x, z, at.yaw + (thing.turn_deg or 0.0)


def _wall_spot(room, twin, thing: SceneThing, near: Pose | None, near_item: InventoryItem | None, taken: list[Polygon]) -> tuple[float, float, float] | None:
    """Where a thing hangs: (x, z, yaw) flat against a wall of the room, facing in. On the wall
    behind the piece it hung nearest, at its place along that piece when known; else in the middle
    of the room's longest wall. Never over a door or a window, nor over another hung thing."""
    outline = Polygon(room.polygon)
    pts = [tuple(p) for p in room.polygon]
    edges = [(a, b) for a, b in zip(pts, pts[1:] + pts[:1]) if math.dist(a, b) > 0.5]
    if not edges:
        return None
    if near is not None and near_item is not None:
        front, right = _axes(near.yaw)
        back = (near.x - front[0] * near_item.dims_m.d / 2, near.z - front[1] * near_item.dims_m.d / 2)
        a, b = min(edges, key=lambda e: LineString(e).distance(Point(back)))
        along = min(max(thing.offset_m[1] if thing.offset_m else 0.0, -near_item.dims_m.w / 2 - 0.6), near_item.dims_m.w / 2 + 0.6)
        want = (near.x + right[0] * along, near.z + right[1] * along)
    else:
        a, b = max(edges, key=lambda e: math.dist(*e))
        want = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    wall = LineString([a, b])
    length = wall.length
    ux, uz = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    nx, nz = -uz, ux  # one of the wall's two sides; the one that looks into the room is found below
    probe = wall.interpolate(length / 2)
    if not outline.contains(Point(probe.x + nx * 0.2, probe.y + nz * 0.2)):
        nx, nz = -nx, -nz
    half = thing.item.dims_m.w / 2
    depth = thing.item.dims_m.d
    # Openings in this wall, as stretches along it that nothing may hang over.
    closed = []
    for o in twin.openings:
        if room.room_id not in (o.room_id, o.other_room_id) or len(o.polygon) < 2:
            continue
        p0, p1 = (o.polygon[0].x, o.polygon[0].z), (o.polygon[1].x, o.polygon[1].z)
        if wall.distance(Point(p0)) < 0.35 and wall.distance(Point(p1)) < 0.35:
            lo, hi = sorted((wall.project(Point(p0)), wall.project(Point(p1))))
            closed.append((lo - 0.1, hi + 0.1))
    start = min(max(wall.project(Point(want)), half + 0.05), max(length - half - 0.05, half + 0.05))
    for step in range(41):  # the wanted place, then further and further to either side of it
        t = start + (0.0 if step == 0 else math.ceil(step / 2) * 0.15 * (1 if step % 2 else -1))
        if t - half < 0.05 or t + half > length - 0.05 or any(t + half > lo and t - half < hi for lo, hi in closed):
            continue
        x = a[0] + ux * t + nx * (depth / 2 + EDGE_M)
        z = a[1] + uz * t + nz * (depth / 2 + EDGE_M)
        yaw = math.degrees(math.atan2(nx, nz))  # a piece's front is (sin yaw, cos yaw): it looks into the room
        shape = layout_solver.footprint(layout_solver.Item("", "", "", thing.item.dims_m.w, max(depth, 0.05), thing.item.dims_m.h), Pose(x, z, yaw))
        if all(not shape.buffer(0.04).intersects(other) for other in taken):
            taken.append(shape)
            return x, z, yaw
    return None


def furnish(house_user_id: str, property_id: str, room_id: str, scene: RoomScene, by: str) -> Outcome:
    """Set a room of the house up with the things from a photo of the person's own room. `by` is
    the person. Raises FurnitureError for an unknown room."""
    twin = get_twin(property_id)
    room = house.room_by_id(twin, room_id)
    if room is None:
        raise FurnitureError(404, "no such room")
    from services.api.agent import prompts

    outcome = Outcome(room_id=room_id, room_name=prompts.room_short_names(twin).get(room_id, room.label), skipped=list(scene.skipped))
    if not scene.things:
        return outcome
    by_id = {str(t.item.item_id): t for t in scene.things}
    floor = [t for t in scene.things if not t.on_wall and not t.rests_on]

    # Too much for this room: the least important floor pieces (the last ones) wait in storage.
    area = Polygon(room.polygon).area
    covers = lambda: sum(t.item.dims_m.w * t.item.dims_m.d for t in floor if t.item.item_class != "rug")  # noqa: E731
    while len(floor) > 1 and (covers() > MAX_FLOOR_SHARE * area or len(floor) > max(3, area / M2_PER_PIECE)):
        outcome.left.append(f"{floor.pop().item.label}: this room is too small for everything in the photo")
    solver_room = house._solver_room(twin, room)
    wishes = _wishes(floor)
    poses: dict[str, Pose] = {}
    for _ in range(SOLVER_TRIES):
        if not floor:
            break
        ids = {str(t.item.item_id) for t in floor}
        result = layout_solver.solve(
            solver_room, twin.openings, [house._solver_item(t.item) for t in floor],
            [w for w in wishes if w.item in ids and (w.target in ids or len(w.target) < 32)], n=1, walls=twin.walls, sweeps=twin.sweeps,
        )
        if result.variants:
            poses = result.variants[0].poses
            break
        outcome.left.append(f"{floor.pop().item.label}: no place for it could be found in this room")
    else:
        outcome.left += [f"{t.item.label}: no arrangement of the room worked with it" for t in floor]
        floor = []

    rows: list[HousePiece] = []
    for t in floor:
        key = str(t.item.item_id)
        if key in poses:
            rows.append(house._piece(twin, property_id, key, room, poses[key].x, poses[key].z, poses[key].yaw, by))
            outcome.placed.append(t.item)
        else:
            outcome.left.append(f"{t.item.label}: no place for it could be found in this room")

    # What stood on a piece goes on that piece (F12).
    tops: dict[str, list[tuple[float, float, float]]] = {}
    for t in scene.things:
        if not t.rests_on:
            continue
        under = by_id.get(t.rests_on)
        if under is None or t.rests_on not in poses:
            outcome.left.append(f"{t.item.label}: {under.item.label if under else 'the piece it stood on'} is not in the room")
            continue
        x, z, yaw = _on_top(t, under.item, poses[t.rests_on], tops.setdefault(t.rests_on, []))
        rows.append(house._piece(twin, property_id, str(t.item.item_id), room, x, z, yaw, by, t.rests_on, under.item.dims_m.h))
        outcome.placed.append(t.item)

    # What hung on a wall hangs on a wall: behind the piece it hung nearest. When the photo did not
    # say which, behind the room's main piece (the bed), to its left and right in turn.
    hung: list[Polygon] = []
    main = next((t for t in floor if str(t.item.item_id) in poses and t.item.item_class != "rug"), None)
    unanchored = 0
    for t in scene.things:
        if not t.on_wall:
            continue
        near = by_id.get(t.anchor) if t.anchor and t.anchor in poses else None
        if near is None and main is not None:
            near = main
            side = 1 if unanchored % 2 == 0 else -1
            t = SceneThing(item=t.item, on_wall=True, anchor=str(main.item.item_id), hang_m=t.hang_m,
                           offset_m=(0.0, side * (main.item.dims_m.w / 2 + t.item.dims_m.w / 2 + 0.15)))
            unanchored += 1
        spot = _wall_spot(room, twin, t, poses.get(t.anchor) if near else None, near.item if near else None, hung)
        if spot is None:
            outcome.left.append(f"{t.item.label}: no free stretch of wall for it")
            continue
        lift = t.hang_m if t.hang_m is not None else HANG_M.get(t.item.item_class, DEFAULT_HANG_M)
        lift = min(lift, max(room.height_m - t.item.dims_m.h - 0.05, 0.3))  # under the ceiling
        if near is not None:  # above the piece it hung over, not through it
            lift = max(lift, near.item.dims_m.h + 0.1)
        rows.append(house._piece(twin, property_id, str(t.item.item_id), room, spot[0], spot[1], spot[2], by, None, lift))
        outcome.placed.append(t.item)

    if rows:
        furniture_store().set_room(house_user_id, property_id, room_id, rows, by, PHOTO)
    return outcome
