"""Furniture layout solver. Owner: B. Pure function of (room, openings, items, constraints).

The designer agent says what should be true ("sofa against a wall, facing the window"). This code
finds positions that make it true. It never returns a layout that breaks a hard rule (B2):

- every footprint is inside the room outline;
- no two pieces overlap (rugs may lie under other pieces);
- the floor in front of every door is clear, and tall pieces don't stand in front of windows;
- a person can still walk between the doors (a 0.9 m wide path).

Search is simulated annealing over (x, z, yaw) per piece, restarted from several random starts.
From all valid results it returns up to `n` that differ visibly from each other. If it finds fewer,
it returns fewer and says why.

Geometry is in the interior frame, plan view (x, z), meters. Yaw follows I-G12: degrees about +Y,
0 = the piece's front points along +Z, so front = (sin yaw, cos yaw).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from packages.schema.twin import Opening, Room, Wall

PATH_M = 0.9  # width a person needs to walk
DOOR_CLEAR_M = 0.9  # clear floor in front of a door
WINDOW_CLEAR_M = 0.3
GAP_M = 0.05  # minimum gap between two pieces
WALL_GAP_M = 0.03
RESTARTS = 14
STEPS_PER_ITEM = 260
HARD = 1000.0  # energy per unit of hard violation: always dominates the soft terms
PASSAGE_MIN_M = 0.8  # a gap this wide in a room's walls is a way through, like a door with no door in it
WALKWAY_M = 1.0  # width kept free along the straight line between two ways in
WALKWAY_COST = 3.0  # soft cost per m² of furniture standing in a walkway
# Pieces that look wrong away from a wall. They get a mild against_wall wish if the designer gave none.
WALL_PIECES = {"sofa", "loveseat", "sectional", "bed", "crib", "bookshelf", "cabinet", "dresser", "wardrobe", "tv_stand", "console_table", "nightstand", "desk"}


class Constraint(BaseModel):
    """One wish from the designer. `item` and item-valued `target`s are item_ids."""

    kind: Literal["against_wall", "faces", "near", "in_front_of", "beside", "away_from"]
    item: str
    # against_wall: "any" | "longest" | "window" (a wall with a window) | "solid" (a wall without)
    # faces: an item_id | "window" | "door" | "center"
    # near / in_front_of / beside: an item_id
    # away_from: "door" | "window"
    target: str = "any"
    weight: float = 1.0

    def describe(self, labels: dict[str, str]) -> str:
        name = lambda x: labels.get(x, x)  # noqa: E731
        return {
            "against_wall": f"{name(self.item)} against a wall ({self.target})",
            "faces": f"{name(self.item)} faces {name(self.target)}",
            "near": f"{name(self.item)} near {name(self.target)}",
            "in_front_of": f"{name(self.item)} in front of {name(self.target)}",
            "beside": f"{name(self.item)} beside {name(self.target)}",
            "away_from": f"{name(self.item)} away from the {self.target}s",
        }[self.kind]


@dataclass(frozen=True)
class Item:
    item_id: str
    label: str
    item_class: str
    w: float  # along the piece's X
    d: float  # along the piece's Z (front to back)
    h: float

    @property
    def is_rug(self) -> bool:
        return self.item_class == "rug"


@dataclass(frozen=True)
class Pose:
    x: float
    z: float
    yaw: float  # degrees

    @property
    def front(self) -> tuple[float, float]:
        r = math.radians(self.yaw)
        return math.sin(r), math.cos(r)

    @property
    def right(self) -> tuple[float, float]:
        r = math.radians(self.yaw)
        return math.cos(r), -math.sin(r)


@dataclass
class Variant:
    poses: dict[str, Pose]
    score: float  # 0-100: how well the wishes are met
    met: list[str]
    unmet: list[str]


@dataclass
class SolveResult:
    variants: list[Variant]
    reason: str | None = None  # set when fewer than n variants came back


def footprint(item: Item, pose: Pose) -> Polygon:
    fx, fz = pose.front
    rx, rz = pose.right
    hw, hd = item.w / 2, item.d / 2
    return Polygon(
        [(pose.x + sx * hw * rx + sz * hd * fx, pose.z + sx * hw * rz + sz * hd * fz) for sx, sz in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    )


@dataclass
class _Wall:
    a: tuple[float, float]
    b: tuple[float, float]
    normal: tuple[float, float]  # into the room
    has_window: bool = False

    @property
    def length(self) -> float:
        return math.dist(self.a, self.b)

    @property
    def yaw(self) -> float:  # yaw of a piece standing with its back to this wall
        return math.degrees(math.atan2(self.normal[0], self.normal[1])) % 360


@dataclass
class _Scene:
    """Everything about the room that doesn't change while solving."""

    room: Polygon
    walls: list[_Wall]
    door_zones: list[Polygon]
    door_entries: list[Point]
    window_zones: list[tuple[Polygon, float]]  # (zone, sill height above the floor)
    door_points: list[Point]
    window_points: list[Point]
    center: Point
    linked_doors: list[tuple[int, int]] = field(default_factory=list)  # door pairs walkable when the room is empty
    walkways: list[Polygon] = field(default_factory=list)  # straight lines people walk between the ways in
    lanes: Polygon | None = None  # all walkways merged, computed once


