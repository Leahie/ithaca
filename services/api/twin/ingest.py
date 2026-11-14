"""Ingestion step 1: Matterport floors, rooms, openings and sweeps -> PropertyTwin.

Keyed by property_id only (C1). Ids come from Matterport (I-G17), output is sorted and rounded so
the same inputs always give byte-identical JSON, and a missing source degrades `quality` instead of
failing (C3).

    python -m services.api.twin.ingest onXQLXhfyQv [--mode record] [--plot]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
from pathlib import Path
from typing import Any

import numpy as np
from shapely.geometry import Polygon
from timezonefinder import TimezoneFinder

from packages.schema.twin import (
    Floor,
    FloorOpening,
    Opening,
    PropertyTwin,
    Quat,
    Room,
    Sweep,
    TwinAssets,
    TwinQuality,
    TwinSources,
    Vec3,
    Wall,
)
from services.api.integrations import costar
from services.api.integrations.cache import RecordReplayCache
from services.api.integrations.config import fixtures_dir
from services.api.integrations.matterport import MatterportClient
from services.api.twin import annotations, frames, nav, splat
from services.api.twin import panos as panos_frames
from services.api.twin import rooms as plan
from services.api.twin.mesh import interior_glb_path
from services.api.twin.service import twin_path

log = logging.getLogger(__name__)

SKIP_ROOM_KEYWORDS = {"nonArea", "hide"}
SKIP_ROOM_LABELS = {"Void"}
MIN_ROOM_AREA_M2 = 0.25
# A room whose own floor elevation is further than this from its floor's is a stairwell or an
# open-to-below area; it gets the floor's elevation instead.
ROOM_FLOOR_TOLERANCE_M = 0.5
DEFAULT_CEILING_M = 2.44
DEFAULT_CAMERA_HEIGHT_M = 1.5
DEFAULT_OPENING_HEIGHT_M = {"window": 1.2, "door": 2.1, "slider": 2.1}
GEOREF_TOLERANCE_M = 2.0
OPENING_SIDE_PROBE_M = 0.3
# An opening with no room on its far side leads outside unless another room outline (labeled or
# not) starts within this distance beyond it. Longer would misread courtyards; shorter, wide walls.
EXTERIOR_PROBE_M = 2.0
MIN_OPENING_WIDTH_M = 0.2  # Matterport has a few centimetre-wide annotation slivers
WINDOW_GLAZING = "double"  # assumed until annotated
FLOOR_MATERIAL_BY_LABEL = {
    "bathroom": "tile",
    "kitchen": "tile",
    "laundry": "tile",
    "bedroom": "carpet",
    "closet": "carpet",
}
DEFAULT_FLOOR_MATERIAL = "hardwood"
# Matterport measures wall thickness. Here every outside window and door sits in a 0.16 m wall and
# every interior doorway in a 0.11-0.14 m one: framed outside wall vs. stud partition.
EXTERIOR_WALL_MIN_THICKNESS_M = 0.15
DEFAULT_WALL_THICKNESS_M = 0.12
EXTERIOR_ASSEMBLY = "exterior_brick_veneer_frame"
INTERIOR_ASSEMBLY = "interior_stud_gypsum"
MIN_FLOOR_OPENING_OVERLAP_M2 = 0.5


def build_twin(
    property_id: str,
    *,
    matterport_model_id: str | None = None,
    cache: RecordReplayCache | None = None,
) -> PropertyTwin:
    cache = cache or RecordReplayCache()
    model_id = matterport_model_id or property_id
    m = MatterportClient(cache=cache).twin_inputs(model_id)

    lat, lon = _latlon(m)
    heading, translation, alignment = _georef(m, lat, lon)

    vertices: dict[str, plan.Point] = {}
    edges: dict[str, dict[str, Any]] = {}
    raw_floors = sorted(m.get("floors") or [], key=lambda f: (f.get("sequence", 0), f["id"]))
    for f in raw_floors:
        for v in f.get("vertices") or []:
            vertices[v["id"]] = (v["position"]["x"], v["position"]["y"])
        for e in f.get("edges") or []:
            ends = [v["id"] for v in e.get("vertices") or []]
            if len(ends) == 2:
                edges[e["id"]] = {"ends": ends, "floor_id": f["id"], "openings": e.get("openings") or [],
                                  "thickness": e.get("thickness")}

    kept = _room_outlines(m.get("rooms") or [], edges, vertices)
    every_room = _room_outlines(m.get("rooms") or [], edges, vertices, keep_all=True)
    panos = [p for p in m.get("panoLocations") or [] if p.get("position") and p.get("placement") != "unplaced"]
    elevations = _floor_elevations(raw_floors, kept, panos)

    floors = [
        Floor(floor_id=f["id"], index=i, elevation_m=_r(elevations[f["id"]]), label=f.get("label"))
        for i, f in enumerate(raw_floors)
    ]
    floor_index = {f.floor_id: f.index for f in floors}
    rooms = _rooms(kept, elevations)
    rooms.sort(key=lambda r: (floor_index.get(r.floor_id, 0), r.room_id))
    annotated = annotations.load_openings(property_id)
    openings = annotated if annotated is not None else _openings(
        raw_floors, edges, vertices, kept, elevations, heading, every_room)
    if annotated is not None:
        opening_quality = "annotated"
    else:
        opening_quality = "detected" if any(e["openings"] for e in edges.values()) else "none"
    walls = _walls(edges, vertices, kept, every_room, {o.opening_id for o in openings})
    floor_openings = _floor_openings(kept, every_room, rooms, openings, edges, floor_index)

    return annotations.apply_room_corrections(PropertyTwin(
        property_id=property_id,
        version=1,
        sources=TwinSources(
            matterport_model_id=model_id,
            costar_listing_id=costar.listing_id_for_model(model_id, cache=cache),
        ),
        address=((m.get("address") or {}).get("address") or m.get("name") or property_id),
        lat=lat,
        lon=lon,
        timezone=TimezoneFinder().timezone_at(lat=lat, lng=lon) or "UTC",
        interior_to_world=frames.to_column_major(frames.interior_to_world_matrix(heading, translation)),
        floors=floors,
        rooms=rooms,
        openings=openings,
        walls=walls,
        floor_openings=floor_openings,
        sweeps=_sweeps(panos, kept, raw_floors, elevations, nav.load_neighbors(property_id)),
        assets=_assets(property_id),
        quality=TwinQuality(
            alignment=alignment,
            openings=opening_quality,
            context="none",
            traffic="none",
            walls="inferred" if walls else "none",
        ),
    ))


def write_twin(twin: PropertyTwin, path: Path) -> PropertyTwin:
    """Write the twin, keeping the stored version unless the content changed (C1)."""
    if path.exists():
        old = PropertyTwin.model_validate_json(path.read_text(encoding="utf-8"))
        same = old.model_dump(exclude={"version"}) == twin.model_dump(exclude={"version"})
        twin = twin.model_copy(update={"version": old.version if same else old.version + 1})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(twin.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return twin


def _assets(property_id: str) -> TwinAssets:
    """Asset keys relative to the fixtures dir; the API turns them into URLs."""
    glb = interior_glb_path(property_id)
    assets = TwinAssets(interior_glb=glb.relative_to(fixtures_dir()).as_posix() if glb.exists() else None)
    ply, placed = splat.splat_path(property_id), splat.transform_path(property_id)
    if ply.exists() and placed.exists():
        assets.splat = ply.relative_to(fixtures_dir()).as_posix()
        assets.splat_to_interior = json.loads(placed.read_text(encoding="utf-8"))["splat_to_interior"]
    return assets


# --- georeference --------------------------------------------------------------------------


def _latlon(m: dict) -> tuple[float, float]:
    geo = m.get("geolocation") or {}
    try:
        return float(geo["lat"]), float(geo["long"])
    except (KeyError, TypeError, ValueError):
        log.warning("model %s has no geolocation; using (0, 0)", m.get("id"))
        return 0.0, 0.0


def _georef(m: dict, lat: float, lon: float) -> tuple[float, tuple[float, float], str]:
    """(heading, ENU offset of the model origin, quality) from Matterport's georeference."""
    g = m.get("geocoordinates") or {}
    rotation, translation = g.get("rotation"), g.get("translation")
    if not rotation or not translation:
        return 0.0, (0.0, 0.0), "gps_only"
    heading = frames.yaw_from_quaternion(**rotation)
    offset = (translation["x"], translation["y"])
    error = georef_residual_m(g, heading, offset, lat, lon)
    if error is not None and error > GEOREF_TOLERANCE_M:
        log.warning("georeference disagrees with Matterport's own conversion by %.1f m; using GPS only", error)
        return 0.0, (0.0, 0.0), "gps_only"
    return heading, offset, "georef"


