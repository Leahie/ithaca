"""In-process interface C -> B (WORKSTREAMS.md section 2.5). Agent tools call these, never
integrations/* directly. get_twin serves the twin that ingestion wrote.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import pvlib
from shapely.geometry import Point, Polygon

from packages.schema.reports import (
    Amenity,
    AreaProfile,
    Comp,
    DaylightMoment,
    DaylightSpot,
    NoiseHouse,
    NoiseReport,
    NoiseScenario,
    NoiseSources,
    SkySource,
    SoundDoors,
    SoundField,
    SoundPreset,
    SunMoment,
    SunReport,
    SunRoomDay,
    SunSpot,
    TrafficData,
)
from packages.schema.twin import PropertyTwin
from services.api.integrations import costar, nsrdb, osm, roads
from services.api.integrations.cache import FixtureMissing
from services.api.integrations.config import IntegrationNotConfigured, fixtures_dir
from services.api.sim import daylight, noise, sun, traffic
from services.api.twin import area


class TwinNotFound(LookupError):
    pass


def twin_path(property_id: str) -> Path:
    return fixtures_dir() / "twin" / f"{property_id}.json"


def get_twin(property_id: str) -> PropertyTwin:
    path = twin_path(property_id)
    if not path.exists():
        raise TwinNotFound(
            f"no twin for {property_id}; run: python -m services.api.twin.ingest {property_id}"
        )
    return PropertyTwin.model_validate_json(path.read_text(encoding="utf-8"))


# Sun and daylight answers are cached per twin version (C8). Times are property-local (twin.timezone).


def sun_at(property_id: str, when: datetime | str, height_m: float = 0.0) -> SunMoment:
    """Where direct sun is inside the house at one instant ("is the sun in the bedroom at 9am?")."""
    return _sun_at(property_id, get_twin(property_id).version, str(when), height_m)


def sun_spot(property_id: str, room_id: str, x: float, z: float, day: date,
             height_m: float = sun.SEATED_M) -> SunSpot:
    """When direct sun reaches one spot ("when does the sun hit me at the desk?")."""
    _check_spot(property_id, room_id, x, z)
    return _sun_spot(property_id, get_twin(property_id).version, room_id, x, z, day, height_m)


def sun_room_day(property_id: str, room_id: str, day: date, height_m: float = 0.0) -> SunRoomDay:
    """When direct sun reaches any part of a room on one day."""
    return _sun_room_day(property_id, get_twin(property_id).version, room_id, day, height_m)


def sun_report(property_id: str, room_id: str, start: date, end: date, height_m: float = 0.0) -> SunReport:
    """Direct-sun hours per day over a date range, plus a mean-hours heatmap."""
    return _sun_report(property_id, get_twin(property_id).version, room_id, start, end, height_m)


def daylight_at(property_id: str, when: datetime | str, sky: SkySource = "clear", height_m: float = 0.0,
                room_id: str | None = None, grids: bool = True) -> DaylightMoment:
    """How bright each room is at one instant (sky + sun + reflected light, in lux).

    sky="clear" is a clear day; sky="typical" uses the NSRDB typical-year weather for that date
    and hour (needs NREL_API_KEY and NREL_EMAIL the first time). room_id limits it to one room."""
    return _daylight_at(property_id, get_twin(property_id).version, str(when), sky, height_m, room_id, grids)


def daylight_spot(property_id: str, room_id: str, x: float, z: float, day: date, sky: SkySource = "clear",
                  height_m: float = sun.SEATED_M, step_min: int = 15) -> DaylightSpot:
    """How bright one spot is through one day, with the moments it is in direct sun."""
    _check_spot(property_id, room_id, x, z)
    return _daylight_spot(property_id, get_twin(property_id).version, room_id, x, z, day, sky, height_m, step_min)


_warmed: set[tuple[str, int]] = set()
_warm_lock = threading.Lock()


def warm(property_id: str, heights: tuple[float, ...] = (0.0, sun.SEATED_M)) -> None:
    """Set up every room's sun and daylight geometry, then the outdoor noise model, in a background
    thread, so the first question is fast. Safe to call often: it runs once per twin version."""
    twin = get_twin(property_id)
    with _warm_lock:
        if (property_id, twin.version) in _warmed:
            return
        _warmed.add((property_id, twin.version))

    def run():
        for h in heights:
            for room in twin.rooms:
                if sun.room_windows(twin, room.room_id):
                    sun.room_rays(twin, room, height_m=h)
                    daylight.room_coefficients(twin, room, h)
        _outdoor(property_id, twin.version)

    threading.Thread(target=run, daemon=True, name=f"warm-{property_id}").start()


def _check_spot(property_id: str, room_id: str, x: float, z: float) -> None:
    room = next((r for r in get_twin(property_id).rooms if r.room_id == room_id), None)
    if room is None:
        raise KeyError(f"no room {room_id} in {property_id}")
    if not Polygon(room.polygon).buffer(0.05).contains(Point(x, z)):
        raise ValueError(f"({x}, {z}) is not inside room {room_id}")


# Sound: a sound placed inside the house, and noise from outside it.


def sound_presets() -> list[SoundPreset]:
    """Sounds a user can place (level at 1 m and spectrum), plus loudness choices for own recordings."""
    return list(acoustics().presets.values())


def sound_field(property_id: str, room_id: str, x: float, z: float, preset_id: str = "tv",
                level_dba: float | None = None, doors: SoundDoors = "closed", include_furniture: bool = True,
                listener: tuple[str, float, float] | None = None) -> SoundField:
    """A sound placed at (x, z) in a room: what every room hears, and optionally one listener spot
    (room_id, x, z) with the direct sound added when it is in the source's room."""
    _check_spot(property_id, room_id, x, z)
    if listener is not None:
        _check_spot(property_id, *listener)
    return _sound_field(property_id, get_twin(property_id).version, room_id, x, z, preset_id, level_dba,
                        doors, include_furniture, listener)


