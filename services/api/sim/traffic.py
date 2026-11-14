"""Outdoor noise on the outside of the house: road traffic, rail and residual background, per outside
wall, per local hour, per octave band. Pure: (PropertyTwin, surroundings data, tables) -> levels (C4).

Emission: CNOSSOS-EU road model (Directive 2002/49/EC Annex II, 2021 coefficients), line sound
power per metre from hourly flow, speed and truck share. Rail: FTA 2018 general assessment.
Propagation (ISO 9613-2 screening): each road is cut into point sources; to each wall's outside,
    L = L_W + D_Omega - (20 lg d + 11) - A_atm - max(A_gr + A_house, D_z)
with porous-ground eq. (10), housing attenuation (Annex A.3) and the single most effective
building edge (OSM footprints, Maekawa/ISO D_z). Only sources in front of a wall reach it: the
house itself blocks the rest. Background is added unpropagated (ANSI S12.9-3 day/night levels).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

from packages.schema.twin import PropertyTwin
from services.api.twin import frames

N_BANDS = 8
BANDS_HZ = np.array([63, 125, 250, 500, 1000, 2000, 4000, 8000], dtype=float)
SPEED_OF_SOUND = 343.0
MPH = 1.609344
MIN_DISTANCE_M = 1.0
STATION_MATCH_M = 30.0
STATION_REACH_M = 1500.0  # a count describes its road this far along it, then class defaults
RECEIVER_OFFSET_M = 0.4  # outside the wall's centreline, clear of the house footprint
RAY_BIN_DEG = 0.25  # about 1.5 m sideways at 350 m
ROOF_M = 1.0  # roof above the top ceiling
DAY_HOURS = range(7, 22)  # ANSI S12.9 day; the rest is night


@dataclass(frozen=True)
class RoadClass:
    aadt: float
    heavy: float
    speed_kmh: float


@dataclass(frozen=True)
class TrafficTables:
    cnossos: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]
    v_ref: float
    v_min: float
    classes: dict[str, RoadClass]
    hourly: np.ndarray  # (24,) share of daily traffic, sums to 1
    atmosphere_db_per_m: np.ndarray
    porous_ground: bool  # lawns and gardens; False = paved, reflecting
    source_height_m: float
    window_height_m: float
    barrier_max_db: float
    housing_density: float
    housing_corridor_m: float
    housing_max_db: float
    building_height_m: float
    storey_height_m: float
    segment_m: list[tuple[float, float]]
    background_category: str
    background_day_night: tuple[float, float]
    background_shape: np.ndarray
    rail: dict
    guidelines: dict
    a_weighting: np.ndarray = field(default_factory=lambda: np.array([-26.2, -16.1, -8.6, -3.2, 0.0, 1.2, 1.0, -1.1]))


def load_traffic(data: dict, a_weighting: np.ndarray | None = None) -> TrafficTables:
    c = data["cnossos_road"]
    p = data["propagation"]
    bg = data["background"]
    hourly = np.array(data["hourly_traffic_pct"]["pct"], dtype=float)
    out = TrafficTables(
        cnossos={k: tuple(np.array(v[key], dtype=float) for key in ("AR", "BR", "AP", "BP"))
                 for k, v in c.items() if k in ("1", "2", "3")},
        v_ref=float(c["v_ref_kmh"]),
        v_min=float(c["v_min_kmh"]),
        classes={k: RoadClass(float(v["aadt"]), float(v["heavy"]), float(v["speed_mph"]) * MPH)
                 for k, v in data["road_classes"].items() if k != "about"},
        hourly=hourly / hourly.sum(),
        atmosphere_db_per_m=np.array(p["atmosphere_db_per_km"], dtype=float) / 1000.0,
        porous_ground=p["ground"] == "porous",
        source_height_m=float(p["source_height_m"]),
        window_height_m=float(p["window_height_m"]),
        barrier_max_db=float(p["barrier_max_db"]),
        housing_density=float(p["housing_density"]),
        housing_corridor_m=float(p["housing_corridor_m"]),
        housing_max_db=float(p["housing_max_db"]),
        building_height_m=float(p["building_height_m"]),
        storey_height_m=float(p["storey_height_m"]),
        segment_m=[(float(a), float(b)) for a, b in p["segment_m"]],
        background_category=bg["category"],
        background_day_night=(float(bg["categories"][bg["category"]]["day"]), float(bg["categories"][bg["category"]]["night"])),
        background_shape=np.array(bg["shape_db"], dtype=float),
        rail=data["rail"],
        guidelines=data["guidelines"],
    )
    if a_weighting is not None:
        object.__setattr__(out, "a_weighting", np.asarray(a_weighting, dtype=float))
    return out


# --- emission -------------------------------------------------------------------------------


def vehicle_power(t: TrafficTables, category: str, speed_kmh: float) -> np.ndarray:
    """Sound power of one vehicle per band, dB re 1 pW (CNOSSOS 2.2.2-2.2.12, reference conditions)."""
    ar, br, ap, bp = t.cnossos[category]
    v = max(speed_kmh, t.v_min)
    rolling = ar + br * np.log10(v / t.v_ref)
    propulsion = ap + bp * (v - t.v_ref) / t.v_ref
    return 10.0 * np.log10(10.0 ** (rolling / 10.0) + 10.0 ** (propulsion / 10.0))


def line_power(t: TrafficTables, flows: dict[str, float], speed_kmh: float) -> np.ndarray:
    """Sound power per metre of road for hourly flows {category: vehicles/h}, dB re 1 pW/m (2.2.1)."""
    total = np.zeros(N_BANDS)
    for category, q in flows.items():
        if q > 0:
            v = max(speed_kmh, t.v_min)
            total += 10.0 ** (vehicle_power(t, category, v) / 10.0) * q / (1000.0 * v)
    return 10.0 * np.log10(np.maximum(total, 1e-30))


def flows(aadt: float, heavy: float) -> dict[str, float]:
    """Mean hourly flow per CNOSSOS category; trucks split evenly between medium and heavy."""
    q = aadt / 24.0
    return {"1": q * (1.0 - heavy), "2": q * heavy / 2.0, "3": q * heavy / 2.0}


def rail_line_power(t: TrafficTables) -> tuple[np.ndarray, np.ndarray]:
    """(mean-hour sound power per metre per band, (24,) energy weights) for the rail defaults."""
    r = t.rail
    s = float(r["speed_mph"])
    sel = 10.0 * np.log10(
        r["locomotives"] * 10.0 ** ((r["locomotive_sel_dba"] - 10.0 * np.log10(s / 50.0)) / 10.0)
        + r["cars"] * 10.0 ** ((r["car_sel_dba"] + 20.0 * np.log10(s / 50.0)) / 10.0))
    per_hour = np.array([r["trains_day"] / 15.0 if h in DAY_HOURS else r["trains_night"] / 9.0 for h in range(24)])
    mean = per_hour.mean()
    leq_50ft = sel + 10.0 * np.log10(mean) - 35.6
    # inverse of the hard-ground infinite line, L(d) = L_W' - 10 lg d - 3
    lw_a = leq_50ft + 10.0 * np.log10(15.24) + 3.0
    return _bands_for(t, np.array(r["shape_db"], dtype=float), lw_a), per_hour / mean


def background_hourly(t: TrafficTables) -> np.ndarray:
    """(24, bands) residual background, 10^(L/10)."""
    day, night = t.background_day_night
    return np.array([10.0 ** (_bands_for(t, t.background_shape, day if h in DAY_HOURS else night) / 10.0)
                     for h in range(24)])


def _bands_for(t: TrafficTables, shape: np.ndarray, level_dba: float) -> np.ndarray:
    return shape + (level_dba - a_sum(shape, t.a_weighting))


def a_sum(bands_db, a_weighting) -> float:
    return float(10.0 * np.log10(np.sum(10.0 ** ((np.asarray(bands_db) + a_weighting) / 10.0))))


# --- sources --------------------------------------------------------------------------------


@dataclass
class LineSource:
    source_id: str
    name: str
    kind: str  # road | rail
    data: str  # txdot_aadt | class_default | fta_default
    points: np.ndarray  # (N, 2) ENU, metres
    power: np.ndarray  # (N, bands) mean-hour sound power of each piece, 10^(L/10)
    weights: np.ndarray  # (24,) energy relative to the mean hour
    aadt: float | None = None
    heavy: float | None = None
    speed_kmh: float | None = None
    path_latlon: list[list[tuple[float, float]]] = field(default_factory=list)


def road_sources(t: TrafficTables, surroundings: dict, stations: list[dict], lat: float, lon: float) -> list[LineSource]:
    """One source per named road (or per unnamed way), cut into point sources."""
    ways = []
    for r in surroundings.get("roads", []):
        if r["highway"] not in t.classes:
            continue
        line = LineString([frames.latlon_to_enu(a, b, lat, lon) for a, b in r["geometry"]])
        ways.append((r, line))
    counts = _match_stations(ways, stations, lat, lon)
    weights = t.hourly * 24.0
    groups: dict[str, dict] = {}
    for (r, line), count in zip(ways, counts):
        cls = t.classes[r["highway"]]
        oneway = r.get("oneway") in ("yes", "-1", "true", "1")
        if count is not None:
            aadt, directional = count
            data = "txdot_aadt"
        else:
            aadt, directional, data = cls.aadt, False, "class_default"
        way_aadt = aadt if (directional or not oneway) else aadt / 2.0
        speed = _speed_kmh(r.get("maxspeed")) or cls.speed_kmh
        lw = line_power(t, flows(way_aadt, cls.heavy), speed)
        points, lengths = _cut(line, t.segment_m)
        name = r.get("name") or f"unnamed {r['highway'].replace('_', ' ')}"
        g = groups.setdefault(name, {"points": [], "power": [], "paths": [], "aadt": 0.0, "heavy": cls.heavy,
                                     "speed": speed, "data": data, "ids": []})
        g["points"].append(points)
        g["power"].append(10.0 ** (lw[None, :] / 10.0) * lengths[:, None])
        g["paths"].append([tuple(p) for p in r["geometry"]])
        g["ids"].append(r["osm_id"])
        if aadt >= g["aadt"]:
            g["aadt"], g["heavy"], g["speed"] = aadt, cls.heavy, speed
        if data == "txdot_aadt":
            g["data"] = data
    out = []
    for name, g in groups.items():
        out.append(LineSource(
            source_id=_slug(name), name=name, kind="road", data=g["data"],
            points=np.vstack(g["points"]), power=np.vstack(g["power"]), weights=weights,
            aadt=round(g["aadt"]), heavy=g["heavy"], speed_kmh=round(g["speed"], 1), path_latlon=g["paths"]))
    return sorted(out, key=lambda s: s.source_id)


def rail_sources(t: TrafficTables, surroundings: dict, lat: float, lon: float) -> list[LineSource]:
    rails = surroundings.get("rail", [])
    if not rails:
        return []
    lw, weights = rail_line_power(t)
    groups: dict[str, list] = {}
    for r in rails:
        groups.setdefault(r.get("name") or "railway", []).append(r)
    out = []
    for name, items in groups.items():
        pts, power, paths = [], [], []
        for r in items:
            line = LineString([frames.latlon_to_enu(a, b, lat, lon) for a, b in r["geometry"]])
            p, lengths = _cut(line, t.segment_m)
            pts.append(p)
            power.append(10.0 ** (lw[None, :] / 10.0) * lengths[:, None])
            paths.append([tuple(q) for q in r["geometry"]])
        out.append(LineSource(source_id=_slug(name), name=name, kind="rail", data="fta_default",
                              points=np.vstack(pts), power=np.vstack(power), weights=weights,
                              speed_kmh=round(float(t.rail["speed_mph"]) * MPH, 1), path_latlon=paths))
    return out


def _match_stations(ways, stations, lat, lon) -> list[tuple[float, bool] | None]:
    """(AADT, one direction only) per way from the nearest count on the same road, else None.
    A count sits on the highest-class way within STATION_MATCH_M; service-road counts (suffix SR)
    on frontage roads. It then applies to every way with that name within STATION_REACH_M."""
    rank = {"motorway": 0, "trunk": 1, "primary": 2, "secondary": 3, "tertiary": 4}
    placed = []  # (name, point, aadt, directional)
    for s in stations:
        p = Point(frames.latlon_to_enu(s["lat"], s["lon"], lat, lon))
        near = [(r, line) for r, line in ways if line.distance(p) <= STATION_MATCH_M and r.get("name")]
        if not near:
            continue
        service_road = s["station_id"].upper().endswith("SR")
        def preference(item):
            r, line = item
            frontage = "frontage" in (r.get("name") or "").lower()
            return (frontage != service_road, rank.get(r["highway"].replace("_link", ""), 9), line.distance(p))
        r, _ = min(near, key=preference)
        directional = bool(re.search(r"(NB|SB|EB|WB)(SR)?$", s["station_id"].upper()))
        placed.append((r["name"], p, float(s["aadt"]), directional))
    out = []
    for r, line in ways:
        best = None
        for name, p, aadt, directional in placed:
            d = line.distance(p)
            if name == r.get("name") and d <= STATION_REACH_M and (best is None or d < best[0]):
                best = (d, aadt, directional)
        out.append(None if best is None else (best[1], best[2]))
    return out


def _speed_kmh(tag: str | None) -> float | None:
    if not tag:
        return None
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(mph)?", tag)
    if not m:
        return None
    v = float(m.group(1))
    return v * MPH if m.group(2) else v


def _cut(line: LineString, tiers: list[tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """Midpoints and lengths of pieces no longer than the tier step for their distance to the house."""
    coords = np.asarray(line.coords)
    points, lengths = [], []
    for a, b in zip(coords[:-1], coords[1:]):
        seg = float(np.hypot(*(b - a)))
        if seg <= 0:
            continue
        dist = float(np.hypot(*((a + b) / 2.0)))
        step = next(s for limit, s in tiers if dist <= limit)
        n = max(int(np.ceil(seg / step)), 1)
        f = (np.arange(n) + 0.5) / n
        points.append(a + np.outer(f, b - a))
        lengths.append(np.full(n, seg / n))
    if not points:
        return np.zeros((0, 2)), np.zeros(0)
    return np.vstack(points), np.concatenate(lengths)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# --- the house's outside --------------------------------------------------------------------


@dataclass
class Receiver:
    key: str  # wall_id, or opening_id for an outside opening that isn't on a known wall
    room_id: str
    point: np.ndarray  # (2,) ENU
    normal: np.ndarray  # (2,) ENU, outward
    height_m: float  # above ground


def receivers(twin: PropertyTwin, t: TrafficTables) -> list[Receiver]:
    """A listening point just outside every outside wall (and loose outside opening), window height."""
    m = frames.from_column_major(twin.interior_to_world)
    rotation = m[:3, :3]
    rooms = {r.room_id: r for r in twin.rooms}
    ground = min((r.floor_y for r in twin.rooms), default=0.0)
    out = []
    on_walls = set()
    for w in twin.walls:
        on_walls.update(w.opening_ids)
        if not w.exterior or not w.room_ids or w.room_ids[0] not in rooms:
            continue
        room = rooms[w.room_ids[0]]
        (x0, z0), (x1, z1) = w.ends
        mid = np.array([(x0 + x1) / 2.0, (z0 + z1) / 2.0])
        direction = np.array([x1 - x0, z1 - z0])
        length = float(np.hypot(*direction))
        if length <= 0:
            continue
        normal = np.array([direction[1], -direction[0]]) / length
        if _points_inward(room.polygon, mid, normal):
            normal = -normal
        out.append(_receiver(w.wall_id, room, mid + normal * (RECEIVER_OFFSET_M + w.thickness_m / 2.0), normal,
                             rotation, m, room.floor_y - ground + t.window_height_m))
    for o in twin.openings:
        if o.opening_id in on_walls or not o.exterior or o.room_id not in rooms:
            continue
        room = rooms[o.room_id]
        c = np.mean([[p.x, p.z] for p in o.polygon], axis=0)
        world_normal = np.array([np.sin(np.radians(o.facade_azimuth_deg)), np.cos(np.radians(o.facade_azimuth_deg)), 0.0])
        normal = (rotation.T @ world_normal)[[0, 2]]
        normal /= max(np.hypot(*normal), 1e-9)
        out.append(_receiver(o.opening_id, room, c + normal * RECEIVER_OFFSET_M, normal, rotation, m,
                             room.floor_y - ground + t.window_height_m))
    return out


def _receiver(key, room, xz, normal_xz, rotation, m, height) -> Receiver:
    world = frames.apply(m, (xz[0], room.floor_y, xz[1]))
    n = rotation @ np.array([normal_xz[0], 0.0, normal_xz[1]])
    n2 = n[:2] / max(np.hypot(n[0], n[1]), 1e-9)
    return Receiver(key=key, room_id=room.room_id, point=world[:2], normal=n2, height_m=float(height))


def _points_inward(polygon, mid, normal) -> bool:
    shape = Polygon(polygon)
    probe = Point(*(mid + normal * 0.3))
    if shape.contains(probe):
        return True
    if shape.contains(Point(*(mid - normal * 0.3))):
        return False
    c = np.array(shape.centroid.coords[0])
    return float(np.dot(c - mid, normal)) > 0


@dataclass
class Obstacles:
    starts: np.ndarray  # (E, 2) ENU, one row per footprint edge
    ends: np.ndarray  # (E, 2)
    heights: np.ndarray  # (E,) m above ground
    extent_m: float  # farthest obstacle point from the property origin


def obstacles(twin: PropertyTwin, surroundings: dict, t: TrafficTables) -> Obstacles:
    """Neighbouring buildings (OSM footprints) and the house itself, one block per floor at that
    floor's roof height, so an upstairs wall sees over a lower roof."""
    m = frames.from_column_major(twin.interior_to_world)
    ground = min((r.floor_y for r in twin.rooms), default=0.0)
    polys, heights = [], []
    for floor_id in {r.floor_id for r in twin.rooms}:
        rooms = [r for r in twin.rooms if r.floor_id == floor_id and len(r.polygon) >= 3]
        if not rooms:
            continue
        # closing fills the wall gaps between room outlines; the net shrink keeps a wall's own
        # receiver from grazing it
        block = unary_union([Polygon([frames.apply(m, (x, r.floor_y, z))[:2] for x, z in r.polygon]).buffer(0)
                             for r in rooms])
        polys.append(block.buffer(0.2, join_style="mitre").buffer(-0.25, join_style="mitre").simplify(0.1))
        heights.append(max(r.floor_y + r.height_m for r in rooms) - ground + ROOF_M)
    house = unary_union(polys) if polys else Polygon()
    for b in surroundings.get("buildings", []):
        if len(b["geometry"]) < 4:
            continue
        p = Polygon([frames.latlon_to_enu(a, c, twin.lat, twin.lon) for a, c in b["geometry"]]).buffer(0)
        if p.is_empty or p.intersects(house.buffer(1.0)):
            continue  # that's this house in OSM
        polys.append(p)
        heights.append(_building_height(b, t))
    starts, ends, edge_heights = [], [], []
    for p, h in zip(polys, heights):
        for ring in shapely.get_rings(p):
            c = shapely.get_coordinates(ring)
            starts.append(c[:-1])
            ends.append(c[1:])
            edge_heights.append(np.full(len(c) - 1, h))
    if not starts:
        return Obstacles(np.zeros((0, 2)), np.zeros((0, 2)), np.zeros(0), 0.0)
    starts, ends = np.vstack(starts), np.vstack(ends)
    return Obstacles(starts=starts, ends=ends, heights=np.concatenate(edge_heights),
                     extent_m=float(np.hypot(*np.vstack([starts, ends]).T).max()))


