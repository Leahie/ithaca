"""Reports computed from a PropertyTwin (WORKSTREAMS.md section 2.1). Owner: C. Consumers: A, B."""

from __future__ import annotations

import datetime as dt
from datetime import date
from typing import Literal

from pydantic import BaseModel

from packages.schema.twin import Vec3

# Sun questions are about a person in the room, so they take a sample height above the room's
# floor: 0 = the floor, about 1.1 = a seated person, about 1.6 = a standing person's head.


class SunInterval(BaseModel):
    start: dt.datetime  # ISO 8601 with offset, property timezone (I-G13)
    end: dt.datetime


class SunMask(BaseModel):
    cell_m: float
    origin_xz: tuple[float, float]  # same grid convention as SunHeatmap
    lit: list[list[bool]]


class SunPatch(BaseModel):
    room_id: str
    lit_area_m2: float
    lit_fraction: float
    through_opening_ids: list[str]  # the windows the sun is coming through
    mask: SunMask | None = None


class SunMoment(BaseModel):
    """Where direct sun is inside the house at one instant."""

    datetime: dt.datetime
    azimuth_deg: float  # clockwise from true north (I-G11)
    elevation_deg: float  # apparent (with refraction)
    sun_up: bool
    direction_interior: Vec3 | None  # unit vector toward the sun, interior frame
    height_m: float
    rooms: list[SunPatch]  # only rooms with direct sun at that height


class SunSpot(BaseModel):
    """When direct sun reaches one spot on one day."""

    room_id: str
    point: Vec3  # interior frame; y = room floor + height_m
    height_m: float
    date: date
    intervals: list[SunInterval]
    total_hours: float


class SunRoomDay(BaseModel):
    """When direct sun reaches any part of a room on one day."""

    room_id: str
    date: date
    height_m: float
    intervals: list[SunInterval]
    total_hours: float
    through_opening_ids: list[str]


SkySource = Literal["clear", "typical"]  # clear-sky model, or the NSRDB typical-year weather
BrightnessLevel = Literal["dark", "dim", "moderate", "bright", "very_bright"]


class LuxGrid(BaseModel):
    cell_m: float
    origin_xz: tuple[float, float]  # same grid convention as SunHeatmap
    lux: list[list[float | None]]  # horizontal illuminance; None outside the room


class RoomDaylight(BaseModel):
    room_id: str
    mean_lux: float
    median_lux: float
    max_lux: float
    reflected_lux: float  # inter-reflected part, uniform across the room (split-flux)
    sun_area_m2: float  # area in direct sun
    level: BrightnessLevel  # from median_lux
    grid: LuxGrid | None = None


class DaylightMoment(BaseModel):
    """Daylight (sky + sun + reflected) inside the house at one instant."""

    datetime: dt.datetime
    sky: SkySource
    azimuth_deg: float
    elevation_deg: float
    sun_up: bool
    dni_w_m2: float
    dhi_w_m2: float
    ghi_w_m2: float
    outdoor_lux: float  # global horizontal illuminance outside
    height_m: float
    rooms: list[RoomDaylight]


class DaylightSpot(BaseModel):
    """Daylight at one spot through one day."""

    room_id: str
    point: Vec3
    height_m: float
    date: date
    sky: SkySource
    times: list[dt.datetime]
    lux: list[float]
    direct_sun: list[bool]


class SunDay(BaseModel):
    date: date
    direct_sun_hours: float  # hours with direct sun on at least MIN_LIT_AREA_M2 of the room at height_m


class SunHeatmap(BaseModel):
    cell_m: float
    origin_xz: tuple[float, float]  # interior (x, z) of the grid's corner; cell [i][j] is centred at
    #                                 origin + ((j + 0.5) * cell_m, (i + 0.5) * cell_m)
    hours: list[list[float]]  # mean direct-sun hours per day; rows run along z, columns along x;
    #                           0 outside the room polygon


class SunReport(BaseModel):
    room_id: str
    start: date
    end: date
    daily: list[SunDay]
    heatmap: SunHeatmap | None
    height_m: float = 0.0


class NoiseScenario(BaseModel):
    glazing: Literal["single", "double", "triple"] | None = None  # None = as built
    add_carpet: bool = False
    include_furniture: bool = True


class NoiseHour(BaseModel):
    hour: int
    laeq_dba: float
    bands_db: list[float]  # [63, 125, 250, 500, 1000, 2000, 4000, 8000] Hz (I-G14)


class NoiseSource(BaseModel):
    name: str
    contribution_dba: float


class NoiseReport(BaseModel):
    room_id: str
    scenario: NoiseScenario
    hourly: list[NoiseHour]  # 24 entries
    rt60_s: float
    sources: list[NoiseSource]


class Amenity(BaseModel):
    name: str
    category: str
    lat: float
    lon: float
    distance_m: float
    source: Literal["vexcel", "costar", "osm"]


class Comp(BaseModel):
    address: str
    price: int
    sqft: int | None = None
    beds: int | None = None
    sold_date: date | None = None
    distance_m: float


class AreaProfile(BaseModel):
    markdown: str
    amenities: list[Amenity]
    comps: list[Comp]