@lru_cache(maxsize=1)
def acoustics() -> noise.Acoustics:
    path = fixtures_dir() / "tables" / "acoustics.json"
    return noise.load_acoustics(json.loads(path.read_text(encoding="utf-8")))


@lru_cache(maxsize=64)
def _sound_network(property_id, version, doors, include_furniture, add_carpet=False, glazing=None,
                   windows_open=False) -> noise.Network:
    return noise.network(get_twin(property_id), acoustics(), doors=doors, include_furniture=include_furniture,
                         add_carpet=add_carpet, glazing=glazing, windows_open=windows_open)


@lru_cache(maxsize=1024)
def _sound_field(property_id, version, room_id, x, z, preset_id, level_dba, doors, include_furniture,
                 listener) -> SoundField:
    twin, table = get_twin(property_id), acoustics()
    preset = noise.scaled_preset(table, preset_id, level_dba)
    net = _sound_network(property_id, version, doors, include_furniture)
    source, rooms, heard = noise.sound_field(twin, net, table, preset, room_id, x, z, listener)
    return SoundField(source=source, doors=doors, include_furniture=include_furniture, rooms=rooms, listener=heard)


def noise_report(property_id: str, room_id: str, scenario: NoiseScenario | None = None) -> NoiseReport:
    """How loud a room is through the day from traffic, rail and background outside: 24 hourly
    levels (dBA and octave bands), day/night averages, what it's made of, and the level just
    outside its most exposed wall. scenario: glazing, carpet, furniture, windows open."""
    scenario = scenario or NoiseScenario()
    twin = get_twin(property_id)
    out, data, caveats = _outdoor(property_id, twin.version)
    net = _scenario_network(property_id, twin.version, scenario)
    return noise.noise_report(twin, net, acoustics(), out, room_id, scenario, data, list(caveats))