def _building_height(b: dict, t: TrafficTables) -> float:
    for key, scale in (("height", 1.0), ("levels", t.storey_height_m)):
        try:
            return float(str(b.get(key)).split()[0]) * scale + (1.5 if key == "levels" else 0.0)
        except (TypeError, ValueError):
            continue
    return t.building_height_m


# --- propagation ----------------------------------------------------------------------------


def incident(t: TrafficTables, rec: Receiver, points: np.ndarray, power: np.ndarray,
             obs: Obstacles | None) -> np.ndarray:
    """(N, bands) mean-hour level just outside one wall from each point source, 10^(L/10)."""
    out = np.zeros((len(points), N_BANDS))
    v = points - rec.point
    dh = np.hypot(v[:, 0], v[:, 1])
    front = (v @ rec.normal) > 0
    if not front.any():
        return out
    dh, power, pts = dh[front], power[front], points[front]
    hs, hr = t.source_height_m, rec.height_m
    d = np.maximum(np.hypot(dh, hr - hs), MIN_DISTANCE_M)
    d_omega = 10.0 * np.log10(1.0 + (dh**2 + (hs - hr) ** 2) / (dh**2 + (hs + hr) ** 2))
    a_div = 20.0 * np.log10(d) + 11.0
    a_atm = t.atmosphere_db_per_m[None, :] * d[:, None]
    hm = (hs + hr) / 2.0
    a_gr = np.maximum(4.8 - (2.0 * hm / d) * (17.0 + 300.0 / d), 0.0) if t.porous_ground else np.zeros_like(d)
    a_house = np.minimum(0.1 * t.housing_density * np.maximum(dh - t.housing_corridor_m, 0.0), t.housing_max_db)
    dz = barrier_db(t, pts, rec, obs) if obs is not None else np.zeros((len(d), N_BANDS))
    excess = np.maximum((a_gr + a_house)[:, None], dz)
    level = 10.0 * np.log10(power) + d_omega[:, None] - a_div[:, None] - a_atm - excess
    out[front] = 10.0 ** (level / 10.0)
    return out


