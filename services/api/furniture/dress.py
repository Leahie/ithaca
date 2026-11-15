"""Dressing a room: where the things go that are not floor furniture. Owner: B.

The layout solver (sim/layout_solver.py) arranges what stands on the floor and takes up room: the
sofa, the bed, the tables, the storage. A room that stops there looks like a showroom on moving
day. This puts in the rest, each by the rule a designer follows, around the furniture the solver
placed:

- a rug under the seating, the bed or the dining table, turned with it;
- a floor lamp at the end of a sofa or beside a reading chair; tall plants in the corners, the
  ones by a window first;
- table lamps, small plants and decorations on the tops that can carry them (F12), two lamps at
  the two ends of a long sideboard;
- pictures and mirrors on the wall, centred over the piece they belong to, at eye height, never
  over a door, a window or a tall piece;
- a ceiling light over the dining table, the coffee table or the middle of the room.

Nothing here moves a piece the solver placed. A thing with no good place is left out and named,
so the room is never made worse to fit one more object. Positions are in the interior frame,
metres; yaw as in sim/layout_solver.Pose (0 = the front along +Z).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon

from packages.schema.inventory import InventoryItem
from packages.schema.twin import PropertyTwin, Room
from services.api.furniture import house
from services.api.objects.classes import HANGS_FROM_CEILING, top_height
from services.api.sim import layout_solver
from services.api.sim.layout_solver import DEFAULT_WALL_M, Pose

EYE_M = 1.5  # the middle of a picture hangs about here
OVER_M = 0.22  # between the top of a piece and the picture over it
WALL_FACE_GAP_M = 0.01  # between the wall's face and the back of a thing hung on it
HEADROOM_M = 1.95  # the underside of a ceiling light over floor people walk on
OVER_TABLE_M = 0.6  # the underside of a ceiling light above the table under it
TOP_MAX_M = 1.3  # nothing is stood on a top higher than this (a wardrobe, a tall bookcase)
LEANS_M = 1.35  # a mirror this tall stands on the floor and leans on the wall
SMALL_PLANT_M = 0.6  # a plant lower than this goes on a table, a taller one on the floor
SEATS = ("sofa", "sectional", "loveseat")

# Which tops a small thing is put on, the best first.
_TOPS_FOR = {
    "table_lamp": ("nightstand", "side_table", "console_table", "cabinet", "desk", "dresser", "tv_stand"),
    "decor": ("coffee_table", "console_table", "cabinet", "dresser", "dining_table", "tv_stand", "desk", "side_table", "bookshelf", "nightstand"),
    "plant": ("console_table", "cabinet", "desk", "side_table", "dresser", "coffee_table", "tv_stand", "nightstand"),
}
# Which piece a hung thing goes over, the best first.
_OVER = {
    "wall_art": ("sofa", "sectional", "loveseat", "bed", "cabinet", "console_table", "desk", "dresser", "bench"),
    "mirror": ("console_table", "dresser", "cabinet", "desk", "bench"),
}


@dataclass
class Spot:
    """Where a thing goes. `on_item_id` and `lift` are those of a row of the house (F12)."""

    x: float
    z: float
    yaw: float
    on_item_id: str | None = None
    lift: float = 0.0


def kind_of(item: InventoryItem) -> str:
    """How a thing is placed: "solve" (floor furniture, the solver's), "rug", "beside" (a floor
    lamp), "corner" (a tall plant), "top", "wall", "lean" (a tall mirror) or "ceiling"."""
    cls = item.item_class
    if cls == "rug":
        return "rug"
    if cls in HANGS_FROM_CEILING:
        return "ceiling"
    if cls == "wall_art":
        return "wall"
    if cls == "mirror":
        return "lean" if item.dims_m.h >= LEANS_M else "wall"
    if cls in ("table_lamp", "decor"):
        return "top"
    if cls == "plant":
        return "top" if item.dims_m.h < SMALL_PLANT_M else "corner"
    if cls == "floor_lamp":
        return "beside"
    return "solve"


def _axes(yaw_deg: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """A piece's front and right in the room's (x, z)."""
    r = math.radians(yaw_deg)
    return (math.sin(r), math.cos(r)), (math.cos(r), -math.sin(r))


def _box(w: float, d: float, pose: Pose) -> Polygon:
    return layout_solver.footprint(layout_solver.Item("", "", "", w, max(d, 0.02), 1.0), pose)


@dataclass
class Dresser:
    """One room being dressed: the floor furniture as it stands, and what has been put in so far."""

    twin: PropertyTwin
    room: Room
    items: dict[str, InventoryItem]  # what stands on the floor, by item_id (lamps and plants join it as they are stood)
    poses: dict[str, Pose]
    spots: dict[str, Spot] = field(default_factory=dict)
    left: list[str] = field(default_factory=list)  # "label: why it is not in the room"
    _rugs: list[Polygon] = field(default_factory=list)
    _hung: list[Polygon] = field(default_factory=list)
    _tops: dict[str, list[tuple[float, float, float]]] = field(default_factory=dict)  # support -> (x, z, radius) taken on it
    _lit: set[str] = field(default_factory=set)  # pieces that already have a lamp by or on them
    _lamps_on: list[str] = field(default_factory=list)  # the top each table lamp stands on
    _rugged: set[str] = field(default_factory=set)  # pieces with a rug under them
    _pictured: set[str] = field(default_factory=set)  # pieces with a picture or a mirror over them
    _overhung: set[str] = field(default_factory=set)  # pieces (or "middle") with a ceiling light over them

    def __post_init__(self) -> None:
        self.solver_room = house._solver_room(self.twin, self.room)
        outline = Polygon(self.room.polygon)
        self.outline = outline if outline.is_valid else outline.buffer(0)
        thickness = [w.thickness_m for w in self.twin.walls if self.room.room_id in w.room_ids]
        self.inset = (max(thickness) if thickness else DEFAULT_WALL_M) / 2  # outlines run along the middle of the walls
        inner = self.outline.buffer(-self.inset, join_style="mitre")
        self.floor = max(getattr(inner, "geoms", [inner]), key=lambda g: g.area) if not inner.is_empty else self.outline
        self.middle = self.floor.representative_point() if not self.floor.contains(self.floor.centroid) else self.floor.centroid
        pts = [tuple(p) for p in self.room.polygon]
        self.edges = [(a, b) for a, b in zip(pts, pts[1:] + pts[:1]) if math.dist(a, b) > 0.5]
        scene = layout_solver._scene(self.solver_room, self.twin.openings, self.twin.walls, self.twin.sweeps)
        self.walls = [(wall.normal, part) for wall, part in scene.solid]  # (the way into the room, the stretch along the wall's face)
        mine = [o for o in self.twin.openings if self.room.room_id in (o.room_id, o.other_room_id) and len(o.polygon) >= 2]
        self.doors = [LineString([(o.polygon[0].x, o.polygon[0].z), (o.polygon[1].x, o.polygon[1].z)]) for o in mine if o.kind != "window"]
        self.windows = [LineString([(o.polygon[0].x, o.polygon[0].z), (o.polygon[1].x, o.polygon[1].z)]) for o in mine if o.kind == "window"]

    # --- helpers ----------------------------------------------------------------------------------

    def _first(self, classes: tuple[str, ...], free_of: set[str] | None = None) -> str | None:
        """The floor piece of the first of these classes there is one of (the largest of it), not in `free_of`."""
        for cls in classes:
            found = [k for k, i in self.items.items() if i.item_class == cls and k in self.poses and (free_of is None or k not in free_of)]
            if found:
                return max(found, key=lambda k: self.items[k].dims_m.w * self.items[k].dims_m.d)
        return None

    def _stand(self, item: InventoryItem, x: float, z: float, reach: float, yaw: float | None = None) -> Pose | None:
        """The nearest spot to (x, z) where a floor piece can stand without breaking a rule."""
        key = str(item.item_id)
        solid = [house._solver_item(i) for k, i in self.items.items() if k in self.poses and k != key]
        return layout_solver.nearest_valid_pose(
            self.solver_room, self.twin.openings, solid + [house._solver_item(item)], {k: p for k, p in self.poses.items() if k != key},
            key, x, z, walls=self.twin.walls, reach_m=reach, sweeps=self.twin.sweeps, yaw=yaw,
        )

    def _stood(self, item: InventoryItem, pose: Pose) -> None:
        key = str(item.item_id)
        self.items[key], self.poses[key] = item, pose
        self.spots[key] = Spot(pose.x, pose.z, pose.yaw)

    # --- on the floor -----------------------------------------------------------------------------

    def rug(self, rug: InventoryItem, target: str | None = None) -> bool:
        """Lay a rug under a group: the seating, the bed, the dining table, the desk; else in the
        middle of the room. Turned so its long side runs with the piece."""
        key = str(rug.item_id)
        target = target if target in self.poses else None
        target = target or self._first(SEATS, self._rugged) or self._first(("bed", "dining_table", "desk", "crib"), self._rugged)
        long, short = max(rug.dims_m.w, rug.dims_m.d), min(rug.dims_m.w, rug.dims_m.d)
        turned = 0.0 if rug.dims_m.w >= rug.dims_m.d else 90.0  # added to a yaw, it lays the rug's long side along that yaw's right
        tries: list[tuple[float, float, float]] = []
        if target:
            under, at = self.items[target], self.poses[target]
            (fx, fz), (rx, rz) = _axes(at.yaw)
            cls = under.item_class
            if cls in ("bed", "crib"):
                if long >= under.dims_m.w + 0.4:  # wide enough to show on both sides: under the foot of the bed
                    out = under.dims_m.d / 2 - short / 2 + 0.45
                    tries.append((at.x + fx * out, at.z + fz * out, at.yaw + turned))
                for side in (1, -1):  # else beside it, along its length
                    out = side * (under.dims_m.w / 2 + short / 2 + 0.05)
                    tries.append((at.x + rx * out, at.z + rz * out, at.yaw + turned + 90))
            elif cls == "dining_table":
                along = at.yaw if under.dims_m.w >= under.dims_m.d else at.yaw + 90
                tries.append((at.x, at.z, along + turned))
            else:  # seating, a desk: the rug starts under the piece's front and runs out before it
                out = under.dims_m.d / 2 - 0.3 + short / 2
                tries.append((at.x + fx * out, at.z + fz * out, at.yaw + turned))
        else:
            # No piece to lie under (a runner in a hall): along one of the room's long walls, as
            # near the middle of the room as it lies flat. A hall bends; its middle may not take it.
            ways = []
            for a, b in sorted(self.edges, key=lambda e: -math.dist(*e))[:3]:
                along = round(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))) % 180
                if along not in ways:
                    ways.append(along)
            minx, minz, maxx, maxz = self.floor.bounds
            spots = [(minx + 0.3 * i, minz + 0.3 * j) for i in range(int((maxx - minx) / 0.3) + 1) for j in range(int((maxz - minz) / 0.3) + 1)]
            spots = sorted((p for p in spots if self.floor.contains(Point(p))), key=lambda p: math.dist(p, (self.middle.x, self.middle.y)))
            tries += [(x, z, -along + turned) for x, z in [(self.middle.x, self.middle.y), *spots[:160]] for along in ways]
        room = self.floor.buffer(0.02)
        for x, z, yaw in tries:
            d = math.hypot(self.middle.x - x, self.middle.y - z) or 1.0
            for step in range(9 if target else 1):  # as wished, then nudged toward the middle of the room until it lies inside it
                px, pz = x + (self.middle.x - x) / d * min(0.15 * step, d), z + (self.middle.y - z) / d * min(0.15 * step, d)
                shape = _box(rug.dims_m.w, rug.dims_m.d, Pose(px, pz, yaw % 360))
                if room.contains(shape) and all(shape.intersection(other).area < 0.1 * shape.area for other in self._rugs):
                    self._rugs.append(shape)
                    self.spots[key] = Spot(px, pz, yaw % 360)
                    if target:
                        self._rugged.add(target)
                    return True
        self.left.append(f"{rug.label}: it is too large to lie flat in this room")
        return False

    def beside(self, lamp: InventoryItem, target: str | None = None) -> bool:
        """Stand a floor lamp by a seat: beside a reading chair first, then at the end of the sofa,
        then by the bed or the desk; its back in line with theirs. Failing that, in a corner."""
        order = [
            *([target] if target in self.poses and target not in self._lit else []),
            *(k for k, i in self.items.items() if i.item_class == "armchair" and k in self.poses and k not in self._lit and k != target),
            *(k for cls in (*SEATS, "bed", "desk") for k, i in self.items.items() if i.item_class == cls and k in self.poses and k not in self._lit and k != target),
        ]
        for by in order:
            under, at = self.items[by], self.poses[by]
            (fx, fz), (rx, rz) = _axes(at.yaw)
            back = under.dims_m.d / 2 - lamp.dims_m.d / 2
            sides = sorted((1, -1), key=lambda s: -self._clear_of_doors(at.x + s * rx * under.dims_m.w / 2, at.z + s * rz * under.dims_m.w / 2))
            for side in sides:
                out = side * (under.dims_m.w / 2 + lamp.dims_m.w / 2 + 0.1)
                pose = self._stand(lamp, at.x + rx * out - fx * back, at.z + rz * out - fz * back, 0.5, at.yaw)
                if pose is not None:
                    self._stood(lamp, pose)
                    self._lit.add(by)
                    return True
        return self.corner(lamp)

    def _clear_of_doors(self, x: float, z: float) -> float:
        return min((d.distance(Point(x, z)) for d in self.doors), default=9.0)

    def corner(self, item: InventoryItem) -> bool:
        """Stand a tall plant (or a lamp with no seat to go by) in a corner of the room: the ones
        nearest a window and furthest from a door first. Failing that, along a wall."""
        half = max(item.dims_m.w, item.dims_m.d) / 2
        pts = list(self.floor.exterior.coords)[:-1]
        corners = []
        for k, (x, z) in enumerate(pts):
            (ax, az), (bx, bz) = pts[k - 1], pts[(k + 1) % len(pts)]
            if min(math.dist((x, z), (ax, az)), math.dist((x, z), (bx, bz))) < 0.6:
                continue  # a jog in the wall, not a corner
            ux, uz = (ax - x) / math.dist((x, z), (ax, az)), (az - z) / math.dist((x, z), (ax, az))
            vx, vz = (bx - x) / math.dist((x, z), (bx, bz)), (bz - z) / math.dist((x, z), (bx, bz))
            if abs(ux * vx + uz * vz) > 0.5:
                continue  # too sharp or too flat to be a corner one puts a plant in
            px, pz = x + (ux + vx) * (half + 0.12), z + (uz + vz) * (half + 0.12)
            if not self.floor.contains(Point(px, pz)):
                continue  # the corner points out of the room
            light = min((w.distance(Point(px, pz)) for w in self.windows), default=6.0)
            corners.append((min(light, 6.0) - min(self._clear_of_doors(px, pz), 2.5), px, pz))
        for _score, px, pz in sorted(corners):
            pose = self._stand(item, px, pz, 0.35)
            if pose is not None:
                self._stood(item, pose)
                return True
        for a, b in sorted(self.edges, key=lambda e: -math.dist(*e)):  # no free corner: the middle of a wall
            mx, mz = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            d = math.hypot(self.middle.x - mx, self.middle.y - mz) or 1.0
            pull = self.inset + half + 0.1
            pose = self._stand(item, mx + (self.middle.x - mx) / d * pull, mz + (self.middle.y - mz) / d * pull, 0.6)
            if pose is not None:
                self._stood(item, pose)
                return True
        self.left.append(f"{item.label}: no free corner or wall for it")
        return False

    # --- on a piece (F12) -------------------------------------------------------------------------

    def _room_on(self, support: str) -> int:
        """How many things a top takes: one on a small table, three on a long sideboard."""
        s = self.items[support].dims_m
        return max(1, min(3, int(s.w * s.d / 0.18)))

    def on_top(self, thing: InventoryItem, target: str | None = None) -> bool:
        """Put a small thing on a piece that can carry it: a lamp on a bedside or side table (one
        each, so two lamps light the two sides of a bed), decorations on the coffee table and the
        sideboard, a small plant on a console."""
        cls = thing.item_class
        lamp = cls == "table_lamp"
        cushion = any(word in thing.label.lower() for word in ("pillow", "cushion"))
        wanted = (*SEATS, "armchair", "bed", "bench") if cushion else _TOPS_FOR.get(cls, _TOPS_FOR["decor"])
        order = [
            *([target] if target in self.poses else []),
            *(k for want in wanted for k, i in sorted(self.items.items(), key=lambda kv: len(self._tops.get(kv[0], ())))
              if i.item_class == want and k in self.poses and k != target),
        ]
        for support in order:
            under, at = self.items[support], self.poses[support]
            taken = self._tops.setdefault(support, [])
            top = top_height(under.item_class, under.dims_m.h)
            if not house.can_rest_on(thing, under) or top > TOP_MAX_M or len(taken) >= self._room_on(support):
                continue
            long = under.dims_m.w >= 1.3
            if lamp and (support in self._lit and not (long and sum(1 for k in self._lamps_on if k == support) < 2)):
                continue  # one lamp to a table; a long sideboard takes a pair
            (fx, fz), (rx, rz) = _axes(at.yaw)
            half_w = max(under.dims_m.w / 2 - thing.dims_m.w / 2 - 0.03, 0.0)
            half_d = max(under.dims_m.d / 2 - thing.dims_m.d / 2 - 0.03, 0.0)
            radius = max(thing.dims_m.w, thing.dims_m.d) / 2
            if lamp:  # at the back; on a long piece toward an end, the second lamp at the other end
                ends = [-0.34 * under.dims_m.w, 0.34 * under.dims_m.w] if long else [0.0]
                places = [(e, -0.35 * half_d) for e in ends]
            elif cushion:  # in the corners of the seat, against the arms
                places = [(s * f * half_w, -0.1 * half_d) for f in (0.85, 0.4, 0.0) for s in (1, -1)]
            else:  # a little off the middle, as a person would set a vase down
                places = [(s * f * half_w, 0.0) for f in (0.3, 0.0, 0.6) for s in (1, -1)]
            for along, back in places:
                along = min(max(along, -half_w), half_w)
                x, z = at.x + rx * along + fx * back, at.z + rz * along + fz * back
                if all(math.hypot(x - tx, z - tz) >= radius + tr + 0.02 for tx, tz, tr in taken):
                    taken.append((x, z, radius))
                    self.spots[str(thing.item_id)] = Spot(x, z, at.yaw, support, top)
                    if lamp:
                        self._lit.add(support)
                        self._lamps_on.append(support)
                    return True
        self.left.append(f"{thing.label}: no free top to stand it on")
        return False

    # --- on a wall --------------------------------------------------------------------------------
    # Things hang on the solid stretches of the room's walls (`self.walls`, from the solver's
    # scene): no door, window or open side of the room in them, measured along the wall's face.

    def _wall_of(self, piece: str) -> int | None:
        """The stretch of wall a floor piece has its back to, if it stands against one."""
        under, at = self.items[piece], self.poses[piece]
        (fx, fz), _ = _axes(at.yaw)
        back = Point(at.x - fx * under.dims_m.d / 2, at.z - fz * under.dims_m.d / 2)
        near = [(part.distance(back), k) for k, (normal, part) in enumerate(self.walls) if normal[0] * fx + normal[1] * fz > 0.7]
        return min(near)[1] if near and min(near)[0] < 0.4 else None

    def _taken(self, k: int, lift: float, skip: str | None = None) -> list[tuple[float, float]]:
        """Spans along a stretch of wall that furniture standing against it reaches up into, at
        the height a thing would hang (all of them, for a thing that stands on the floor)."""
        _normal, part = self.walls[k]
        spans = []
        for key, at in self.poses.items():
            item = self.items[key]
            if key == skip or item.dims_m.h <= lift - 0.03:
                continue
            shape = _box(item.dims_m.w, item.dims_m.d, at)
            if shape.distance(part) < 0.35:
                along = [part.project(Point(c)) for c in shape.exterior.coords]
                spans.append((min(along) - 0.05, max(along) + 0.05))
        return spans

    def _on_wall(self, thing: InventoryItem, k: int, want: float, lift: float, skip: str | None = None, reach: float = 3.0) -> Spot | None:
        """Flat against a stretch of wall, facing in, as near `want` (metres along it) as it is free."""
        (nx, nz), part = self.walls[k]
        half, depth = thing.dims_m.w / 2, max(thing.dims_m.d, 0.02)
        if part.length < thing.dims_m.w + 0.06:
            return None
        taken = self._taken(k, lift, skip)
        start = min(max(want, half + 0.03), part.length - half - 0.03)
        yaw = math.degrees(math.atan2(nx, nz)) % 360  # its front (sin yaw, cos yaw) looks into the room
        out = depth / 2 + WALL_FACE_GAP_M
        for step in range(int(reach / 0.1) * 2 + 1):  # the wanted place, then further to either side of it
            t = start + (0.0 if step == 0 else math.ceil(step / 2) * 0.1 * (1 if step % 2 else -1))
            if t - half < 0.03 or t + half > part.length - 0.03 or any(t + half > lo and t - half < hi for lo, hi in taken):
                continue
            on = part.interpolate(t)
            x, z = on.x + nx * out, on.y + nz * out
            shape = _box(thing.dims_m.w, depth + 0.1, Pose(x, z, yaw))
            if all(not shape.buffer(0.06).intersects(other) for other in self._hung):
                self._hung.append(shape)
                return Spot(x, z, yaw, None, round(lift, 3))
        return None

    def hang(self, thing: InventoryItem, target: str | None = None) -> bool:
        """Hang a picture or a mirror: centred over the piece it belongs to (a picture over the
        sofa or the bed, a mirror over a console or a dresser), a hand above it and near eye
        height; else in the middle of the longest free stretch of wall, at eye height."""
        ceiling = self.room.height_m - thing.dims_m.h - 0.08
        wanted = _OVER.get(thing.item_class, _OVER["wall_art"])
        order = [
            *([target] if target in self.poses and target not in self._pictured else []),
            *(k for want in wanted for k, i in self.items.items() if i.item_class == want and k in self.poses and k not in self._pictured and k != target),
        ]
        for over in order:
            under, at = self.items[over], self.poses[over]
            wall = self._wall_of(over)
            lift = max(under.dims_m.h + OVER_M, EYE_M - thing.dims_m.h / 2)
            if wall is None or thing.dims_m.w > under.dims_m.w + 0.3:
                continue
            if lift > ceiling:
                lift = under.dims_m.h + 0.1  # a tall headboard under a low ceiling: closer over it
                if lift > ceiling:
                    continue
            spot = self._on_wall(thing, wall, self.walls[wall][1].project(Point(at.x, at.z)), lift, skip=over, reach=0.5)
            if spot is not None:
                self.spots[str(thing.item_id)] = spot
                self._pictured.add(over)
                return True
        return self._free_wall(thing, max(min(EYE_M - thing.dims_m.h / 2, ceiling), 0.3))

    def lean(self, mirror: InventoryItem) -> bool:
        """A tall mirror stands on the floor against a free stretch of wall."""
        return self._free_wall(mirror, 0.0)

    def _free_wall(self, thing: InventoryItem, lift: float) -> bool:
        """In the middle of the longest stretch of wall that nothing stands or hangs on."""
        best: tuple[float, int, float] | None = None
        for k, (_normal, part) in enumerate(self.walls):
            at = 0.0
            for lo, hi in sorted(self._taken(k, lift)) + [(part.length, part.length)]:
                if lo - at >= thing.dims_m.w + 0.3 and (best is None or lo - at > best[0]):
                    best = (lo - at, k, (at + lo) / 2)
                at = max(at, hi)
        if best is not None:
            spot = self._on_wall(thing, best[1], best[2], lift, reach=0.6)
            if spot is not None:
                self.spots[str(thing.item_id)] = spot
                return True
        self.left.append(f"{thing.label}: no free stretch of wall for it")
        return False

    # --- from the ceiling -------------------------------------------------------------------------

    def overhead(self, light: InventoryItem, target: str | None = None) -> bool:
        """Hang a ceiling light over the dining table, else the coffee table, else the middle of
        the room when it leaves headroom to walk under."""
        lift = self.room.height_m - light.dims_m.h - 0.01
        target = target if target in self.poses else None
        over = target or self._first(("dining_table",), self._overhung) or self._first(("coffee_table",), self._overhung)
        if over and lift >= self.items[over].dims_m.h + OVER_TABLE_M:
            at = self.poses[over]
            self.spots[str(light.item_id)] = Spot(at.x, at.z, at.yaw, None, round(lift, 3))
            self._overhung.add(over)
            return True
        if lift >= HEADROOM_M and "middle" not in self._overhung:
            self.spots[str(light.item_id)] = Spot(self.middle.x, self.middle.y, 0.0, None, round(lift, 3))
            self._overhung.add("middle")
            return True
        self.left.append(f"{light.label}: the ceiling is too low to hang it where people walk")
        return False

    # --- all of it --------------------------------------------------------------------------------

    def place(self, thing: InventoryItem, target: str | None = None) -> bool:
        """Put one thing in by its kind. False (and a line in `left`) when it has no place."""
        how = kind_of(thing)
        if how == "rug":
            return self.rug(thing, target)
        if how == "beside":
            return self.beside(thing, target)
        if how == "corner":
            return self.corner(thing)
        if how == "top":
            return self.on_top(thing, target)
        if how == "wall":
            return self.hang(thing, target)
        if how == "lean":
            return self.lean(thing)
        if how == "ceiling":
            return self.overhead(thing, target)
        return False


_ORDER = {"rug": 0, "beside": 1, "corner": 2, "top": 3, "wall": 4, "lean": 4, "ceiling": 5}


def dress(
    twin: PropertyTwin, room: Room, floor: dict[str, InventoryItem], poses: dict[str, Pose], things: list[InventoryItem],
    targets: dict[str, str] | None = None,
) -> tuple[dict[str, Spot], list[str]]:
    """Put `things` into a room whose floor furniture (`floor`, `poses`) is arranged. `targets`:
    item_id of a thing -> item_id of the floor piece it goes with, where someone said so.
    Returns where each thing went (those with a place) and "label: why not" for the rest.
    Rugs go down first, then what stands on the floor (it takes room), then what stands on the
    furniture, then the walls and the ceiling."""
    room_now = Dresser(twin, room, dict(floor), dict(poses))
    for thing in sorted(things, key=lambda t: _ORDER.get(kind_of(t), 9)):
        room_now.place(thing, (targets or {}).get(str(thing.item_id)))
    return room_now.spots, room_now.left


def in_house(house_user_id: str, property_id: str, twin: PropertyTwin, room: Room, without: str | None = None) -> Dresser:
    """The room as it stands in the house now, ready for one more thing: its floor furniture, and
    which tops, walls and lamps are already taken. `without`: a piece to leave out (the one being
    placed, when it is moved within the room)."""
    from services.api.furniture.store import furniture_store

    store = furniture_store()
    pieces = [p for p in store.house_pieces(house_user_id, property_id, room.room_id) if str(p.item_id) != without]
    found = {str(p.item_id): store.get_item(str(p.item_id)) for p in pieces}
    floor = {str(p.item_id): p for p in pieces if not p.on_item_id and not p.lift_m and found[str(p.item_id)] is not None}
    now = Dresser(
        twin, room,
        {k: found[k] for k, p in floor.items() if found[k].item_class != "rug"},
        {k: Pose(p.position.x, p.position.z, p.yaw_deg) for k, p in floor.items() if found[k].item_class != "rug"},
    )
    for k, p in floor.items():
        if found[k].item_class == "rug":
            now._rugs.append(_box(found[k].dims_m.w, found[k].dims_m.d, Pose(p.position.x, p.position.z, p.yaw_deg)))
        elif found[k].item_class == "floor_lamp":  # the seat it stands by is lit
            near = min((s for s in now.poses if s != k), key=lambda s: math.hypot(now.poses[s].x - p.position.x, now.poses[s].z - p.position.z), default=None)
            if near is not None:
                now._lit.add(near)
    for p in pieces:
        item = found[str(p.item_id)]
        if item is None:
            continue
        if p.on_item_id:
            now._tops.setdefault(str(p.on_item_id), []).append((p.position.x, p.position.z, max(item.dims_m.w, item.dims_m.d) / 2))
            if item.item_class == "table_lamp":
                now._lit.add(str(p.on_item_id))
                now._lamps_on.append(str(p.on_item_id))
        elif p.lift_m > 0:
            if item.item_class in HANGS_FROM_CEILING:
                under = min(now.poses, key=lambda s: math.hypot(now.poses[s].x - p.position.x, now.poses[s].z - p.position.z), default=None)
                near = under is not None and math.hypot(now.poses[under].x - p.position.x, now.poses[under].z - p.position.z) < 0.5
                now._overhung.add(under if near else "middle")
            else:
                now._hung.append(_box(item.dims_m.w, max(item.dims_m.d, 0.02) + 0.1, Pose(p.position.x, p.position.z, p.yaw_deg)))
    return now