def noise_house(property_id: str, hour: int, scenario: NoiseScenario | None = None) -> NoiseHouse:
    """Every room's level from outside at one local hour (for the floor-plan map and walking audio)."""
    scenario = scenario or NoiseScenario()
    twin = get_twin(property_id)
    out, data, caveats = _outdoor(property_id, twin.version)
    net = _scenario_network(property_id, twin.version, scenario)
    return noise.noise_house(twin, net, acoustics(), out, hour, scenario, data, list(caveats))


def noise_sources(property_id: str) -> NoiseSources:
    """The roads and railways around the house, their traffic, and how loud each is at the house."""
    twin = get_twin(property_id)
    out, data, caveats = _outdoor(property_id, twin.version)
    return noise.noise_sources(acoustics(), out, data, list(caveats))


@lru_cache(maxsize=1)
def traffic_tables() -> traffic.TrafficTables:
    path = fixtures_dir() / "tables" / "traffic.json"
    return traffic.load_traffic(json.loads(path.read_text(encoding="utf-8")), acoustics().a_weighting)


def _scenario_network(property_id: str, version: int, scenario: NoiseScenario) -> noise.Network:
    return _sound_network(property_id, version, "closed", scenario.include_furniture, scenario.add_carpet,
                          scenario.glazing, scenario.windows_open)


_outdoor_lock = threading.Lock()


def _outdoor(property_id: str, version: int) -> tuple[traffic.Outdoor, TrafficData, tuple[str, ...]]:
    with _outdoor_lock:  # one build per version, even when warm() and a request race
        return _outdoor_cached(property_id, version)


@lru_cache(maxsize=8)
def _outdoor_cached(property_id: str, version: int) -> tuple[traffic.Outdoor, TrafficData, tuple[str, ...]]:
    twin, t = get_twin(property_id), traffic_tables()
    caveats = []
    surroundings, stations = None, []
    try:
        surroundings = roads.surroundings(twin.lat, twin.lon)
    except (IntegrationNotConfigured, RuntimeError) as e:
        caveats.append(f"No road or building data ({e}); only background noise is modelled.")
    if surroundings is not None:
        try:
            stations = roads.aadt_counts(twin.lat, twin.lon, twin.address)
        except (IntegrationNotConfigured, RuntimeError) as e:
            caveats.append(f"No traffic counts ({e}); road volumes are class defaults.")
    sources = []
    if surroundings is not None:
        sources = traffic.road_sources(t, surroundings, stations, twin.lat, twin.lon)
        sources += traffic.rail_sources(t, surroundings, twin.lat, twin.lon)
    out = traffic.outdoor(twin, t, sources, surroundings)
    roads_used = [s for s in sources if s.kind == "road"]
    counted = [s.name for s in roads_used if s.data == "txdot_aadt"]
    data: TrafficData = "aadt" if counted else "class_default" if roads_used else "none"
    if counted:
        caveats.append(f"Traffic counts (TxDOT AADT) for {', '.join(sorted(counted))}; other roads use "
                       "typical volumes for their class. Hours follow a typical weekday.")
    elif roads_used:
        caveats.append("No traffic counts here: road volumes are typical for each road class.")
    if any(s.kind == "rail" for s in sources):
        trains = int(t.rail["trains_day"] + t.rail["trains_night"])
        caveats.append(f"Rail uses default freight traffic ({trains} trains a day), not counts; horns at "
                       "crossings are not included.")
    caveats.append(f"Background ({t.background_category}) stands for unmodelled sound: distant city, AC "
                   "units, insects. Aircraft are not modelled.")
    caveats.append("Neighbouring buildings come from OpenStreetMap, which misses some houses; a built-up "
                   "allowance covers the rest. Expect about +/-3 dB.")
    return out, data, tuple(caveats)


def area_profile(property_id: str) -> AreaProfile:
    """The home's own listing, comparable homes and nearby amenities, with a markdown summary.
    Missing sources don't fail it: the gaps are listed in `caveats` (C3)."""
    return _area_profile(property_id, get_twin(property_id).version)


