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

import logging
import math
import os
import random
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel
from shapely.geometry import LineString, MultiLineString, Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

from packages.schema.twin import Opening, Room, Sweep, Wall

PATH_M = 0.9  # width a person needs to walk
DOOR_CLEAR_M = 0.9  # clear floor in front of a door
WINDOW_CLEAR_M = 0.3
GAP_M = 0.05  # minimum gap between two pieces
WALL_GAP_M = 0.03  # between a piece and the wall face (room for a baseboard)
DEFAULT_WALL_M = 0.14  # wall thickness assumed when the twin has no wall list
RESTARTS = 14
STEPS_PER_ITEM = 260
WORKERS = 7  # restarts run side by side in this many processes (two rounds of 7)
HARD = 1000.0  # energy per unit of hard violation: always dominates the soft terms
PASSAGE_MIN_M = 0.8  # a gap this wide in a room's walls is a way through, like a door with no door in it
WALKWAY_M = 1.0  # width kept free along the straight line between two ways in
WALKWAY_COST = 8.0  # soft cost per m² of furniture standing in a walkway
GLASS_TO_FLOOR_M = 0.3  # a "window" whose sill is lower than this is a glass door: people walk through it
FLOAT_COST = 2.0  # soft cost for a piece (or group) standing in the open, away from every wall
GROUP_GAP_M = 0.8  # pieces this close belong to one group
BACK_GAP_M = 0.2  # a piece is "on" a wall when its whole back is within this of a solid stretch of it
LOW_PIECE_M = 0.6  # pieces lower than this may stand under a window
# Pieces that look wrong away from a wall. They get a mild against_wall wish if the designer gave none.
WALL_PIECES = {"sofa", "loveseat", "sectional", "bed", "crib", "bookshelf", "cabinet", "dresser", "wardrobe", "tv_stand", "console_table", "nightstand", "desk"}


class Constraint(BaseModel):
    """One wish from the designer. `item` and item-valued `target`s are item_ids."""

    # The last three are not the solver's: they say which piece a lamp stands on, a picture hangs
    # above or a rug lies under, and are carried out by furniture/dress.py once the floor is arranged.
    kind: Literal["against_wall", "faces", "near", "in_front_of", "beside", "away_from", "on", "above", "under"]
    item: str
    # against_wall: "any" | "longest" | "window" (a wall with a window) | "solid" (a wall without)
    #   | "north" | "east" | "south" | "west" (the wall on that side of the room; needs solve(compass=...))
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
            "on": f"{name(self.item)} on {name(self.target)}",
            "above": f"{name(self.item)} on the wall above {name(self.target)}",
            "under": f"{name(self.item)} under {name(self.target)}",
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
    line: LineString = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.line = LineString([self.a, self.b])

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
    routes: object | None = None  # centerlines of the walking routes the scan itself followed, if known
    # solid stretches of wall: (wall, stretch) with no door, glass or window in them; and the same
    # with only doors and floor-level glass removed, for pieces low enough to stand under a window
    solid: list = field(default_factory=list)
    solid_low: list = field(default_factory=list)
    solid_zone: object | None = None
    solid_low_zone: object | None = None
    strict: bool = False  # True: no piece may stand on a route centerline
    compass: dict[str, tuple[float, float]] = field(default_factory=dict)  # "north" -> unit (x, z) in the interior frame
    # The same shapes made quick to test against (shapely's prepared geometry), once the scene is
    # built: the search tests every footprint against them thousands of times.
    fast: dict = field(default_factory=dict)

    # Sent to the worker processes without the prepared shapes, which cannot be pickled.
    def __getstate__(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "fast"}

    def __setstate__(self, state: dict) -> None:
        self.__dict__.update(state)
        _prepare(self)