def georef_residual_m(g: dict, heading: float, offset: tuple[float, float], lat: float, lon: float) -> float | None:
    """Worst distance between our model -> lat/lon and Matterport's geoLocationOf probes."""
    rotation = frames.model_to_world_rotation(heading)
    probes = {"checkOrigin": (0, 0, 0), "checkX": (10, 0, 0), "checkY": (0, 10, 0)}
    errors = []
    for alias, point in probes.items():
        probe = g.get(alias) or {}
        if probe.get("status") != "COMPLETE":
            continue
        east, north, _ = rotation @ np.asarray(point, dtype=float) + np.array([*offset, 0.0])
        e2, n2 = frames.latlon_to_enu(float(probe["lat"]), float(probe["long"]), lat, lon)
        errors.append(math.hypot(east - e2, north - n2))
    return max(errors) if errors else None


# --- rooms ---------------------------------------------------------------------------------


def _room_outlines(raw_rooms: list[dict], edges: dict, vertices: dict, keep_all: bool = False) -> list[dict]:
    kept = []
    for r in raw_rooms:
        classes = sorted(r.get("classifications") or [], key=lambda c: -(c.get("confidence") or 0))
        label = r.get("label") or (classes[0]["label"] if classes else None)
        skipped = label in SKIP_ROOM_LABELS or SKIP_ROOM_KEYWORDS & set(r.get("keywords") or [])
        if skipped and not keep_all:
            continue
        boundary = [e["id"] for e in (r.get("boundary") or {}).get("edges") or [] if e["id"] in edges]
        outline = plan.outline_from_edges([tuple(edges[e]["ends"]) for e in boundary], vertices)
        area = plan.signed_area(outline) if outline else 0.0
        if area < MIN_ROOM_AREA_M2:
            continue
        kept.append({"raw": r, "label": label or "Room", "outline": outline, "area": area,
                     "floor_id": (r.get("floor") or {}).get("id"), "edges": set(boundary)})
    return kept