def area_search(property_id: str, query: str, limit: int = 10) -> list[Amenity]:
    """Amenities near the home matching a free-text query ("coffee", "bus stop", "H-E-B"), nearest first."""
    twin = get_twin(property_id)
    _, _, pois, _ = _area_inputs(property_id, twin.version)
    return area.search(area.amenities(twin, pois or []), query, limit)


def get_comps(property_id: str) -> list[Comp]:
    """Comparable homes nearby, most comparable first."""
    return area_profile(property_id).comps


@lru_cache(maxsize=32)
def _area_profile(property_id: str, version: int) -> AreaProfile:
    subject, nearby, pois, caveats = _area_inputs(property_id, version)
    return area.build_profile(get_twin(property_id), subject, nearby, pois, list(caveats))


@lru_cache(maxsize=32)
def _area_inputs(property_id: str, version: int) -> tuple[dict | None, list | None, list | None, tuple[str, ...]]:
    twin = get_twin(property_id)
    caveats = []
    subject = nearby = pois = None
    listing_id = twin.sources.costar_listing_id
    try:
        subject = costar.home_listing(listing_id) if listing_id else None
        nearby = costar.nearby_listings(twin.lat, twin.lon, area.COMP_RADIUS_M)
    except (IntegrationNotConfigured, FixtureMissing) as e:
        caveats.append(f"No CoStar listing data: {e}.")
    try:
        pois = osm.points_of_interest(twin.lat, twin.lon, area.AMENITY_RADIUS_M)
    except (IntegrationNotConfigured, RuntimeError) as e:
        caveats.append(f"No amenity data: {e}.")
    return subject, nearby, pois, tuple(caveats)


def weather(property_id: str, times: pd.DatetimeIndex, sky: SkySource) -> daylight.Weather:
    twin = get_twin(property_id)
    if sky == "typical":
        tmy, offset = nsrdb.typical_year(twin.lat, twin.lon)
        return daylight.typical_weather(tmy, times, offset)
    return daylight.clear_sky(twin, times, pvlib.clearsky.lookup_linke_turbidity(times, twin.lat, twin.lon))


@lru_cache(maxsize=1024)
def _daylight_at(property_id, version, when, sky, height_m, room_id, grids) -> DaylightMoment:
    twin = get_twin(property_id)
    if room_id is not None and all(r.room_id != room_id for r in twin.rooms):
        raise KeyError(f"no room {room_id} in {property_id}")
    times = daylight.single_time(when, twin.timezone)
    return daylight.daylight_at(twin, weather(property_id, times, sky), height_m=height_m,
                                room_ids=None if room_id is None else [room_id], with_grids=grids)


@lru_cache(maxsize=256)
def _daylight_spot(property_id, version, room_id, x, z, day, sky, height_m, step_min) -> DaylightSpot:
    twin = get_twin(property_id)
    times = sun.day_times(day, twin.timezone, step_min)
    pos = sun.sun_positions(times, twin.lat, twin.lon)
    times = times[np.asarray(pos["apparent_elevation"] > 0)]
    return daylight.daylight_spot(twin, room_id, x, z, day, weather(property_id, times, sky), height_m=height_m)


@lru_cache(maxsize=512)
def _sun_at(property_id: str, version: int, when: str, height_m: float) -> SunMoment:
    return sun.sun_at(get_twin(property_id), when, height_m=height_m)


@lru_cache(maxsize=512)
def _sun_spot(property_id, version, room_id, x, z, day, height_m) -> SunSpot:
    return sun.sun_spot(get_twin(property_id), room_id, x, z, day, height_m=height_m)


@lru_cache(maxsize=512)
def _sun_room_day(property_id, version, room_id, day, height_m) -> SunRoomDay:
    return sun.sun_room_day(get_twin(property_id), room_id, day, height_m=height_m)


@lru_cache(maxsize=512)
def _sun_report(property_id, version, room_id, start, end, height_m) -> SunReport:
    return sun.sun_report(get_twin(property_id), room_id, start, end, height_m=height_m)