def _prepare(scene: _Scene) -> None:
    some = lambda g: prep(g) if g is not None and not g.is_empty else None  # noqa: E731
    scene.fast = {
        "room": prep(scene.room),
        "doors": [some(z) for z in scene.door_zones],
        "windows": [some(z) for z, _ in scene.window_zones],
        "solid": some(scene.solid_zone),
        "solid_low": some(scene.solid_low_zone),
        "lanes": some(scene.lanes),
        "routes": some(scene.routes),
        "walls": MultiLineString([w.line for w in scene.walls]) if scene.walls else None,
    }


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


def _scene(room: Room, openings: list[Opening], twin_walls: list[Wall] | None = None, sweeps: list[Sweep] | None = None) -> _Scene:
    poly = Polygon(room.polygon)
    if not poly.is_valid:
        poly = poly.buffer(0)
    # Room outlines run along the middle of the walls (adjoining rooms share an edge), so the wall
    # face a piece can touch is half a wall inside the outline. Work on that inner floor.
    thickness = [w.thickness_m for w in (twin_walls or []) if room.room_id in w.room_ids]
    inset = (max(thickness) if thickness else DEFAULT_WALL_M) / 2
    inner = poly.buffer(-inset, join_style="mitre")
    if not inner.is_empty:
        poly = max(getattr(inner, "geoms", [inner]), key=lambda g: g.area)
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
    walk_through: list[LineString] = []  # doors and glass down to the floor
    see_through: list[LineString] = []  # windows with a sill
    for o in openings:
        if room.room_id not in (o.room_id, o.other_room_id):
            continue
        a, b = (o.polygon[0].x, o.polygon[0].z), (o.polygon[1].x, o.polygon[1].z)
        seg, mid = LineString([a, b]), Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        wall = min(walls, key=lambda w: LineString([w.a, w.b]).distance(mid), default=None)
        sill = o.polygon[0].y - room.floor_y
        if o.kind == "window":
            scene.window_points.append(mid)  # something to face, whatever its height
            if wall:
                wall.has_window = True
        if o.kind == "window" and sill >= GLASS_TO_FLOOR_M:
            see_through.append(seg)
            scene.window_zones.append((seg.buffer(WINDOW_CLEAR_M, cap_style="flat").intersection(poly), sill))
        else:  # a door, a slider, or glass down to the floor: all need clear floor in front
            walk_through.append(seg)
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
    routes = _scan_routes(room, poly, sweeps or [], scene)
    passages = [p.buffer(0.6) for p in scene.door_points[len(walk_through):]]  # ways in with no door segment

    def stretches(gaps: list) -> list:
        out = []
        cut = unary_union([g.buffer(0.2) if g.geom_type == "LineString" else g for g in gaps]) if gaps else None
        for w in walls:
            line = LineString([w.a, w.b])
            rest = line.difference(cut) if cut is not None else line
            out += [(w, part) for part in getattr(rest, "geoms", [rest]) if not part.is_empty and part.length >= 0.4]
        return out

    scene.solid = stretches(walk_through + see_through + passages)
    scene.solid_low = stretches(walk_through + passages)
    scene.solid_zone = unary_union([part for _, part in scene.solid]).buffer(BACK_GAP_M) if scene.solid else None
    scene.solid_low_zone = unary_union([part for _, part in scene.solid_low]).buffer(BACK_GAP_M) if scene.solid_low else None
    scene.linked_doors = _walkable_pairs(scene, [])
    if routes:
        scene.routes = unary_union(routes).intersection(poly.buffer(-0.05))
        scene.walkways = [line.buffer(WALKWAY_M / 2, cap_style="flat").intersection(poly) for line in routes]
    else:  # no scan data: assume people walk straight between the ways in
        for i, j in scene.linked_doors:
            line = LineString([scene.door_entries[i], scene.door_entries[j]])
            if line.length > 0.5:
                scene.walkways.append(line.buffer(WALKWAY_M / 2, cap_style="flat").intersection(poly))
    scene.lanes = unary_union(scene.walkways).simplify(0.05) if scene.walkways else None
    _prepare(scene)
    return scene