def _passages(room: Room, outline: Polygon, walls: list[Wall]) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Stretches of the room's outline with no wall behind them: open-plan edges where one room
    runs into the next. People walk through these, so they are treated like doorways."""
    mine = [LineString(w.ends) for w in walls if room.room_id in w.room_ids]
    if not mine:
        return []
    built = unary_union(mine).buffer(0.3)
    out = []
    pts = list(outline.exterior.coords)
    for a, b in zip(pts, pts[1:]):
        gap = LineString([a, b]).difference(built)
        for part in getattr(gap, "geoms", [gap]):
            if not part.is_empty and part.length >= PASSAGE_MIN_M:
                out.append((part.coords[0], part.coords[-1]))
    return out


def _scene(room: Room, openings: list[Opening], twin_walls: list[Wall] | None = None) -> _Scene:
    poly = Polygon(room.polygon)
    if not poly.is_valid:
        poly = poly.buffer(0)
    outline = poly.simplify(0.03)
    pts = list(outline.exterior.coords)[:-1]
    walls = []
    for a, b in zip(pts, pts[1:] + pts[:1]):
        length = math.dist(a, b)
        if length < 0.3:
            continue
        nx, nz = (b[1] - a[1]) / length, -(b[0] - a[0]) / length
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if not poly.contains(Point(mid[0] + nx * 0.1, mid[1] + nz * 0.1)):
            nx, nz = -nx, -nz
        walls.append(_Wall(a, b, (nx, nz)))

    scene = _Scene(poly, walls, [], [], [], [], [], poly.representative_point() if not poly.contains(poly.centroid) else poly.centroid)
    for o in openings:
        if room.room_id not in (o.room_id, o.other_room_id):
            continue
        a, b = (o.polygon[0].x, o.polygon[0].z), (o.polygon[1].x, o.polygon[1].z)
        seg, mid = LineString([a, b]), Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        wall = min(walls, key=lambda w: LineString([w.a, w.b]).distance(mid), default=None)
        if o.kind == "window":
            scene.window_zones.append((seg.buffer(WINDOW_CLEAR_M, cap_style="flat").intersection(poly), o.polygon[0].y - room.floor_y))
            scene.window_points.append(mid)
            if wall:
                wall.has_window = True
        else:  # door or slider: both need clear floor
            scene.door_zones.append(seg.buffer(DOOR_CLEAR_M, cap_style="flat").intersection(poly))
            scene.door_points.append(mid)
            n = wall.normal if wall else (0.0, 0.0)
            scene.door_entries.append(Point(mid.x + n[0] * (PATH_M / 2 + 0.1), mid.y + n[1] * (PATH_M / 2 + 0.1)))
    for a, b in _passages(room, outline, twin_walls or []):
        seg, mid = LineString([a, b]), Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if any(mid.distance(d) < 0.6 for d in scene.door_points):
            continue  # a real door is already there
        wall = min(scene.walls, key=lambda w: LineString([w.a, w.b]).distance(mid), default=None)
        n = wall.normal if wall else (0.0, 0.0)
        scene.door_zones.append(seg.buffer(DOOR_CLEAR_M, cap_style="flat").intersection(poly))
        scene.door_points.append(mid)
        scene.door_entries.append(Point(mid.x + n[0] * (PATH_M / 2 + 0.1), mid.y + n[1] * (PATH_M / 2 + 0.1)))
    scene.linked_doors = _walkable_pairs(scene, [])
    for i, j in scene.linked_doors:
        line = LineString([scene.door_entries[i], scene.door_entries[j]])
        if line.length > 0.5:
            scene.walkways.append(line.buffer(WALKWAY_M / 2, cap_style="flat").intersection(poly))
    scene.lanes = unary_union(scene.walkways).simplify(0.05) if scene.walkways else None
    return scene


def _walkable_pairs(scene: _Scene, solids: list[Polygon]) -> list[tuple[int, int]]:
    """Pairs of doors a person can walk between, given the solid footprints on the floor."""
    free = scene.room.buffer(-PATH_M / 2)
    if solids:
        free = free.difference(unary_union(solids).buffer(PATH_M / 2))
    parts = list(getattr(free, "geoms", [free])) if not free.is_empty else []
    part_of = [next((k for k, part in enumerate(parts) if part.distance(e) < 0.15), None) for e in scene.door_entries]
    n = len(part_of)
    return [(i, j) for i in range(n) for j in range(i + 1, n) if part_of[i] is not None and part_of[i] == part_of[j]]


def violations(
    room: Room, openings: list[Opening], items: list[Item], poses: dict[str, Pose], walls: list[Wall] | None = None
) -> list[str]:
    """Hard rules a layout breaks. Empty means valid. This is the definition of B2."""
    return _violations(_scene(room, openings, walls), items, poses)


def _violations(scene: _Scene, items: list[Item], poses: dict[str, Pose]) -> list[str]:
    out = []
    shapes = {i.item_id: footprint(i, poses[i.item_id]) for i in items}
    for i in items:
        fp = shapes[i.item_id]
        if not scene.room.buffer(0.01).contains(fp):
            out.append(f"{i.label} is not fully inside the room")
        if i.is_rug:
            continue
        if any(fp.intersects(z) and fp.intersection(z).area > 1e-4 for z in scene.door_zones):
            out.append(f"{i.label} blocks a door or a way through")
        if any(i.h > sill + 0.05 and fp.intersects(z) and fp.intersection(z).area > 1e-4 for z, sill in scene.window_zones):
            out.append(f"{i.label} stands in front of a window")
    solid = [i for i in items if not i.is_rug]
    for k, a in enumerate(solid):
        for b in solid[k + 1 :]:
            if shapes[a.item_id].distance(shapes[b.item_id]) < GAP_M:
                out.append(f"{a.label} and {b.label} overlap")
    if set(scene.linked_doors) - set(_walkable_pairs(scene, [shapes[i.item_id] for i in solid])):
        out.append("the walking path between doors is blocked")
    return out


def _hard_energy(scene: _Scene, items: list[Item], shapes: dict[str, Polygon]) -> float:
    """A smooth measure of how badly the hard rules are broken, so the search can climb out."""
    e = 0.0
    for i in items:
        fp = shapes[i.item_id]
        e += fp.difference(scene.room).area * 4
        if i.is_rug:
            continue
        e += sum(fp.intersection(z).area for z in scene.door_zones if fp.intersects(z)) * 2
        e += sum(fp.intersection(z).area for z, sill in scene.window_zones if i.h > sill + 0.05 and fp.intersects(z)) * 2
    solid = [i for i in items if not i.is_rug]
    for k, a in enumerate(solid):
        for b in solid[k + 1 :]:
            gap = shapes[a.item_id].distance(shapes[b.item_id])
            if gap < GAP_M:
                e += (GAP_M - gap) + shapes[a.item_id].intersection(shapes[b.item_id]).area * 4
    if e == 0 and set(scene.linked_doors) - set(_walkable_pairs(scene, [shapes[i.item_id] for i in solid])):
        e += 0.5
    return e


def _angle_between(u: tuple[float, float], v: tuple[float, float]) -> float:
    lu, lv = math.hypot(*u), math.hypot(*v)
    if lu < 1e-9 or lv < 1e-9:
        return 0.0
    return math.degrees(math.acos(max(-1.0, min(1.0, (u[0] * v[0] + u[1] * v[1]) / (lu * lv)))))


def _walls_for(scene: _Scene, target: str) -> list[_Wall]:
    walls = scene.walls
    if target == "longest":
        return [max(walls, key=lambda w: w.length)]
    if target == "window":
        return [w for w in walls if w.has_window] or walls
    if target == "solid":
        return [w for w in walls if not w.has_window] or walls
    return walls


def _cost(scene: _Scene, c: Constraint, by_id: dict[str, Item], poses: dict[str, Pose]) -> float:
    """How far a wish is from being met: 0 = met, 1 = not at all."""
    item, pose = by_id[c.item], poses[c.item]
    here = (pose.x, pose.z)
    if c.kind == "against_wall":
        back = Point(pose.x - pose.front[0] * item.d / 2, pose.z - pose.front[1] * item.d / 2)
        best = min(
            LineString([w.a, w.b]).distance(back) / 0.6 + _angle_between(pose.front, w.normal) / 90 for w in _walls_for(scene, c.target)
        )
        return min(1.0, best)
    if c.kind == "faces":
        if c.target in poses:
            goal = (poses[c.target].x, poses[c.target].z)
        else:
            points = {"window": scene.window_points, "door": scene.door_points}.get(c.target) or [scene.center]
            goal = min(((p.x, p.y) for p in points), key=lambda p: math.dist(p, here))
        return _angle_between(pose.front, (goal[0] - here[0], goal[1] - here[1])) / 180
    if c.kind == "away_from":
        points = scene.door_points if c.target == "door" else scene.window_points
        return 0.0 if not points else 1 - min(1.0, min(p.distance(Point(here)) for p in points) / 2.5)
    if c.target not in poses:
        return 0.0
    other, other_pose = by_id[c.target], poses[c.target]
    there = (other_pose.x, other_pose.z)
    if c.kind == "near":
        reach = (max(item.w, item.d) + max(other.w, other.d)) / 2 + 0.8
        return min(1.0, max(0.0, math.dist(here, there) - reach) / 2)
    if c.kind == "in_front_of":
        off = other.d / 2 + 0.4 + item.d / 2
        goal = (there[0] + other_pose.front[0] * off, there[1] + other_pose.front[1] * off)
        return min(1.0, math.dist(here, goal) / 1.5)
    # beside
    off = other.w / 2 + 0.2 + item.w / 2
    sides = [(there[0] + s * other_pose.right[0] * off, there[1] + s * other_pose.right[1] * off) for s in (-1, 1)]
    return min(1.0, min(math.dist(here, s) for s in sides) / 1.5)


def _in_walkways(scene: _Scene, items: list[Item], shapes: dict[str, Polygon]) -> float:
    """Area (m²) of furniture standing where people walk between the ways into the room."""
    if scene.lanes is None:
        return 0.0
    return sum(shapes[i.item_id].intersection(scene.lanes).area for i in items if not i.is_rug and shapes[i.item_id].intersects(scene.lanes))


def _soft(scene: _Scene, constraints: list[Constraint], by_id: dict[str, Item], poses: dict[str, Pose]) -> float:
    return sum(c.weight * _cost(scene, c, by_id, poses) for c in constraints)


def _on_wall(scene: _Scene, item: Item, wall: _Wall, t: float) -> Pose:
    off = item.d / 2 + WALL_GAP_M
    return Pose(
        wall.a[0] + (wall.b[0] - wall.a[0]) * t + wall.normal[0] * off,
        wall.a[1] + (wall.b[1] - wall.a[1]) * t + wall.normal[1] * off,
        wall.yaw,
    )


def _anneal(
    scene: _Scene, items: list[Item], constraints: list[Constraint], rng: random.Random, pin: tuple[str, _Wall] | None = None
) -> dict[str, Pose]:
    """One annealing run. `pin` = (item_id, wall): that piece may only stand on that wall, which is how
    different runs are made to explore different walls."""
    by_id = {i.item_id: i for i in items}
    yaws = sorted({round(w.yaw) % 360 for w in scene.walls}) or [0]
    minx, minz, maxx, maxz = scene.room.bounds
    wall_wish = {c.item: c.target for c in constraints if c.kind == "against_wall"}
    follows = {c.item: c for c in constraints if c.kind in ("in_front_of", "beside") and c.target in by_id}

    def wall_pose(item: Item) -> Pose:
        if pin and pin[0] == item.item_id:
            walls = [pin[1]]
        else:
            walls = [w for w in _walls_for(scene, wall_wish.get(item.item_id, "any")) if w.length >= item.w] or scene.walls
        wall = rng.choices(walls, weights=[w.length for w in walls])[0]
        margin = min(0.45, item.w / 2 / wall.length)
        return _on_wall(scene, item, wall, rng.uniform(margin, 1 - margin))

    def follow_pose(c: Constraint, poses: dict[str, Pose]) -> Pose:
        item, other, tp = by_id[c.item], by_id[c.target], poses[c.target]
        if c.kind == "in_front_of":
            off = other.d / 2 + 0.4 + item.d / 2
            return Pose(tp.x + tp.front[0] * off, tp.z + tp.front[1] * off, (tp.yaw + 180) % 360)
        off = (other.w / 2 + 0.2 + item.w / 2) * rng.choice((-1, 1))
        return Pose(tp.x + tp.right[0] * off, tp.z + tp.right[1] * off, tp.yaw)

    poses = {i.item_id: wall_pose(i) for i in items}
    for item_id, c in follows.items():
        poses[item_id] = follow_pose(c, poses)

    def energy(p: dict[str, Pose]) -> float:
        shapes = {i.item_id: footprint(i, p[i.item_id]) for i in items}
        return HARD * _hard_energy(scene, items, shapes) + _soft(scene, constraints, by_id, p) + WALKWAY_COST * _in_walkways(scene, items, shapes)

    current = best = energy(poses)
    best_poses = dict(poses)
    steps = STEPS_PER_ITEM * len(items)
    for step in range(steps):
        temp = max(0.01, 1 - step / steps)
        item = rng.choice(items)
        old = poses[item.item_id]
        move = rng.random()
        if move < 0.55:
            new = Pose(old.x + rng.gauss(0, 0.6 * temp + 0.05), old.z + rng.gauss(0, 0.6 * temp + 0.05), old.yaw)
        elif move < 0.75:
            new = wall_pose(item)
        elif move < 0.88:
            new = Pose(old.x, old.z, rng.choice(yaws))
        elif item.item_id in follows:
            new = follow_pose(follows[item.item_id], poses)
        else:
            new = Pose(rng.uniform(minx, maxx), rng.uniform(minz, maxz), rng.choice(yaws))
        poses[item.item_id] = new
        candidate = energy(poses)
        if candidate <= current or rng.random() < math.exp((current - candidate) / (0.3 * temp)):
            current = candidate
            if current < best:
                best, best_poses = current, dict(poses)
        else:
            poses[item.item_id] = old
    return best_poses


def _difference(a: dict[str, Pose], b: dict[str, Pose]) -> float:
    """How different two layouts look: mean distance a piece moved, with a turn counting as 1 m."""
    return sum(math.dist((a[k].x, a[k].z), (b[k].x, b[k].z)) + (1.0 if abs(a[k].yaw - b[k].yaw) % 360 > 30 else 0.0) for k in a) / len(a)


def solve(
    room: Room,
    openings: list[Opening],
    items: list[Item],
    constraints: list[Constraint],
    n: int = 3,
    seed: int = 0,
    walls: list[Wall] | None = None,
) -> SolveResult:
    """Up to n valid, visibly different layouts, best first. `walls` (the twin's wall list) lets
    the solver see open-plan edges and keep them and the walkways between them clear."""
    if not items:
        return SolveResult([], "no items to place")
    by_id = {i.item_id: i for i in items}
    constraints = [c for c in constraints if c.item in by_id]
    walled = {c.item for c in constraints if c.kind == "against_wall"}
    followers = {c.item for c in constraints if c.kind in ("in_front_of", "beside")}
    constraints = constraints + [
        Constraint(kind="against_wall", item=i.item_id, target="any", weight=0.6)
        for i in items
        if i.item_class in WALL_PIECES and i.item_id not in walled and i.item_id not in followers
    ]
    scene = _scene(room, openings, walls)
    labels = {i.item_id: i.label for i in items}
    total_weight = sum(c.weight for c in constraints) or 1.0

    found: list[Variant] = []
    anchor_item = max(items, key=lambda i: i.w * i.d)
    wish = next((c.target for c in constraints if c.kind == "against_wall" and c.item == anchor_item.item_id), None)
    anchor_walls = sorted((w for w in _walls_for(scene, wish) if w.length >= anchor_item.w), key=lambda w: -w.length) if wish else []
    for k in range(RESTARTS):
        # each run sends the biggest piece to a different wall in turn, so the options really differ
        pin = (anchor_item.item_id, anchor_walls[k % len(anchor_walls)]) if anchor_walls else None
        poses = _anneal(scene, items, constraints, random.Random(seed * 1000 + k), pin)
        if _violations(scene, items, poses):
            continue
        costs = [(c, _cost(scene, c, by_id, poses)) for c in constraints]
        blocked = _in_walkways(scene, items, {i.item_id: footprint(i, poses[i.item_id]) for i in items})
        found.append(
            Variant(
                poses=poses,
                score=round(max(0.0, 100 * (1 - sum(c.weight * cost for c, cost in costs) / total_weight) - 25 * blocked), 1),
                met=[c.describe(labels) for c, cost in costs if cost <= 0.15],
                unmet=[c.describe(labels) for c, cost in costs if cost > 0.15]
                + (["part of a piece stands in a walkway"] if blocked > 0.05 else []),
            )
        )

    found.sort(key=lambda v: -v.score)
    found = [v for v in found if v.score >= found[0].score - 25] if found else []  # weak options are not worth showing
    if wish:  # the biggest piece was asked onto a wall: keep only options where it really is, if any are
        on_wall = [v for v in found if not any(u.startswith(f"{anchor_item.label} against a wall") for u in v.unmet)]
        found = on_wall or found
    anchor = max(items, key=lambda i: i.w * i.d).item_id  # the biggest piece defines the arrangement
    chosen: list[Variant] = []
    # First pass: options that put the biggest piece on a different wall. Second: any clearly different one.
    for different_wall in (True, False):
        for v in found:
            if len(chosen) == n:
                break
            if v in chosen or any(_difference(v.poses, c.poses) < 0.6 for c in chosen):
                continue
            if different_wall and any(abs(v.poses[anchor].yaw - c.poses[anchor].yaw) % 360 < 30 for c in chosen):
                continue
            chosen.append(v)
    chosen.sort(key=lambda v: -v.score)

    if not found:
        area = sum(i.w * i.d for i in items if not i.is_rug)
        return SolveResult([], f"no arrangement fits: the pieces cover {area:.1f} m² of a {scene.room.area:.1f} m² room once doors, windows and a walking path are kept clear")
    reason = None if len(chosen) == n else f"only {len(chosen)} clearly different arrangement{'s' if len(chosen) != 1 else ''} fit this room"
    return SolveResult(chosen, reason)


def nearest_valid_pose(
    room: Room,
    openings: list[Opening],
    items: list[Item],
    poses: dict[str, Pose],
    item_id: str,
    x: float,
    z: float,
    walls: list[Wall] | None = None,
    reach_m: float = 2.0,
) -> Pose | None:
    """Where a piece dropped at (x, z) can actually stand: the closest spot within reach_m that
    breaks no hard rule, trying each wall direction. Near a wall it backs up against it.
    `poses` holds the other pieces already in the room. None if nothing nearby works."""
    scene = _scene(room, openings, walls)
    item = next(i for i in items if i.item_id == item_id)
    yaws = sorted({round(w.yaw) % 360 for w in scene.walls}) or [0.0]
    here = Point(x, z)
    near = sorted(scene.walls, key=lambda w: LineString([w.a, w.b]).distance(here))
    candidates: list[Pose] = []
    if near and LineString([near[0].a, near[0].b]).distance(here) < 0.9:  # dropped by a wall: back against it
        wall = near[0]
        along = LineString([wall.a, wall.b])
        t = along.project(here) / along.length
        candidates.append(_on_wall(scene, item, wall, min(max(t, 0.0), 1.0)))
        yaws = [wall.yaw] + [y for y in yaws if abs(y - wall.yaw) > 1]
    steps = int(reach_m / 0.15) + 1
    for ring in range(steps):
        radius = ring * 0.15
        for k in range(max(1, ring * 6)):
            angle = 2 * math.pi * k / max(1, ring * 6)
            for yaw in yaws:
                candidates.append(Pose(x + radius * math.cos(angle), z + radius * math.sin(angle), float(yaw)))
    for pose in candidates:
        if not _violations(scene, items, {**poses, item_id: pose}):
            return pose
    return None