def _floor_elevations(raw_floors: list[dict], kept: list[dict], panos: list[dict]) -> dict[str, float]:
    out = {}
    for f in raw_floors:
        own = [
            k["raw"]["dimensionEstimates"]["lowerElevation"]
            for k in kept
            if k["floor_id"] == f["id"] and (k["raw"].get("dimensionEstimates") or {}).get("lowerElevation") is not None
        ]
        cams = [p["position"]["z"] for p in panos if ((p.get("anchor") or {}).get("floor") or {}).get("id") == f["id"]]
        if own:
            out[f["id"]] = statistics.median(own)
        elif cams:
            out[f["id"]] = statistics.median(cams) - DEFAULT_CAMERA_HEIGHT_M
        else:
            out[f["id"]] = 0.0
    return out


def _rooms(kept: list[dict], elevations: dict[str, float]) -> list[Room]:
    heights = [h for k in kept if (h := _room_height(k["raw"])) is not None]
    fallback_height = statistics.median(heights) if heights else DEFAULT_CEILING_M
    rooms = []
    for k in kept:
        r = k["raw"]
        floor_elevation = elevations.get(k["floor_id"], 0.0)
        own = (r.get("dimensionEstimates") or {}).get("lowerElevation")
        floor_y = own if own is not None and abs(own - floor_elevation) <= ROOM_FLOOR_TOLERANCE_M else floor_elevation
        polygon = [(_r(x), _r(z)) for x, z in (frames.model_xy_to_interior_xz(*p) for p in k["outline"])]
        rooms.append(Room(
            room_id=r["id"],
            floor_id=k["floor_id"],
            label=k["label"],
            polygon=plan.start_at_min(frames.ccw_from_above(polygon)),
            floor_y=_r(floor_y),
            height_m=_r(_room_height(r) or fallback_height),
            floor_material=FLOOR_MATERIAL_BY_LABEL.get(k["label"].lower(), DEFAULT_FLOOR_MATERIAL),
            area_m2=_r(k["area"], 2),
        ))
    return rooms