def _scan_routes(room: Room, poly: Polygon, sweeps: list[Sweep], scene: _Scene) -> list[LineString]:
    """Where people actually walk in this room, from the scan.

    Sweeps are the spots the scanner stood on; `neighbors` joins two sweeps with a clear straight
    line between them. A neighbor line that leaves the room marks a way in: a door, or an open-plan
    passage the door data doesn't have (those are added to the scene as ways in). The routes are
    the shortest paths along the in-room sweeps that connect every way in, as a tree, so the room
    keeps one skeleton of walkways instead of a web. Empty when the room has no usable scan data.
    """
    by_id = {s.sweep_id: s for s in sweeps}
    inside = [s for s in sweeps if s.room_id == room.room_id]
    if not inside:
        return []
    at = lambda s: (s.position.x, s.position.z)  # noqa: E731
    graph: dict[str, list[tuple[str, float]]] = {s.sweep_id: [] for s in inside}
    crossings: list[tuple[Point, str]] = []  # (where a neighbor line leaves the room, the sweep inside)
    for s in inside:
        for other_id in s.neighbors:
            o = by_id.get(other_id)
            if o is None or o.floor_id != s.floor_id:
                continue
            line = LineString([at(s), at(o)])
            if o.room_id == room.room_id:
                graph[s.sweep_id].append((other_id, line.length))
                continue
            hit = line.intersection(poly.exterior)
            if not hit.is_empty:
                crossings.append((min(getattr(hit, "geoms", [hit]), key=lambda g: g.distance(Point(at(s)))).representative_point(), s.sweep_id))

    # ways in the door data already has: attach each to the sweep whose line passes through it, else the nearest
    terminals: list[tuple[Point, str]] = []
    for door in scene.door_points:
        near = [c for c in crossings if c[0].distance(door) < 0.9]
        sweep_id = near[0][1] if near else min(inside, key=lambda s: door.distance(Point(at(s)))).sweep_id
        terminals.append((door, sweep_id))
    # ways in it doesn't have: open passages, one per cluster of crossings
    for point, sweep_id in crossings:
        if any(point.distance(t[0]) < 1.0 for t in terminals):
            continue
        inward = Point(at(by_id[sweep_id]))
        reach = max(point.distance(inward), 1e-6)
        step = min(PATH_M / 2 + 0.1, reach)
        scene.door_zones.append(point.buffer(DOOR_CLEAR_M * 0.75).intersection(poly))
        scene.door_points.append(point)
        scene.door_entries.append(Point(point.x + (inward.x - point.x) * step / reach, point.y + (inward.y - point.y) * step / reach))
        terminals.append((point, sweep_id))
    if len(terminals) < 2:
        return []

    def paths_from(start: str) -> tuple[dict[str, float], dict[str, str]]:
        dist, prev, todo = {start: 0.0}, {}, [start]
        while todo:
            node = min(todo, key=dist.get)
            todo.remove(node)
            for nxt, length in graph[node]:
                if dist[node] + length < dist.get(nxt, math.inf):
                    dist[nxt], prev[nxt] = dist[node] + length, node
                    if nxt not in todo:
                        todo.append(nxt)
        return dist, prev

    def route(i: int, j: int) -> tuple[float, LineString] | None:
        (pi, si), (pj, sj) = terminals[i], terminals[j]
        direct = LineString([(pi.x, pi.y), (pj.x, pj.y)])
        dist, prev = paths_from(si)
        if sj not in dist:
            return (direct.length, direct) if poly.buffer(0.05).contains(direct) else None
        chain = [sj]
        while chain[-1] != si:
            chain.append(prev[chain[-1]])
        via = LineString([(pi.x, pi.y), *(at(by_id[k]) for k in reversed(chain)), (pj.x, pj.y)])
        if poly.buffer(0.05).contains(direct) and direct.length < via.length:
            return direct.length, direct  # a clear straight line beats the detour past a scan point
        return via.length, via

    # a tree over the ways in (Prim): the shortest set of routes that still reaches all of them
    reached, lines = {0}, []
    while len(reached) < len(terminals):
        options = [(r[0], r[1], j) for i in reached for j in range(len(terminals)) if j not in reached and (r := route(i, j))]
        if not options:
            break
        _length, line, j = min(options, key=lambda o: o[0])
        reached.add(j)
        if line.length > 0.5:
            lines.append(line)
    return lines


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
    room: Room,
    openings: list[Opening],
    items: list[Item],
    poses: dict[str, Pose],
    walls: list[Wall] | None = None,
    sweeps: list[Sweep] | None = None,
) -> list[str]:
    """Hard rules a layout breaks. Empty means valid. This is the definition of B2.

    Standing on a walking route is not one of them: the solver avoids it when it can, but a user
    who drags a piece there is allowed to."""
    return _violations(_scene(room, openings, walls, sweeps), items, poses)


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
        if scene.strict and scene.routes is not None and fp.intersects(scene.routes):
            out.append(f"{i.label} stands on a walking route")
        if scene.strict and i.item_class in WALL_PIECES and _off_wall(scene, i, fp) > 0:
            out.append(f"{i.label} is not backed against a solid wall")
    solid = [i for i in items if not i.is_rug]
    for k, a in enumerate(solid):
        for b in solid[k + 1 :]:
            if shapes[a.item_id].distance(shapes[b.item_id]) < GAP_M:
                out.append(f"{a.label} and {b.label} overlap")
    if set(scene.linked_doors) - set(_walkable_pairs(scene, [shapes[i.item_id] for i in solid])):
        out.append("the walking path between doors is blocked")
    return out


