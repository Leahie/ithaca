"""Where to stand and what to look at: the best view of a room, and a walking route between rooms.

Pure geometry on the PropertyTwin (rooms, doors, sweeps). The agent's UI tools call these and send
the result to the viewer; the LLM never sees or produces a coordinate (B1).

A sweep is one of the scan's camera positions. The viewer can only stand on sweeps, so a view is
(sweep, point to look at) and a walk is a list of sweeps.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from functools import lru_cache

from shapely.geometry import LineString, Point, Polygon

from packages.schema.twin import PropertyTwin, Room, Sweep, Vec3

STEP_M = 3.6  # two sweeps this close can be one step of a walk
DOOR_REACH_M = 0.7  # a step between rooms counts as "through the door" if it passes this close to one
UNMAPPED_COST = 3.0  # a step between adjoining rooms with no recorded door costs this much more
STAIRS_COST = 1.5
STAIRS_RISE_M = 1.2  # a route that climbs or drops more than this goes by stairs
MAX_RISE_M = 1.0  # the most one step may climb; more than that is a different storey, not a stair
WALK_SPEED_M_S = 1.2


@dataclass(frozen=True)
class View:
    sweep_id: str
    look_at: Vec3


@dataclass(frozen=True)
class Route:
    sweep_ids: list[str]
    room_ids: list[str]  # rooms passed, in order, without repeats
    distance_m: float
    floor_changes: int
    unmapped_steps: int  # steps between rooms where the scan recorded no door
    look_at: Vec3  # what to face on arrival

    @property
    def seconds(self) -> float:
        return self.distance_m / WALK_SPEED_M_S


def floor_height(twin: PropertyTwin, room_id: str) -> float:
    """The height of a room's floor, for standing furniture on it.

    Room.floor_y is too low in some rooms (by 0.3-0.4 m in this house's large Floor 1 rooms). The
    scan camera stands on the floor at a fixed tripod height, so the sweeps give a second reading:
    median sweep height in the room, minus the tripod height seen in the rest of the house. The
    higher of the two is used. Rooms without a sweep keep Room.floor_y."""
    room = _room(twin, room_id)
    heights = {}
    for r in twin.rooms:
        ys = sorted(s.position.y for s in twin.sweeps if s.room_id == r.room_id)
        if ys:
            heights[r.room_id] = ys[len(ys) // 2] - r.floor_y
    if room_id not in heights or len(heights) < 4:
        return room.floor_y
    tripod = sorted(heights.values())[len(heights) // 4]  # lower quartile: rooms whose floor_y is right
    return round(max(room.floor_y, room.floor_y + heights[room_id] - tripod), 3)


def _xz(s: Sweep) -> tuple[float, float]:
    return (s.position.x, s.position.z)


def _room(twin: PropertyTwin, room_id: str) -> Room:
    return next(r for r in twin.rooms if r.room_id == room_id)


def _focus(twin: PropertyTwin, room: Room) -> tuple[float, float]:
    """The point a visitor should face: the middle of the room, pulled toward its windows."""
    poly = Polygon(room.polygon)
    cx, cz = poly.representative_point().coords[0] if not poly.contains(poly.centroid) else poly.centroid.coords[0]
    windows = [o for o in twin.openings if o.room_id == room.room_id and o.kind != "door" and o.exterior is not False]
    if not windows:
        return cx, cz
    wx = sum((o.polygon[0].x + o.polygon[1].x) / 2 for o in windows) / len(windows)
    wz = sum((o.polygon[0].z + o.polygon[1].z) / 2 for o in windows) / len(windows)
    pulled = (cx + (wx - cx) * 0.35, cz + (wz - cz) * 0.35)
    return pulled if poly.contains(Point(pulled)) else (cx, cz)  # an L-shaped room: stay inside it


def best_view(twin: PropertyTwin, room_id: str) -> View | None:
    """Stand at the edge of the room, facing its middle and windows. None if the room has no sweep.

    Picks the sweep in the room that is farthest from the focus point while still having a clear
    line to it inside the room, so the view takes in as much of the room as possible."""
    room = _room(twin, room_id)
    sweeps = [s for s in twin.sweeps if s.room_id == room_id]
    if not sweeps:
        return None
    # a room outline can take in a staircase: never stand on a step
    level = sorted(s.position.y for s in sweeps)[len(sweeps) // 2]
    sweeps = [s for s in sweeps if abs(s.position.y - level) < 0.4]
    poly = Polygon(room.polygon).buffer(0.15)
    fx, fz = _focus(twin, room)

    def score(s: Sweep) -> float:
        clear = poly.contains(LineString([_xz(s), (fx, fz)]))
        return math.dist(_xz(s), (fx, fz)) + (100 if clear else 0)

    sweep = max(sweeps, key=score)
    if math.dist(_xz(sweep), (fx, fz)) < 0.6:  # standing on the focus: face the far end of the room instead
        fx, fz = max(room.polygon, key=lambda p: math.dist(p, _xz(sweep)))
    return View(sweep.sweep_id, Vec3(x=fx, y=sweep.position.y - 0.25, z=fz))


@lru_cache(maxsize=8)
def _graph(property_id: str, version: int, twin_json: str) -> dict[str, list[tuple[str, float, float, bool]]]:
    """sweep_id -> [(neighbor, cost, meters, unmapped)]. Cached per twin version."""
    twin = PropertyTwin.model_validate_json(twin_json)
    sweeps = [s for s in twin.sweeps if s.room_id is not None]
    if any(s.neighbors for s in sweeps):
        # The scan's own record of which sweeps have a clear straight line between them. Nothing to
        # guess: a step along one of these never crosses a wall or a floor.
        inside = {s.sweep_id: s for s in sweeps}
        known: dict[str, list[tuple[str, float, float, bool]]] = {s.sweep_id: [] for s in sweeps}
        for a in sweeps:
            for other_id in a.neighbors:
                b = inside.get(other_id)
                if b is not None:
                    meters = math.dist((a.position.x, a.position.y, a.position.z), (b.position.x, b.position.y, b.position.z))
                    known[a.sweep_id].append((other_id, meters, meters, False))
        return known
    # No neighbor data (older twins): infer the steps from distance, room outlines and doors.
    polys = {r.room_id: Polygon(r.polygon) for r in twin.rooms}
    floor_of = {r.room_id: r.floor_id for r in twin.rooms}
    doors: dict[frozenset[str], list[Point]] = {}
    for o in twin.openings:
        if o.kind == "door" and o.other_room_id:
            mid = Point((o.polygon[0].x + o.polygon[1].x) / 2, (o.polygon[0].z + o.polygon[1].z) / 2)
            doors.setdefault(frozenset((o.room_id, o.other_room_id)), []).append(mid)

    graph: dict[str, list[tuple[str, float, float, bool]]] = {s.sweep_id: [] for s in sweeps}
    for i, a in enumerate(sweeps):
        for b in sweeps[i + 1 :]:
            pa, pb = a.position, b.position
            meters = math.dist((pa.x, pa.y, pa.z), (pb.x, pb.y, pb.z))
            if meters > STEP_M or abs(pa.y - pb.y) > MAX_RISE_M:
                continue  # too far, or straight up through a floor (stairs rise a little per step)
            line = LineString([_xz(a), _xz(b)])
            unmapped = False
            if a.room_id == b.room_id:
                if not polys[a.room_id].buffer(0.3).contains(line):
                    continue  # the straight line leaves the room: there is a wall in the way
                factor = 1.0
            elif any(line.distance(d) <= DOOR_REACH_M for d in doors.get(frozenset((a.room_id, b.room_id)), [])):
                factor = 1.0
            elif floor_of[a.room_id] != floor_of[b.room_id]:
                factor = STAIRS_COST
            elif polys[a.room_id].distance(polys[b.room_id]) <= 1.0:
                factor, unmapped = UNMAPPED_COST, True  # open-plan edge, or a door the scan missed
            else:
                continue
            graph[a.sweep_id].append((b.sweep_id, meters * factor, meters, unmapped))
            graph[b.sweep_id].append((a.sweep_id, meters * factor, meters, unmapped))
    return graph


def route(twin: PropertyTwin, from_room_id: str, to_room_id: str) -> Route | None:
    """The shortest walk between two rooms as a list of sweeps. None if no path is known."""
    start, end = best_view(twin, from_room_id), best_view(twin, to_room_id)
    if start is None or end is None:
        return None
    by_id = {s.sweep_id: s for s in twin.sweeps}
    # start from the sweep nearest the middle of the first room; end on the best view of the second
    middle = Polygon(_room(twin, from_room_id).polygon).representative_point().coords[0]
    source = min((s for s in twin.sweeps if s.room_id == from_room_id), key=lambda s: math.dist(_xz(s), middle)).sweep_id
    targets = {s.sweep_id for s in twin.sweeps if s.room_id == to_room_id}
    graph = _graph(twin.property_id, twin.version, twin.model_dump_json())

    best = {source: 0.0}
    came: dict[str, tuple[str, float, bool]] = {}
    heap = [(0.0, source)]
    reached = None
    while heap:
        cost, node = heapq.heappop(heap)
        if node in targets:
            reached = node
            break
        if cost > best.get(node, math.inf):
            continue
        for nxt, step_cost, meters, unmapped in graph.get(node, []):
            if cost + step_cost < best.get(nxt, math.inf):
                best[nxt] = cost + step_cost
                came[nxt] = (node, meters, unmapped)
                heapq.heappush(heap, (cost + step_cost, nxt))
    if reached is None:
        return None

    path, meters, unmapped_steps = [reached], 0.0, 0
    while path[-1] != source:
        prev, step_m, unmapped = came[path[-1]]
        meters += step_m
        unmapped_steps += unmapped
        path.append(prev)
    path.reverse()

    room_ids: list[str] = []
    for sid in path:
        if not room_ids or room_ids[-1] != by_id[sid].room_id:
            room_ids.append(by_id[sid].room_id)
    # Stairs are judged by how far the route actually climbs, not by room labels: the bottom steps
    # of a staircase can be labelled as the upstairs room they lead to.
    heights = [by_id[sid].position.y for sid in path]
    climbs = max(heights) - min(heights) > STAIRS_RISE_M
    if not climbs:  # a level walk: don't name a room on another floor it merely brushed past
        level = {_room(twin, from_room_id).floor_id, _room(twin, to_room_id).floor_id}
        room_ids = [r for r in room_ids if _room(twin, r).floor_id in level]
    return Route(
        sweep_ids=path,
        room_ids=room_ids,
        distance_m=meters,
        floor_changes=1 if climbs else 0,
        unmapped_steps=unmapped_steps,
        look_at=end.look_at,
    )