def _room_height(r: dict) -> float | None:
    for value in (
        (r.get("dimensionEstimates") or {}).get("height"),
        (r.get("dimensions") or {}).get("height"),
        (r.get("ceiling") or {}).get("minHeight"),
    ):
        if value:
            return value
    return None


def _room_at(point: plan.Point, floor_id: str | None, kept: list[dict]) -> str | None:
    hits = [k for k in kept if k["floor_id"] == floor_id and plan.point_in_polygon(point, k["outline"])]
    return min(hits, key=lambda k: k["area"])["raw"]["id"] if hits else None


# --- openings ------------------------------------------------------------------------------


def _leads_outside(center: np.ndarray, outward: np.ndarray, floor_id: str, every_room: list[dict]) -> bool:
    """No room outline on this floor (labeled or not) within EXTERIOR_PROBE_M beyond the opening."""
    for distance in np.arange(OPENING_SIDE_PROBE_M, EXTERIOR_PROBE_M + 1e-9, 0.1):
        if _room_at(tuple(center + outward * distance), floor_id, every_room):
            return False
    return True


def _openings(raw_floors, edges, vertices, kept, elevations, heading, every_room) -> list[Opening]:
    rotation = frames.model_to_world_rotation(heading)
    out = []
    for f in raw_floors:
        for e in f.get("edges") or []:
            if e["id"] not in edges:
                continue
            a_id, b_id = edges[e["id"]]["ends"]
            if a_id not in vertices or b_id not in vertices:
                continue
            a, b = np.array(vertices[a_id]), np.array(vertices[b_id])
            length = float(np.linalg.norm(b - a))
            if length < 1e-6:
                continue
            u = (b - a) / length
            left = np.array([-u[1], u[0]])
            for op in edges[e["id"]]["openings"]:
                kind = _opening_kind(op)
                if kind is None or (op.get("width") or 0.0) < MIN_OPENING_WIDTH_M:
                    continue
                center = a + u * (op.get("relativeCenter", 0.5) * length)
                half = min(op.get("width") or 0.0, length) / 2.0
                left_room = _room_at(tuple(center + left * OPENING_SIDE_PROBE_M), f["id"], kept)
                right_room = _room_at(tuple(center - left * OPENING_SIDE_PROBE_M), f["id"], kept)
                sides = sorted(
                    [(rid, -left) for rid in [left_room] if rid] + [(rid, left) for rid in [right_room] if rid],
                    key=lambda s: s[0],
                )
                if not sides:
                    log.info("opening %s (%s) has no room on either side; skipped", op["id"], kind)
                    continue
                room_id, outward = sides[0]
                other = sides[1][0] if len(sides) > 1 and sides[1][0] != room_id else None
                bottom = op.get("lowerElevation")
                if bottom is None:
                    bottom = elevations.get(f["id"], 0.0)
                top = bottom + (op.get("height") or DEFAULT_OPENING_HEIGHT_M[kind])
                p0, p1 = center - u * half, center + u * half
                corners = [(*p0, bottom), (*p1, bottom), (*p1, top), (*p0, top)]
                east, north, _ = rotation @ np.array([outward[0], outward[1], 0.0])
                out.append(Opening(
                    opening_id=op["id"],
                    room_id=room_id,
                    kind=kind,
                    polygon=[_vec(frames.model_to_interior(c)) for c in corners],
                    facade_azimuth_deg=_r(frames.compass_azimuth_deg(east, north), 2),
                    glazing="none" if kind == "door" else WINDOW_GLAZING,
                    other_room_id=other,
                    exterior=(exterior := False if other else _leads_outside(center, outward, f["id"], every_room)),
                    leaf=_door_leaf(op, kind, exterior),
                ))
    out.sort(key=lambda o: o.opening_id)
    return out