def barrier_db(t: TrafficTables, pts: np.ndarray, rec: Receiver, obs: Obstacles) -> np.ndarray:
    """(N, bands) ISO 9613-2 D_z of the most effective building edge on each source->wall path.

    Paths are traced as one ray per RAY_BIN_DEG of direction from the wall; every source in that
    direction shares the ray's footprint-edge crossings (those nearer than the source itself)."""
    n = len(pts)
    z = np.zeros(n)
    v = pts - rec.point
    dist = np.hypot(v[:, 0], v[:, 1])
    angle = np.arctan2(v[:, 1], v[:, 0])
    bins = np.round(angle / np.radians(RAY_BIN_DEG)).astype(int)
    ids, inverse = np.unique(bins, return_inverse=True)
    longest = np.zeros(len(ids))
    np.maximum.at(longest, inverse, dist)
    theta = ids * np.radians(RAY_BIN_DEG)
    u = np.stack([np.cos(theta), np.sin(theta)], axis=1)  # (R, 2)
    w = obs.starts - rec.point  # (E, 2)
    e = obs.ends - obs.starts
    den = u[:, None, 0] * e[None, :, 1] - u[:, None, 1] * e[None, :, 0]
    with np.errstate(divide="ignore", invalid="ignore"):
        s = (w[None, :, 0] * e[None, :, 1] - w[None, :, 1] * e[None, :, 0]) / den
        f = (w[None, :, 0] * u[:, None, 1] - w[None, :, 1] * u[:, None, 0]) / den
    hit = (np.abs(den) > 1e-12) & (f >= 0) & (f <= 1) & (s > 0) & (s < longest[:, None])
    ray_of, ei = np.nonzero(hit)  # sorted by ray
    if len(ray_of):
        hb, b_all = obs.heights[ei], s[ray_of, ei]
        hs, hr = t.source_height_m, rec.height_m
        starts = np.searchsorted(ray_of, np.arange(len(ids)))
        stops = np.searchsorted(ray_of, np.arange(len(ids)), side="right")
        point_order = np.argsort(inverse, kind="stable")
        p_starts = np.searchsorted(inverse[point_order], np.arange(len(ids)))
        p_stops = np.searchsorted(inverse[point_order], np.arange(len(ids)), side="right")
        for k in np.flatnonzero(stops > starts):
            members = point_order[p_starts[k]:p_stops[k]]
            total = dist[members][:, None]
            b = b_all[starts[k]:stops[k]][None, :]
            h = hb[starts[k]:stops[k]][None, :]
            a = total - b
            detour = np.hypot(a, h - hs) + np.hypot(b, h - hr) - np.hypot(total, hr - hs)
            detour = np.where(a > 0, detour, 0.0)
            z[members] = detour.max(axis=1)
    wavelength = SPEED_OF_SOUND / BANDS_HZ
    dz = 10.0 * np.log10(3.0 + 20.0 * z[:, None] / wavelength[None, :])
    return np.where(z[:, None] > 0, np.minimum(dz, t.barrier_max_db), 0.0)


