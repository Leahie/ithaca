"""In-process interface C -> B (WORKSTREAMS.md section 2.5). Agent tools call these, never
integrations/* directly. get_twin serves the twin that ingestion wrote.
"""

from __future__ import annotations

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
    NoiseReport,
    NoiseScenario,
    SkySource,
    SunMoment,
    SunReport,
    SunRoomDay,
    SunSpot,
)
from packages.schema.twin import PropertyTwin
from services.api.integrations import nsrdb
from services.api.integrations.config import IntegrationNotConfigured, fixtures_dir
from services.api.sim import daylight, sun


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
    """Set up every room's sun and daylight geometry in a background thread, so the first question
    at these heights is fast. Safe to call often: it runs once per twin version."""
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

    threading.Thread(target=run, daemon=True, name=f"warm-{property_id}").start()


def _check_spot(property_id: str, room_id: str, x: float, z: float) -> None:
    room = next((r for r in get_twin(property_id).rooms if r.room_id == room_id), None)
    if room is None:
        raise KeyError(f"no room {room_id} in {property_id}")
    if not Polygon(room.polygon).buffer(0.05).contains(Point(x, z)):
        raise ValueError(f"({x}, {z}) is not inside room {room_id}")


# Not built yet. They raise IntegrationNotConfigured (I-G21) so agent tools can say so.


def noise_report(property_id: str, room_id: str, scenario: NoiseScenario) -> NoiseReport:
    raise IntegrationNotConfigured("noise", "the noise engine (services/api/sim/noise.py, not built yet)")


def area_profile(property_id: str) -> AreaProfile:
    raise IntegrationNotConfigured("area", "the CoStar area profile (not built yet)")


def area_search(property_id: str, query: str, limit: int = 10) -> list[Amenity]:
    raise IntegrationNotConfigured("area", "the amenity search (not built yet)")


def get_comps(property_id: str) -> list[Comp]:
    raise IntegrationNotConfigured("costar", "the CoStar comps lookup (not built yet)")


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