def _door_leaf(op: dict, kind: str, exterior: bool) -> bool | None:
    """Matterport labels hinged doors "door-pivot". Its "door-unknown" openings here are archways
    and passages (living-kitchen, hall), except on outside walls, where a door is assumed."""
    if kind != "door":
        return None
    label = (op.get("label") or "").lower()
    if "pivot" in label or "hinge" in label:
        return True
    return None if exterior else False


# --- walls ---------------------------------------------------------------------------------


def _walls(edges, vertices, kept, every_room, opening_ids: set[str]) -> list[Wall]:
    """Every wall edge that bounds a kept room. Matterport gives thickness but no material, so the
    assembly is inferred: thick walls are framed outside walls, thin ones interior partitions."""
    rooms_by_edge: dict[str, list[str]] = {}
    for k in kept:
        for e in k["edges"]:
            rooms_by_edge.setdefault(e, []).append(k["raw"]["id"])
    out = []
    for edge_id, room_ids in rooms_by_edge.items():
        edge = edges[edge_id]
        a_id, b_id = edge["ends"]
        if a_id not in vertices or b_id not in vertices:
            continue
        a, b = np.array(vertices[a_id]), np.array(vertices[b_id])
        length = float(np.linalg.norm(b - a))
        if length < 1e-6:
            continue
        thickness = edge["thickness"] or DEFAULT_WALL_THICKNESS_M
        exterior = False
        if len(room_ids) == 1:
            u = (b - a) / length
            left = np.array([-u[1], u[0]])
            mid = (a + b) / 2
            outward = left if _room_at(tuple(mid - left * OPENING_SIDE_PROBE_M), edge["floor_id"], kept) else -left
            exterior = _leads_outside(mid, outward, edge["floor_id"], every_room)
        ends = tuple((_r(x), _r(z)) for x, z in (frames.model_xy_to_interior_xz(*p) for p in (a, b)))
        out.append(Wall(
            wall_id=edge_id,
            floor_id=edge["floor_id"],
            ends=ends,
            thickness_m=_r(thickness),
            room_ids=sorted(room_ids),
            exterior=exterior,
            assembly=EXTERIOR_ASSEMBLY if thickness >= EXTERIOR_WALL_MIN_THICKNESS_M else INTERIOR_ASSEMBLY,
            opening_ids=sorted(op["id"] for op in edge["openings"] if op["id"] in opening_ids),
        ))
    out.sort(key=lambda w: w.wall_id)
    return out


def _floor_openings(kept, every_room, rooms: list[Room], openings: list[Opening], edges, floor_index) -> list[FloorOpening]:
    """Hidden "Void" spaces that a room opens onto through a passage and that overlap a room on the
    next floor: stairwells and open-to-below areas. Voids with no passage (duct chases) are skipped."""
    kept_ids = {k["raw"]["id"] for k in kept}
    passages = {o.opening_id: o for o in openings if o.kind == "door" and o.leaf is False}
    room_polygons = {r.room_id: Polygon(r.polygon) for r in rooms}
    out = []
    for v in every_room:
        if v["raw"]["id"] in kept_ids:
            continue
        via = sorted(
            op["id"] for e in v["edges"] if e in edges for op in edges[e]["openings"] if op["id"] in passages
        )
        if not via:
            continue
        polygon = [(_r(x), _r(z)) for x, z in (frames.model_xy_to_interior_xz(*p) for p in v["outline"])]
        shape = Polygon(polygon)
        level = floor_index.get(v["floor_id"], 0)
        other = sorted(
            r.room_id for r in rooms
            if abs(floor_index.get(r.floor_id, 0) - level) == 1
            and room_polygons[r.room_id].intersection(shape).area >= MIN_FLOOR_OPENING_OVERLAP_M2
        )
        if not other:
            continue
        out.append(FloorOpening(
            floor_opening_id=v["raw"]["id"],
            floor_id=v["floor_id"],
            polygon=frames.ccw_from_above(polygon),
            area_m2=_r(shape.area, 2),
            room_ids=sorted({passages[o].room_id for o in via}),
            via_opening_ids=via,
            other_floor_room_ids=other,
        ))
    out.sort(key=lambda f: f.floor_opening_id)
    return out


