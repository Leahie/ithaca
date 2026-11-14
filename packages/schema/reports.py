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


# Outdoor noise (traffic, rail, background) heard inside. Hours are property-local 0..23. Day is
# 07-23 and night 23-07 (WHO); Lden weights evening (19-23) +5 dB and night +10 dB.

TrafficData = Literal["aadt", "class_default", "none"]


class NoiseScenario(BaseModel):
    glazing: Literal["single", "double", "triple"] | None = None  # None = as built
    add_carpet: bool = False
    include_furniture: bool = True
    windows_open: bool = False  # windows and sliders open halfway


class NoiseHour(BaseModel):
    hour: int
    laeq_dba: float
    bands_db: list[float]  # [63, 125, 250, 500, 1000, 2000, 4000, 8000] Hz (I-G14)


class NoiseSource(BaseModel):
    name: str
    contribution_dba: float  # LAeq,24h this source alone gives in the room
    kind: Literal["road", "rail", "background"] | None = None
    outdoor_dba: float | None = None  # LAeq,24h it gives outside the room's most exposed wall


class NoiseReport(BaseModel):
    room_id: str
    scenario: NoiseScenario
    hourly: list[NoiseHour]  # 24 entries
    rt60_s: float
    sources: list[NoiseSource]  # loudest first
    laeq_24h_dba: float | None = None
    laeq_day_dba: float | None = None
    laeq_night_dba: float | None = None
    outdoor_hourly: list[NoiseHour] = []  # just outside the room's most exposed wall
    outdoor_lden_dba: float | None = None
    outdoor_lnight_dba: float | None = None
    traffic: TrafficData = "none"  # where road volumes came from (counts or class defaults)
    caveats: list[str] = []


class NoiseRoomHour(BaseModel):
    room_id: str
    laeq_dba: float
    bands_db: list[float]
    outdoor_laeq_dba: float  # outside the room's most exposed wall; 0 for rooms with no outside wall
    outdoor_bands_db: list[float]  # the same, per band (all 0 with no outside wall)
    rt60_s: float


class NoiseHouse(BaseModel):
    """Every room at one hour: the floor-plan map, and the audio as you walk around."""

    hour: int
    scenario: NoiseScenario
    rooms: list[NoiseRoomHour]  # loudest first
    traffic: TrafficData
    caveats: list[str] = []


class OutdoorSource(BaseModel):
    source_id: str
    name: str
    kind: Literal["road", "rail", "background"]
    data: Literal["txdot_aadt", "class_default", "fta_default", "ansi_category"]
    aadt: int | None = None  # vehicles/day, both directions
    heavy_share: float | None = None  # trucks
    speed_kmh: float | None = None
    distance_m: float | None = None  # nearest point to the property origin
    facade_laeq_dba: float  # LAeq,24h outside the most exposed wall, this source alone
    path: list[list[tuple[float, float]]] = []  # (lat, lon) polylines


class NoiseSources(BaseModel):
    sources: list[OutdoorSource]  # loudest first
    traffic: TrafficData
    caveats: list[str] = []


# Sound placed inside the house ("a TV in the den: what do I hear in the bedroom?"). Every
# bands list is the 8 octave bands of I-G14. Levels are dB re 20 uPa.

SoundDoors = Literal["closed", "open"]  # the hinged interior doors; passages are always open


class SoundPreset(BaseModel):
    preset_id: str
    label: str
    level_dba: float  # free field, 1 m from the source
    bands_db: list[float]  # at 1 m


class SoundCatalog(BaseModel):
    presets: list[SoundPreset]
    loudness_dba: dict[str, float]  # choices for the user's own recording, e.g. {"talking": 60}


class SoundSource(BaseModel):
    room_id: str
    position: Vec3  # interior frame
    preset_id: str
    level_dba: float  # at 1 m
    bands_db: list[float]  # at 1 m


class SoundRoom(BaseModel):
    room_id: str
    laeq_dba: float  # the room's diffuse (reverberant) level; uniform across the room
    bands_db: list[float]
    # Per band, this room's level minus the source's level at 1 m. For auralization: play a clip
    # recorded about 1 m from the source through these band gains and this room's reverb.
    gain_db: list[float]
    rt60_s: float  # mean of 500 and 1000 Hz
    rt60_bands_s: list[float]


class SoundListener(BaseModel):
    room_id: str
    position: Vec3
    distance_m: float | None  # to the source, when in the source's room
    laeq_dba: float  # direct + reverberant
    bands_db: list[float]
    gain_db: list[float]
    direct_fraction: float  # share of the energy (A-weighted) arriving directly: the dry/wet mix
    rt60_s: float


class SoundField(BaseModel):
    source: SoundSource
    doors: SoundDoors
    include_furniture: bool
    rooms: list[SoundRoom]  # every room, loudest first
    listener: SoundListener | None = None


AmenityCategory = Literal[
    "grocery", "school", "park", "playground", "restaurant", "cafe", "transit", "pharmacy",
    "health", "fitness", "library", "worship", "shopping",
]


class Amenity(BaseModel):
    name: str
    category: str  # an AmenityCategory
    lat: float
    lon: float
    distance_m: float  # straight line from the property
    source: Literal["vexcel", "costar", "osm"]
    kind: str | None = None  # the source's own tag, e.g. "supermarket", "bus_stop"


CompStatus = Literal["active", "pending", "sold"]
# Texas does not disclose sale prices, so a Texas sold home shows its last asking price.
PriceKind = Literal["sale", "asking", "last_asking"]


class Comp(BaseModel):
    address: str
    price: int
    sqft: int | None = None
    beds: int | None = None
    sold_date: date | None = None
    distance_m: float
    status: CompStatus | None = None
    price_kind: PriceKind | None = None
    baths: float | None = None
    year_built: int | None = None
    price_per_sqft: float | None = None
    lat: float | None = None
    lon: float | None = None
    listing_id: str | None = None  # CoStar residential listing id


class ListingFacts(BaseModel):
    """The property's own listing (CoStar residential listings)."""

    listing_id: str
    status: CompStatus | Literal["off_market"]
    price: int | None = None
    price_kind: PriceKind | None = None
    list_date: date | None = None
    days_on_market: int | None = None
    sqft: int | None = None
    beds: int | None = None
    baths: float | None = None
    year_built: int | None = None
    lot_acres: float | None = None
    hoa_monthly: float | None = None
    subdivision: str | None = None
    neighborhood: str | None = None
    zip: str | None = None
    highlights: list[str] = []


class AreaProfile(BaseModel):
    markdown: str
    amenities: list[Amenity]  # the nearest few per category
    comps: list[Comp]  # most comparable first
    listing: ListingFacts | None = None
    caveats: list[str] = []  # data gaps the agent should mention (C3)
