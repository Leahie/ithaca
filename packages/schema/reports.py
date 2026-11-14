"""Report types. Owner: C. Consumers: A, B."""
from __future__ import annotations

from datetime import date
from typing import Literal

from .common import Model


class SunDay(Model):
    date: date
    direct_sun_hours: float


class SunHeatmap(Model):
    cell_m: float
    origin_xz: tuple[float, float]
    # hours[row][col]; row steps along +z, col along +x, from origin_xz.
    hours: list[list[float]]


class SunReport(Model):
    room_id: str
    start: date
    end: date
    daily: list[SunDay]
    heatmap: SunHeatmap | None = None


class NoiseScenario(Model):
    # None = as built.
    glazing: Literal["single", "double", "triple"] | None = None
    add_carpet: bool = False
    include_furniture: bool = True


class NoiseHour(Model):
    hour: int
    laeq_dba: float
    # 8 octave bands, 63 Hz .. 8 kHz (I-G14).
    bands_db: list[float]


class NoiseSource(Model):
    name: str
    contribution_dba: float


class NoiseReport(Model):
    room_id: str
    scenario: NoiseScenario
    # 24 entries.
    hourly: list[NoiseHour]
    rt60_s: float
    sources: list[NoiseSource]


class Amenity(Model):
    name: str
    category: str
    lat: float
    lon: float
    distance_m: float
    source: Literal["vexcel", "costar", "osm"]


class Comp(Model):
    address: str
    price: int
    sqft: int | None = None
    beds: int | None = None
    sold_date: date | None = None
    distance_m: float


class AreaProfile(Model):
    markdown: str
    amenities: list[Amenity]
    comps: list[Comp]