def _hard_energy(scene: _Scene, items: list[Item], shapes: dict[str, Polygon], off_cache: dict[str, float] | None = None) -> float:
    """A smooth measure of how badly the hard rules are broken, so the search can climb out."""
    fast = scene.fast
    e = 0.0
    for i in items:
        fp = shapes[i.item_id]
        if not fast["room"].contains(fp):
            e += fp.difference(scene.room).area * 4
        if i.is_rug:
            continue
        e += sum(fp.intersection(z).area for z, zp in zip(scene.door_zones, fast["doors"]) if zp is not None and zp.intersects(fp)) * 2
        e += sum(
            fp.intersection(z).area for (z, sill), zp in zip(scene.window_zones, fast["windows"]) if i.h > sill + 0.05 and zp is not None and zp.intersects(fp)
        ) * 2
        if scene.strict and fast["routes"] is not None and fast["routes"].intersects(fp):
            e += 0.05 + fp.intersection(scene.routes).length
        if scene.strict and i.item_class in WALL_PIECES and scene.solid_zone is not None:
            off = _off_wall(scene, i, fp, off_cache)
            if off > 0:
                e += 0.2 * off + 0.1 * min(2.0, scene.solid_zone.distance(fp))
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
    if target in scene.compass and walls:
        # The north wall is the one whose far side looks north: its normal points into the room.
        away = lambda w: _angle_between((-w.normal[0], -w.normal[1]), scene.compass[target])  # noqa: E731
        return [w for w in walls if away(w) <= 45] or [min(walls, key=away)]
    return walls


def _cost(scene: _Scene, c: Constraint, by_id: dict[str, Item], poses: dict[str, Pose]) -> float:
    """How far a wish is from being met: 0 = met, 1 = not at all."""
    item, pose = by_id[c.item], poses[c.item]
    here = (pose.x, pose.z)
    if c.kind == "against_wall":
        back = Point(pose.x - pose.front[0] * item.d / 2, pose.z - pose.front[1] * item.d / 2)
        best = min(w.line.distance(back) / 0.6 + _angle_between(pose.front, w.normal) / 90 for w in _walls_for(scene, c.target))
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
    lanes = scene.fast["lanes"]
    if lanes is None:
        return 0.0
    return sum(shapes[i.item_id].intersection(scene.lanes).area for i in items if not i.is_rug and lanes.intersects(shapes[i.item_id]))


