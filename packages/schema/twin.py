"""PropertyTwin (WORKSTREAMS.md section 2.1). Owner: C. Consumers: A, B.

Frames and units follow the global invariants: interior frame = Showcase SDK frame (right-handed,
Y up, meters, I-G9); world frame = local ENU at (lat, lon) (I-G10); compass angles clockwise from
true north (I-G11).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Vec3(BaseModel):
    x: float
    y: float
    z: float


class TwinSources(BaseModel):
    matterport_model_id: str
    vexcel_parcel_id: str | None = None
    costar_listing_id: str | None = None


class Floor(BaseModel):
    floor_id: str
    index: int
    elevation_m: float
    label: str | None = None


class Room(BaseModel):
    room_id: str
    floor_id: str
    label: str
    polygon: list[tuple[float, float]]  # (x, z) in interior frame, CCW seen from above, not closed
    floor_y: float
    height_m: float
    floor_material: str  # key in the shared absorption table
    area_m2: float | None = None


class Opening(BaseModel):
    opening_id: str
    room_id: str
    kind: Literal["window", "door", "slider"]
    polygon: list[Vec3]  # interior frame
    facade_azimuth_deg: float  # outward normal, away from room_id (I-G11)
    glazing: Literal["none", "single", "double", "triple"]
    stc: int | None = None
    other_room_id: str | None = None  # the room on the far side of an interior door


class Sweep(BaseModel):
    sweep_id: str
    floor_id: str
    room_id: str | None
    position: Vec3  # interior frame
    label: str | None = None


class TwinAssets(BaseModel):
    interior_glb: str | None = None
    context_glb: str | None = None
    ortho: str | None = None
    obliques: list[str] = Field(default_factory=list)


class TwinQuality(BaseModel):
    # "georef": Matterport's own georeference (heading + offset); "icp" refines it against Vexcel.
    alignment: Literal["icp", "georef", "gps_only"]
    openings: Literal["annotated", "detected", "none"]
    context: Literal["dsm", "none"]
    traffic: Literal["aadt", "class_default", "none"]


class PropertyTwin(BaseModel):
    property_id: str
    version: int  # bumps only when ingestion output changes
    sources: TwinSources
    address: str
    lat: float
    lon: float
    timezone: str  # IANA, e.g. "America/Chicago"
    interior_to_world: list[float] = Field(min_length=16, max_length=16)  # column-major (I-G10)
    floors: list[Floor]
    rooms: list[Room]
    openings: list[Opening]
    sweeps: list[Sweep]
    assets: TwinAssets
    quality: TwinQuality
