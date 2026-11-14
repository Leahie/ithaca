"""Twin types. Owner: C. Consumers: A, B."""
from __future__ import annotations

from typing import Literal

from .common import Model, Vec3


class TwinSources(Model):
    matterport_model_id: str
    vexcel_parcel_id: str | None = None
    costar_listing_id: str | None = None


class Floor(Model):
    floor_id: str
    index: int
    elevation_m: float


class Room(Model):
    room_id: str
    floor_id: str
    label: str
    # (x, z) in the interior frame, CCW seen from above, not closed.
    polygon: list[tuple[float, float]]
    floor_y: float
    height_m: float
    # Key in the shared absorption table.
    floor_material: str


OpeningKind = Literal["window", "door", "slider"]
Glazing = Literal["none", "single", "double", "triple"]


class Opening(Model):
    opening_id: str
    room_id: str
    kind: OpeningKind
    # Interior frame.
    polygon: list[Vec3]
    # I-G11: degrees clockwise from true north, direction of the outward normal.
    facade_azimuth_deg: float
    glazing: Glazing
    stc: int | None = None


class Sweep(Model):
    sweep_id: str
    floor_id: str
    room_id: str | None = None
    position: Vec3


class TwinAssets(Model):
    """URLs, resolved by the API."""

    interior_glb: str
    context_glb: str | None = None
    ortho: str | None = None
    obliques: list[str] = []


class TwinQuality(Model):
    alignment: Literal["icp", "gps_only"]
    openings: Literal["annotated", "detected", "none"]
    context: Literal["dsm", "none"]
    traffic: Literal["aadt", "class_default", "none"]


class PropertyTwin(Model):
    property_id: str
    # Bumps only when ingestion output changes.
    version: int
    sources: TwinSources
    address: str
    lat: float
    lon: float
    # IANA, e.g. "America/Chicago".
    timezone: str
    # 16 floats, column-major (I-G10).
    interior_to_world: list[float]
    floors: list[Floor]
    rooms: list[Room]
    openings: list[Opening]
    sweeps: list[Sweep]
    assets: TwinAssets
    quality: TwinQuality