def _off_wall(scene: _Scene, item: Item, shape: Polygon, cache: dict[str, float] | None = None) -> float:
    """0 when the piece's whole back runs along a solid stretch of wall, up to 1 when none of it does.
    A window, a door or a glass door behind the piece doesn't count as wall (low pieces may stand
    under a window). `cache`: answers already worked out for these same footprints."""
    if cache is not None and item.item_id in cache:
        return cache[item.item_id]
    low = item.h < LOW_PIECE_M
    zone = scene.solid_low_zone if low else scene.solid_zone
    if zone is None:
        off = 1.0
    else:
        back = LineString(list(shape.exterior.coords)[:2])  # footprint() lists the two back corners first
        if scene.fast[("solid_low" if low else "solid")].contains(back):
            off = 0.0
        else:
            covered = back.intersection(zone).length / back.length
            off = 0.0 if covered >= 0.9 else 1.0 - covered
    if cache is not None:
        cache[item.item_id] = off
    return off


def _floating(scene: _Scene, items: list[Item], shapes: dict[str, Polygon], off_cache: dict[str, float] | None = None) -> dict[str, float]:
    """item_id -> how much a piece stands in the open (0 = anchored, 1 = adrift).

    A piece is anchored when it touches a wall, or belongs to a group (pieces within GROUP_GAP_M of
    each other) in which some piece does. An unanchored group is charged by how far it is from the
    nearest wall, so the search pulls the whole group toward one."""
    solid = [i for i in items if not i.is_rug]
    if not solid or not scene.walls:
        return {}
    walls = scene.fast["walls"]
    wall_gap = {i.item_id: walls.distance(shapes[i.item_id]) for i in solid}
    group = {i.item_id: i.item_id for i in solid}  # union-find by proximity

    def root(k: str) -> str:
        while group[k] != k:
            k = group[k]
        return k

    for n, a in enumerate(solid):
        for b in solid[n + 1 :]:
            if shapes[a.item_id].distance(shapes[b.item_id]) <= GROUP_GAP_M:
                group[root(a.item_id)] = root(b.item_id)
    nearest: dict[str, float] = {}
    for i in solid:
        r = root(i.item_id)
        nearest[r] = min(nearest.get(r, math.inf), wall_gap[i.item_id])
    # a group is anchored by a wall piece whose back is on solid wall, or any other piece touching a
    # wall. A dining table is anchored where it stands: it belongs in the open, with room to walk round it.
    anchored = {
        root(i.item_id)
        for i in solid
        if i.item_class == "dining_table"
        or (_off_wall(scene, i, shapes[i.item_id], off_cache) == 0.0 if i.item_class in WALL_PIECES else wall_gap[i.item_id] <= 0.15)
    }
    return {i.item_id: 0.0 if root(i.item_id) in anchored else max(0.3, min(1.0, nearest[root(i.item_id)] / 1.2)) for i in solid}


def _soft(scene: _Scene, constraints: list[Constraint], by_id: dict[str, Item], poses: dict[str, Pose]) -> float:
    return sum(c.weight * _cost(scene, c, by_id, poses) for c in constraints)


def _stretches_for(scene: _Scene, item: Item, target: str | None) -> list:
    """Solid stretches of wall this piece's back fits on, the ones the wish asks for first."""
    pool = scene.solid_low if item.h < LOW_PIECE_M else scene.solid
    fits = [(w, part) for w, part in pool if part.length >= item.w]
    wanted = {id(w) for w in _walls_for(scene, target or "any")}
    first = [x for x in fits if id(x[0]) in wanted]
    return sorted(first, key=lambda x: -x[1].length) + sorted((x for x in fits if id(x[0]) not in wanted), key=lambda x: -x[1].length)


def _on_wall(scene: _Scene, item: Item, wall: _Wall, t: float) -> Pose:
    off = item.d / 2 + WALL_GAP_M
    return Pose(
        wall.a[0] + (wall.b[0] - wall.a[0]) * t + wall.normal[0] * off,
        wall.a[1] + (wall.b[1] - wall.a[1]) * t + wall.normal[1] * off,
        wall.yaw,
    )