def _opening_kind(op: dict) -> str | None:
    kind, label = op.get("type"), (op.get("label") or "").lower()
    if kind == "window":
        return "window"
    if kind in ("doorway", "opening"):
        return "slider" if "slid" in label else "door"
    return None


# --- sweeps --------------------------------------------------------------------------------


def _sweeps(panos, kept, raw_floors, elevations, neighbors: dict[str, list[str]]) -> list[Sweep]:
    kept_ids = {k["raw"]["id"] for k in kept}
    out = []
    for p in panos:
        pos = p["position"]
        anchor = p.get("anchor") or {}
        floor_id = (anchor.get("floor") or {}).get("id") or _nearest_floor(pos["z"], raw_floors, elevations)
        room_id = (anchor.get("room") or {}).get("id")
        if room_id not in kept_ids:
            room_id = _room_at((pos["x"], pos["y"]), floor_id, kept)
        out.append(Sweep(
            sweep_id=p["id"],
            floor_id=floor_id,
            room_id=room_id,
            position=_vec(frames.model_to_interior((pos["x"], pos["y"], pos["z"]))),
            label=p.get("label"),
            neighbors=neighbors.get(p["id"], []),
            rotation=_quat(panos_frames.interior_quaternion(p["rotation"])) if p.get("rotation") else None,
        ))
    out.sort(key=lambda s: s.sweep_id)
    return out


def _nearest_floor(z: float, raw_floors, elevations) -> str:
    return min(raw_floors, key=lambda f: abs(z - DEFAULT_CAMERA_HEIGHT_M - elevations[f["id"]]))["id"]


# --- helpers -------------------------------------------------------------------------------


def _r(value: float, digits: int = 3) -> float:
    return round(float(value), digits) + 0.0  # + 0.0 turns -0.0 into 0.0


def _vec(p) -> Vec3:
    return Vec3(x=_r(p[0]), y=_r(p[1]), z=_r(p[2]))


def _quat(q) -> Quat:
    return Quat(x=_r(q[0], 6), y=_r(q[1], 6), z=_r(q[2], 6), w=_r(q[3], 6))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    parser.add_argument("--matterport-model-id", help="defaults to property_id")
    parser.add_argument("--mode", choices=["live", "record", "replay"], help="overrides INTEGRATION_MODE")
    parser.add_argument("--plot", action="store_true", help="also draw each floor to out/twin/")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    twin = build_twin(args.property_id, matterport_model_id=args.matterport_model_id,
                      cache=RecordReplayCache(mode=args.mode))
    path = twin_path(args.property_id)
    twin = write_twin(twin, path)
    with_room = sum(1 for s in twin.sweeps if s.room_id)
    kinds = {k: sum(1 for o in twin.openings if o.kind == k) for k in ("window", "door", "slider")}
    print(f"wrote {path} (version {twin.version})")
    print(f"  {twin.address} | {twin.lat}, {twin.lon} | {twin.timezone} | alignment: {twin.quality.alignment}")
    print(f"  {len(twin.floors)} floors, {len(twin.rooms)} rooms, {len(twin.sweeps)} sweeps "
          f"({with_room} in a room), openings {kinds}, CoStar listing {twin.sources.costar_listing_id}")
    if args.plot:
        from services.api.twin.plot import plot_floors

        for out in plot_floors(twin):
            print(f"  plot {out}")


if __name__ == "__main__":
    main()