@dataclass
class Outdoor:
    receivers: list[Receiver]
    sources: list[LineSource]
    energy: np.ndarray  # (receivers, sources, bands) mean hour, 10^(L/10)
    background: np.ndarray  # (24, bands)
    background_label: str = ""

    def hourly(self, include: set[str] | None = None, background: bool = True) -> np.ndarray:
        """(receivers, 24, bands) level outside each wall per hour, 10^(L/10)."""
        weights = np.array([s.weights for s in self.sources]) if self.sources else np.zeros((0, 24))
        if include is not None:
            weights = weights * np.array([[s.source_id in include] for s in self.sources])
        out = np.einsum("rsb,sh->rhb", self.energy, weights) if self.sources else np.zeros((len(self.receivers), 24, N_BANDS))
        if background:
            out = out + self.background[None, :, :]
        return out


def outdoor(twin: PropertyTwin, t: TrafficTables, sources: list[LineSource], surroundings: dict | None) -> Outdoor:
    recs = receivers(twin, t)
    obs = obstacles(twin, surroundings or {}, t) if surroundings is not None else None
    energy = np.zeros((len(recs), len(sources), N_BANDS))
    if sources:
        points = np.vstack([s.points for s in sources])
        power = np.vstack([s.power for s in sources])
        owner = np.concatenate([np.full(len(s.points), j) for j, s in enumerate(sources)])
        for i, r in enumerate(recs):
            np.add.at(energy[i], owner, incident(t, r, points, power, obs))
    return Outdoor(receivers=recs, sources=sources, energy=energy, background=background_hourly(t),
                   background_label=t.background_category)