def _anneal(
    scene: _Scene, items: list[Item], constraints: list[Constraint], rng: random.Random, pin: tuple | None = None
) -> dict[str, Pose]:
    """One annealing run. `pin` = (item_id, (wall, stretch)): that piece may only stand on that
    stretch of wall, which is how different runs are made to explore different walls."""
    by_id = {i.item_id: i for i in items}
    yaws = sorted({round(w.yaw) % 360 for w in scene.walls}) or [0]
    minx, minz, maxx, maxz = scene.room.bounds
    wall_wish = {c.item: c.target for c in constraints if c.kind == "against_wall"}
    follows = {c.item: c for c in constraints if c.kind in ("in_front_of", "beside") and c.target in by_id}

    def wall_pose(item: Item) -> Pose:
        if pin and pin[0] == item.item_id:
            options = [pin[1]]
        else:
            options = _stretches_for(scene, item, wall_wish.get(item.item_id, "any"))
        if not options:  # no solid stretch is long enough: anywhere along a wall
            walls = [w for w in scene.walls if w.length >= item.w] or scene.walls
            wall = rng.choices(walls, weights=[w.length for w in walls])[0]
            margin = min(0.45, item.w / 2 / wall.length)
            return _on_wall(scene, item, wall, rng.uniform(margin, 1 - margin))
        wall, part = rng.choices(options, weights=[part.length for _, part in options])[0]
        slack = max(0.0, part.length - item.w)
        spot = part.interpolate(item.w / 2 + rng.uniform(0, slack))  # the piece's whole back fits on the stretch
        off = item.d / 2 + WALL_GAP_M
        return Pose(spot.x + wall.normal[0] * off, spot.y + wall.normal[1] * off, wall.yaw)

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
        off: dict[str, float] = {}
        return (
            HARD * _hard_energy(scene, items, shapes, off)
            + _soft(scene, constraints, by_id, p)
            + WALKWAY_COST * _in_walkways(scene, items, shapes)
            + FLOAT_COST * sum(_floating(scene, items, shapes, off).values())
        )

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


_pool: ProcessPoolExecutor | None = None
_pool_broken = False


def _anneal_job(scene: _Scene, items: list[Item], constraints: list[Constraint], seed: int, pin: tuple | None) -> dict[str, Pose]:
    return _anneal(scene, items, constraints, random.Random(seed), pin)


