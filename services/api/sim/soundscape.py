"""The soundscape around a house: how loud each kind of outdoor sound is at the house, per local
hour, for each day type (season x weekday/weekend). Pure: (PropertyTwin, surroundings, tables) -> dataset.

Sources and how each is estimated:
- highway, arterial, local streets, rail: traffic.py (CNOSSOS-EU emission, ISO 9613-2 propagation,
  TxDOT counts where there are any), grouped by road class; weekends use their own hourly profile.
- horns, dogs: events. Each honk or barking bout is a point source at a road piece or a house; the
  hour's level is SEL energy x events per hour.
- birds, cicadas, crickets and katydids, frogs and toads: area sources on a habitat grid built from
  OpenStreetMap (water, waterways, woods, mapped trees, grass, parks; yards elsewhere), with a calling
  density per habitat class, a 1 m source level, and activity by hour (sunrise and sunset), season,
  temperature and, for frogs, recent rain.
- residual: the ANSI S12.9-3 background traffic.py already uses (distant city, AC units).

Everything is propagated with traffic.incident (divergence, air absorption, ground, and the most
effective building edge) to the same receivers just outside every outside wall, then energy-averaged
over them: "at the house" is the average outside wall at window height. Units as in traffic.py:
energies are 10^(L/10) per octave band.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
import pvlib
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import nearest_points, unary_union

from packages.schema.twin import PropertyTwin
from services.api.sim import traffic
from services.api.twin import frames

N_BANDS = traffic.N_BANDS
SEASONS = ("winter", "spring", "summer", "fall")
DAYS = ("weekday", "weekend")
SUBSTEPS = (np.arange(4) + 0.5) / 4  # activity is averaged over four points in each hour
CLASS_ORDER = ("building", "water_edge", "water", "wetland", "trees", "wood", "scrub", "park", "pitch", "grass")
YARD = "yard"


@dataclass(frozen=True)
class DayType:
    id: str
    season: str
    day: str
    date: str
    sunrise_h: float
    sunset_h: float
    temperature_c: np.ndarray  # (24,) mean for the season, per local hour
    rain_share: float  # share of the season's evenings with recent rain (frogs)


@dataclass
class Source:
    source_id: str
    label: str
    category: str  # traffic | neighbourhood | nature | background
    kind: str  # steady | events
    method: str
    energy: np.ndarray  # (day types, 24, bands) at the house
    events: np.ndarray | None = None  # (day types, 24, points) events per hour from each origin
    lmax_dba: np.ndarray | None = None  # (points,) A-weighted level at the house while one event lasts
    details: dict = dataclasses.field(default_factory=dict)


# --- day types ------------------------------------------------------------------------------


def day_types(table: dict, lat: float, lon: float, timezone: str, weather: dict, year: int) -> list[DayType]:
    times = pd.to_datetime(weather["time"])
    temp = pd.Series(weather["temperature_c"], index=times, dtype=float)
    rain = pd.Series(weather["precipitation_mm"], index=times, dtype=float).fillna(0.0)
    rain_cfg = table["biophony"]["frogs"]["rain"]
    # rain in the 48 h up to 6 pm of each day decides that evening's chorus
    rain_48h = rain.rolling(48, min_periods=1).sum()
    evenings = rain_48h[rain_48h.index.hour == 18]
    out = []
    for season in SEASONS:
        cfg = table["seasons"][season]
        months = set(cfg["months"])
        in_season = temp[temp.index.month.isin(months)]
        hourly_t = in_season.groupby(in_season.index.hour).mean().reindex(range(24)).interpolate().to_numpy()
        ev = evenings[evenings.index.month.isin(months)]
        share = float((ev >= rain_cfg["mm_48h"]).mean()) if len(ev) else 0.0
        day = date.fromisoformat(f"{year}-{cfg['date']}")
        sunrise, sunset = sun_times(lat, lon, timezone, day)
        for kind in DAYS:
            out.append(DayType(id=f"{season}-{kind}", season=season, day=kind, date=day.isoformat(), sunrise_h=sunrise,
                               sunset_h=sunset, temperature_c=hourly_t, rain_share=share))
    return out


def sun_times(lat: float, lon: float, timezone: str, day: date) -> tuple[float, float]:
    """Local sunrise and sunset, hours after midnight."""
    when = pd.DatetimeIndex([pd.Timestamp(day).tz_localize(timezone)])
    t = pvlib.solarposition.sun_rise_set_transit_spa(when, lat, lon)
    hours = lambda ts: ts.hour + ts.minute / 60.0 + ts.second / 3600.0  # noqa: E731
    return hours(t["sunrise"].iloc[0]), hours(t["sunset"].iloc[0])


# --- activity (0..1 of a full chorus) -------------------------------------------------------


def activity(group: str, cfg: dict, d: DayType) -> np.ndarray:
    """(24,) share of the full chorus in each local hour: time of day x season x temperature x rain."""
    t = np.arange(24)[:, None] + SUBSTEPS[None, :]
    shape = {"birds": _birds, "cicadas": _cicadas, "night_insects": _night_insects, "frogs": _frogs}[group]
    a = shape(t, d.sunrise_h, d.sunset_h).mean(axis=1) * cfg["season"][d.season]
    if "temperature_c" in cfg:
        lo, hi = cfg["temperature_c"]
        a = a * np.clip((d.temperature_c - lo) / (hi - lo), 0.0, 1.0)
    if "rain" in cfg:
        base = cfg["rain"]["base"]
        a = a * (base + (1.0 - base) * d.rain_share)
    return a


def _gauss(t, centre, sigma):
    return np.exp(-0.5 * ((t - centre) / sigma) ** 2)


def _birds(t, sr, ss):
    """Dawn chorus around sunrise, a busy morning, a quiet afternoon, a smaller dusk chorus."""
    up = (t > sr + 0.5) & (t < ss - 0.5)
    return np.maximum.reduce([np.full_like(t, 0.005), _gauss(t, sr - 0.2, 0.75), 0.4 * _gauss(t, sr + 2.5, 1.8),
                              np.where(up, 0.12, 0.0), 0.4 * _gauss(t, ss - 0.4, 0.7)])


def _cicadas(t, sr, ss):
    """Hot afternoons, peaking mid-afternoon, with a dusk chorus; silent at night."""
    day = (t > sr + 1.5) & (t < ss + 0.7)
    return np.where(day, np.maximum(_gauss(t, 15.0, 2.8), 0.6 * _gauss(t, ss - 0.3, 0.5)), 0.0)


def _since_sunset(t, sr, ss):
    """(hours since sunset, night length): s runs 0 at sunset to night length at sunrise."""
    return (t - ss) % 24.0, (sr - ss) % 24.0


def _night_insects(t, sr, ss):
    s, night = _since_sunset(t, sr, ss)
    rise = np.clip((s + 0.5) / 2.0, 0.0, 1.0)
    rise = np.where(s > 23.5, np.clip((s - 24.0 + 0.5) / 2.0, 0.0, 1.0), rise)
    fall = np.clip((night + 0.5 - s) / 1.5, 0.0, 1.0)
    return np.maximum(np.where(s <= night + 0.5, np.minimum(rise, fall), np.where(s > 23.5, rise, 0.0)), 0.05)


def _frogs(t, sr, ss):
    """Loudest in the first hours after dark, tapering towards dawn."""
    s, night = _since_sunset(t, sr, ss)
    rise = np.clip((s + 0.3) / 1.3, 0.0, 1.0)
    late = np.interp(s, [4.5, max(night - 1.0, 4.6), night + 0.5], [1.0, 0.25, 0.0])
    value = np.where(s < 4.5, rise, late)
    value = np.where(s > night + 0.5, np.where(s > 23.7, np.clip((s - 24.0 + 0.3) / 1.3, 0.0, 1.0), 0.0), value)
    return np.maximum(value, 0.01)


# --- geometry -------------------------------------------------------------------------------


def house_polygon(twin: PropertyTwin) -> Polygon:
    """The house footprint in ENU metres (all floors)."""
    m = frames.from_column_major(twin.interior_to_world)
    parts = [Polygon([frames.apply(m, (x, r.floor_y, z))[:2] for x, z in r.polygon]).buffer(0)
             for r in twin.rooms if len(r.polygon) >= 3]
    return unary_union(parts).buffer(0.5).buffer(-0.5) if parts else Polygon()


def habitat_grid(twin: PropertyTwin, habitat: dict, buildings: list[dict], cfg: dict,
                 radius_m: float) -> tuple[np.ndarray, np.ndarray, float]:
    """(cell centres (N, 2) ENU, habitat class per cell (N,), cell area m^2) within radius_m of the house."""
    lat, lon = twin.lat, twin.lon
    enu = lambda pts: [frames.latlon_to_enu(a, b, lat, lon) for a, b in pts]  # noqa: E731
    cell = float(cfg["cell_m"])
    shapes: dict[str, list] = {k: [] for k in CLASS_ORDER}
    shores = []
    for a in habitat.get("areas", []):
        p = Polygon(enu(a["geometry"])).buffer(0)
        if p.is_empty:
            continue
        shapes[a["kind"]].append(p)
        if a["kind"] in ("water", "wetland"):
            shores.append(p.boundary)
    for line in habitat.get("lines", []):
        if len(line["geometry"]) < 2:
            continue
        g = LineString(enu(line["geometry"]))
        if line["kind"] == "waterway":
            shores.append(g)
        else:
            shapes["trees"].append(g.buffer(cfg["tree_crown_m"]))
    for tree in habitat.get("trees", []):
        shapes["trees"].append(Point(frames.latlon_to_enu(tree["lat"], tree["lon"], lat, lon)).buffer(cfg["tree_crown_m"]))
    if shores:
        shapes["water_edge"].append(unary_union(shores).buffer(cfg["water_edge_m"]))
    for b in buildings:
        if len(b["geometry"]) >= 4:
            shapes["building"].append(Polygon(enu(b["geometry"])).buffer(0))

    n = int(np.ceil(radius_m / cell))
    axis = (np.arange(-n, n + 1)) * cell
    xs, ys = np.meshgrid(axis, axis)
    xs, ys = xs.ravel(), ys.ravel()
    keep = np.hypot(xs, ys) <= radius_m
    house = house_polygon(twin)
    if not house.is_empty:
        keep &= ~shapely.contains_xy(house.buffer(cfg["quiet_m"]), xs, ys)
    xs, ys = xs[keep], ys[keep]
    classes = np.full(len(xs), YARD, dtype=object)
    free = np.ones(len(xs), dtype=bool)
    for kind in CLASS_ORDER:
        if not shapes[kind]:
            continue
        inside = shapely.contains_xy(unary_union(shapes[kind]), xs, ys) & free
        classes[inside] = kind
        free &= ~inside
    return np.stack([xs, ys], axis=1), classes, cell * cell


def yard_points(twin: PropertyTwin, buildings: list[dict], radius_m: float, yard_m: float = 3.0) -> np.ndarray:
    """Two yard spots per neighbouring house (OSM footprints of 40-1000 m^2) within radius_m, ENU:
    yard_m out from the wall that faces this house, and as far out on the other side."""
    house = house_polygon(twin)
    out = []
    for b in buildings:
        if len(b["geometry"]) < 4:
            continue
        p = Polygon([frames.latlon_to_enu(a, c, twin.lat, twin.lon) for a, c in b["geometry"]]).buffer(0)
        if p.is_empty or not 40.0 <= p.area <= 1000.0 or p.intersects(house.buffer(1.0)):
            continue
        c = np.array(p.centroid.coords[0])
        if np.hypot(*c) > radius_m:
            continue
        near = np.array(nearest_points(p.exterior, house)[0].coords[0])
        reach = max(float(np.hypot(*(near - c))), 1.0)
        u = (near - c) / reach
        out += [near + u * yard_m, c - u * (reach + yard_m)]
    return np.array(out).reshape(-1, 2)


def horn_points(t: traffic.TrafficTables, cfg: dict, surroundings: dict, stations: list[dict], lat: float,
                lon: float) -> tuple[np.ndarray, np.ndarray]:
    """(road pieces (N, 2) ENU, vehicle-km per day on each) for the roads where people honk."""
    classes = set(cfg["classes"])
    ways = []
    for r in surroundings.get("roads", []):
        if r["highway"] in classes or (r["highway"] in t.classes and "frontage" in (r.get("name") or "").lower()):
            ways.append((r, LineString([frames.latlon_to_enu(a, b, lat, lon) for a, b in r["geometry"]])))
    counts = traffic._match_stations(ways, stations, lat, lon)
    points, vkm = [], []
    for (r, line), count in zip(ways, counts):
        cls = t.classes[r["highway"]]
        oneway = r.get("oneway") in ("yes", "-1", "true", "1")
        aadt, directional = count if count is not None else (cls.aadt, False)
        way_aadt = aadt if (directional or not oneway) else aadt / 2.0
        p, lengths = traffic._cut(line, t.segment_m)
        near = np.hypot(p[:, 0], p[:, 1]) <= cfg["radius_m"] if len(p) else np.zeros(0, dtype=bool)
        points.append(p[near])
        vkm.append(way_aadt * lengths[near] / 1000.0)
    if not points:
        return np.zeros((0, 2)), np.zeros(0)
    return np.vstack(points), np.concatenate(vkm)


# --- propagation ----------------------------------------------------------------------------


def at_house(t: traffic.TrafficTables, receivers: list[traffic.Receiver], obstacles: traffic.Obstacles | None,
             points: np.ndarray, power: np.ndarray, height_m: float) -> tuple[np.ndarray, np.ndarray]:
    """((N, bands) level at the house from each point source averaged over the outside walls,
    (N, bands) the same at the wall it reaches best)."""
    if not len(points) or not receivers:
        return np.zeros((len(points), N_BANDS)), np.zeros((len(points), N_BANDS))
    th = dataclasses.replace(t, source_height_m=height_m)
    total = np.zeros((len(points), N_BANDS))
    best = np.zeros((len(points), N_BANDS))
    best_a = np.full(len(points), -np.inf)
    weight = 10.0 ** (t.a_weighting / 10.0)
    for r in receivers:
        e = traffic.incident(th, r, points, power, obstacles)
        total += e
        a = e @ weight
        louder = a > best_a
        best[louder], best_a[louder] = e[louder], a[louder]
    return total / len(receivers), best


def band_power(t: traffic.TrafficTables, shape_db, level_1m_dba: float) -> np.ndarray:
    """(bands,) sound power 10^(Lw/10) of a source with this spectrum shape and level at 1 m (L = Lw - 11 at 1 m)."""
    return 10.0 ** ((traffic._bands_for(t, np.asarray(shape_db, dtype=float), level_1m_dba) + 11.0) / 10.0)


def dba(e: np.ndarray, a_weighting: np.ndarray) -> np.ndarray:
    """A-weighted level of (..., bands) energies."""
    return 10.0 * np.log10(np.maximum((np.asarray(e) * 10.0 ** (a_weighting / 10.0)).sum(axis=-1), 1e-12))


# --- the sources ----------------------------------------------------------------------------


def traffic_sources(t: traffic.TrafficTables, table: dict, out: traffic.Outdoor, surroundings: dict,
                    days: list[DayType]) -> list[Source]:
    groups = {g: set(classes) for g, classes in table["traffic_groups"].items() if g != "about"}
    rank = {c: i for i, c in enumerate(c for g in ("highway", "arterial", "local_streets") for c in table["traffic_groups"][g])}
    by_name: dict[str, str] = {}
    for r in surroundings.get("roads", []):
        name = r.get("name") or f"unnamed {r['highway'].replace('_', ' ')}"
        if r["highway"] in rank and (name not in by_name or rank[r["highway"]] < rank[by_name[name]]):
            by_name[name] = r["highway"]
    weekend = table["traffic_days"]["weekend"]
    pct = np.asarray(weekend["pct"], dtype=float)
    weekend_weights = weekend["factor"] * 24.0 * pct / pct.sum()
    mean_energy = out.energy.mean(axis=0)  # (sources, bands), averaged over the walls
    labels = {"highway": "Highway (freeway lanes and ramps)", "arterial": "Main roads", "local_streets": "Local streets",
              "rail": "Freight trains"}
    members: dict[str, list[int]] = {k: [] for k in labels}
    for j, s in enumerate(out.sources):
        if s.kind == "rail":
            members["rail"].append(j)
            continue
        cls = by_name.get(s.name)
        group = next((g for g, classes in groups.items() if cls in classes), "local_streets")
        members[group].append(j)
    result = []
    for group, idx in members.items():
        if not idx:
            continue
        energy = np.zeros((len(days), 24, N_BANDS))
        for k, d in enumerate(days):
            for j in idx:
                s = out.sources[j]
                w = s.weights if (s.kind == "rail" or d.day == "weekday") else weekend_weights
                energy[k] += w[:, None] * mean_energy[j][None, :]
        roads = [out.sources[j] for j in idx]
        result.append(Source(
            source_id=group, label=labels[group], category="traffic", kind="steady",
            method="traffic.py: CNOSSOS-EU emission, ISO 9613-2 propagation" + (", FTA rail" if group == "rail" else ""),
            energy=energy,
            details={"roads": [{"name": s.name, "aadt": None if s.aadt is None else int(s.aadt), "data": s.data}
                               for s in sorted(roads, key=lambda s: -(s.aadt or 0))[:6]]}))
    return result


def horn_source(t, table, receivers, obstacles, surroundings, stations, lat, lon, days, weekday_weights) -> Source:
    cfg = table["horns"]
    points, vkm = horn_points(t, cfg, surroundings, stations, lat, lon)
    level_1m = cfg["level_7m_dba"] + 20.0 * np.log10(7.0)
    e, nearest = at_house(t, receivers, obstacles, points, np.tile(band_power(t, cfg["shape_db"], level_1m), (len(points), 1)),
                          cfg["height_m"])
    weekend = table["traffic_days"]["weekend"]
    pct = np.asarray(weekend["pct"], dtype=float)
    weights = {"weekday": weekday_weights, "weekend": weekend["factor"] * 24.0 * pct / pct.sum()}
    per_day = cfg["per_1000_vehicle_km"] * vkm / 1000.0  # honks per day from each piece
    events = np.array([[per_day * weights[d.day][h] / 24.0 for h in range(24)] for d in days])  # (D, 24, N)
    energy = np.einsum("dhn,nb->dhb", events, e) * cfg["duration_s"] / 3600.0
    return Source(source_id="horns", label=cfg["label"], category="traffic", kind="events",
                  method="events: honks per vehicle-km on signalled roads x hourly traffic, ISO 9613-2 propagation",
                  energy=energy, events=events, lmax_dba=dba(nearest, t.a_weighting),
                  details={"assumption": f"{cfg['per_1000_vehicle_km']} honks per 1000 vehicle-km",
                           "vehicle_km_per_day": round(float(vkm.sum()))})


def dog_source(t, table, receivers, obstacles, twin, buildings, days) -> Source:
    cfg = table["dogs"]
    points = yard_points(twin, buildings, cfg["radius_m"])
    e, nearest = at_house(t, receivers, obstacles, points,
                          np.tile(band_power(t, cfg["shape_db"], cfg["level_1m_dba"]), (len(points), 1)), cfg["height_m"])
    bouts = np.asarray(cfg["bouts_per_dog_hour"], dtype=float) * cfg["dogs_per_house"] / 2.0  # two yard spots a house
    events = np.broadcast_to(bouts[None, :, None], (len(days), 24, len(points))).copy()
    energy = np.einsum("dhn,nb->dhb", events, e) * cfg["bout_s"] / 3600.0
    return Source(source_id="dogs", label=cfg["label"], category="neighbourhood", kind="events",
                  method="events: barking bouts per dog x dogs per house, at neighbouring OSM houses",
                  energy=energy, events=events, lmax_dba=dba(nearest, t.a_weighting),
                  details={"houses": int(len(points) // 2), "dogs": round(len(points) / 2 * cfg["dogs_per_house"], 1)})


def biophony_sources(t, table, receivers, obstacles, twin, habitat, buildings, species, days) -> list[Source]:
    groups = table["biophony"]
    radius = max(g["radius_m"] for g in groups.values())
    points, classes, cell_area = habitat_grid(twin, habitat, buildings, table["habitat"], radius)
    dist = np.hypot(points[:, 0], points[:, 1])
    inat = {"birds": "birds", "frogs": "frogs", "cicadas": "cicadas", "night_insects": "orthoptera"}
    out = []
    for group, cfg in groups.items():
        density = np.array([cfg["density_per_ha"].get(c, cfg["density_per_ha"][YARD]) for c in classes]) / 1e4
        use = (density > 0) & (dist <= cfg["radius_m"])
        power = (density[use] * cell_area * cfg["peak_duty"])[:, None] * band_power(t, cfg["shape_db"], cfg["level_1m_dba"])[None, :]
        full = at_house(t, receivers, obstacles, points[use], power, cfg["height_m"])[0].sum(axis=0)  # (bands)
        share = np.array([activity(group, cfg, d) for d in days])  # (D, 24)
        seen = species.get(inat[group], {}) if species else {}
        out.append(Source(
            source_id=group, label=cfg["label"], category="nature", kind="steady",
            method="area sources on an OSM habitat grid: density x 1 m level x activity (sun, season, temperature"
                   + (", rain" if "rain" in cfg else "") + ")",
            energy=share[:, :, None] * full[None, None, :],
            details={"full_chorus_dba": round(float(dba(full, t.a_weighting)), 1),
                     "callers_in_range": round(float((density[use] * cell_area).sum()), 1),
                     "habitat_m2": {c: int((classes[use] == c).sum() * cell_area) for c in sorted(set(classes[use]))},
                     "species_nearby": seen.get("species"),
                     "observations_nearby": seen.get("observations"),
                     "common_species": [s["name"] for s in seen.get("top", [])[:8]]}))
    return out


# --- the dataset ----------------------------------------------------------------------------


def soundscape(twin: PropertyTwin, t: traffic.TrafficTables, table: dict, out: traffic.Outdoor, surroundings: dict,
               stations: list[dict], habitat: dict, species: dict, weather: dict, year: int) -> dict:
    days = day_types(table, twin.lat, twin.lon, twin.timezone, weather, year)
    receivers = out.receivers
    obstacles = traffic.obstacles(twin, surroundings, t)
    buildings = surroundings.get("buildings", [])
    sources = traffic_sources(t, table, out, surroundings, days)
    sources.append(horn_source(t, table, receivers, obstacles, surroundings, stations, twin.lat, twin.lon, days,
                               t.hourly * 24.0))
    sources.append(dog_source(t, table, receivers, obstacles, twin, buildings, days))
    sources += biophony_sources(t, table, receivers, obstacles, twin, habitat, buildings, species, days)
    sources.append(Source(source_id="residual", label=f"Residual ({out.background_label})", category="background",
                          kind="steady", method="ANSI/ASA S12.9-2013 Part 3 typical day/night level",
                          energy=np.broadcast_to(out.background[None], (len(days), 24, N_BANDS)).copy()))
    return assemble(days, sources, t.a_weighting)


def assemble(days: list[DayType], sources: list[Source], a_weighting: np.ndarray) -> dict:
    steady = sum(s.energy for s in sources if s.kind == "steady")
    total = sum(s.energy for s in sources)
    steady_dba = dba(steady, a_weighting)  # (D, 24)
    levels = {}
    for k, d in enumerate(days):
        per_source = {}
        for s in sources:
            entry = {"laeq_dba": _r(np.maximum(dba(s.energy[k], a_weighting), 0.0)),
                     "bands_db": [_r(np.maximum(10.0 * np.log10(np.maximum(b, 1e-12)), 0.0)) for b in s.energy[k]]}
            if s.kind == "events":
                rate = s.events[k]  # (24, N)
                louder = s.lmax_dba[None, :] > steady_dba[k][:, None]
                entry["events_per_hour"] = _r(rate.sum(axis=1), 2)
                entry["audible_per_hour"] = _r((rate * louder).sum(axis=1), 2)
            per_source[s.source_id] = entry
        levels[d.id] = {"total_laeq_dba": _r(dba(total[k], a_weighting)), "steady_laeq_dba": _r(steady_dba[k]),
                        "sources": per_source}
    meta = []
    for s in sources:
        item = {"source_id": s.source_id, "label": s.label, "category": s.category, "kind": s.kind, "method": s.method,
                **s.details}
        if s.kind == "events" and s.lmax_dba is not None and len(s.lmax_dba):
            weights = s.events.sum(axis=(0, 1))
            item["event_lmax_dba"] = {"median": round(_weighted_quantile(s.lmax_dba, weights, 0.5), 1),
                                      "p90": round(_weighted_quantile(s.lmax_dba, weights, 0.9), 1)}
        meta.append(item)
    return {
        "day_types": [{"id": d.id, "season": d.season, "day": d.day, "date": d.date,
                       "sunrise": _clock(d.sunrise_h), "sunset": _clock(d.sunset_h),
                       "temperature_c": _r(d.temperature_c), "evenings_after_rain": round(d.rain_share, 2)} for d in days],
        "sources": meta,
        "levels": levels,
    }


def _weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    if not len(values) or weights.sum() <= 0:
        return 0.0
    order = np.argsort(values)
    cum = np.cumsum(weights[order]) / weights.sum()
    return float(values[order][min(np.searchsorted(cum, q), len(values) - 1)])


def _clock(h: float) -> str:
    m = int(round(h * 60)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"


def _r(values, digits: int = 1) -> list[float]:
    return [round(float(v), digits) for v in np.asarray(values).ravel()]