def _anneal_all(scene: _Scene, items: list[Item], constraints: list[Constraint], seeds: list[int], pins: list) -> list[dict[str, Pose]]:
    """Every restart's result, in order. Side by side in worker processes when the machine has the
    cores, one after another otherwise; the same results either way, since each run has its own seed."""
    global _pool, _pool_broken
    jobs = [(scene, items, constraints, s, p) for s, p in zip(seeds, pins)]
    if len(jobs) > 1 and not _pool_broken and (os.cpu_count() or 1) > 2:
        try:
            if _pool is None:
                _pool = ProcessPoolExecutor(max_workers=min(WORKERS, (os.cpu_count() or 2) - 1))
            return list(_pool.map(_anneal_job, *zip(*jobs)))
        except Exception:
            logging.getLogger(__name__).exception("layout solver: worker processes failed, solving in this process")
            _pool_broken, _pool = True, None
    return [_anneal_job(*job) for job in jobs]


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
    sweeps: list[Sweep] | None = None,
    compass: dict[str, tuple[float, float]] | None = None,
) -> SolveResult:
    """Up to n valid, visibly different layouts, best first. `walls` (the twin's wall list) lets
    the solver see open-plan edges and keep them and the walkways between them clear. `compass`
    ("north" -> unit (x, z) in the interior frame) lets wishes name a wall by its side."""
    if not items:
        return SolveResult([], "no items to place")
    by_id = {i.item_id: i for i in items}
    constraints = [c for c in constraints if c.item in by_id and c.kind not in ("on", "above", "under")]
    walled = {c.item for c in constraints if c.kind == "against_wall"}
    followers = {c.item for c in constraints if c.kind in ("in_front_of", "beside")}
    constraints = constraints + [
        Constraint(kind="against_wall", item=i.item_id, target="any", weight=1.0)
        for i in items
        if i.item_class in WALL_PIECES and i.item_id not in walled and i.item_id not in followers
    ]
    scene = _scene(room, openings, walls, sweeps)
    scene.compass = compass or {}
    scene.strict = True  # wall pieces on solid wall, nothing on a walking route; relaxed below if nothing fits
    labels = {i.item_id: i.label for i in items}
    total_weight = sum(c.weight for c in constraints) or 1.0

    anchor_item = max(items, key=lambda i: i.w * i.d)
    wish = next((c.target for c in constraints if c.kind == "against_wall" and c.item == anchor_item.item_id), None)
    anchor_walls = _stretches_for(scene, anchor_item, wish) if wish else []  # the wished kind of wall first, then the rest

    def search() -> list[Variant]:
        found: list[Variant] = []
        # each run sends the biggest piece to a different wall in turn, so the options really differ
        pins = [(anchor_item.item_id, anchor_walls[k % len(anchor_walls)]) if anchor_walls else None for k in range(RESTARTS)]
        for poses in _anneal_all(scene, items, constraints, [seed * 1000 + k for k in range(RESTARTS)], pins):
            if _violations(scene, items, poses):
                continue
            costs = [(c, _cost(scene, c, by_id, poses)) for c in constraints]
            shapes = {i.item_id: footprint(i, poses[i.item_id]) for i in items}
            blocked = _in_walkways(scene, items, shapes)
            adrift = {k: v for k, v in _floating(scene, items, shapes).items() if v > 0.1}
            found.append(
                Variant(
                    poses=poses,
                    score=round(max(0.0, 100 * (1 - sum(c.weight * cost for c, cost in costs) / total_weight) - 25 * blocked - 15 * sum(adrift.values())), 1),
                    met=[c.describe(labels) for c, cost in costs if cost <= 0.15],
                    unmet=[c.describe(labels) for c, cost in costs if cost > 0.15]
                    + (["part of a piece stands in a walkway"] if blocked > 0.05 else [])
                    + [f"{labels[k]} stands in the open, away from the walls" for k in adrift],
                )
            )
        return found

    found = search()
    relaxed = False
    if not found and scene.strict:  # nothing fits under the strict rules: relax them, and say so
        scene.strict, relaxed = False, True
        found = search()
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
    if relaxed:
        note = "the room is too tight to put every large piece on a solid wall clear of the walking routes, so one rule was relaxed"
        reason = f"{reason}; {note}" if reason else note
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
    sweeps: list[Sweep] | None = None,
    yaw: float | None = None,
) -> Pose | None:
    """Where a piece dropped at (x, z) can actually stand: the closest spot within reach_m that
    breaks no hard rule, trying each wall direction. Near a wall it backs up against it.
    `poses` holds the other pieces already in the room. None if nothing nearby works.
    `yaw`: the way it should face (a chair toward its desk). That is tried first at every spot,
    and the piece is then not turned to back against a wall."""
    scene = _scene(room, openings, walls, sweeps)
    item = next(i for i in items if i.item_id == item_id)
    yaws = sorted({round(w.yaw) % 360 for w in scene.walls}) or [0.0]
    if yaw is not None:
        yaws = [yaw % 360] + [y for y in yaws if abs(y - yaw % 360) > 1]
    here = Point(x, z)
    near = sorted(scene.walls, key=lambda w: LineString([w.a, w.b]).distance(here))
    candidates: list[Pose] = []
    if yaw is None and near and LineString([near[0].a, near[0].b]).distance(here) < 0.9:  # dropped by a wall: back against it
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
    # Pieces the user already placed by hand may break a rule. The dropped piece must not add to that.
    others = [i for i in items if i.item_id != item_id]
    already = set(_violations(scene, others, poses))
    for pose in candidates:
        if set(_violations(scene, items, {**poses, item_id: pose})) <= already:
            return pose
    return None
