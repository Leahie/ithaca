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


class Quat(BaseModel):
    x: float
    y: float
    z: float
    w: float


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
    exterior: bool | None = None  # leads outside; False = interior (maybe unlabeled space); None = unknown
    leaf: bool | None = None  # doors: True = a hinged leaf that can close; False = open passage; None = unknown


class Wall(BaseModel):
    """A wall centerline from the floor plan. Sound and (later) the layout solver use these."""

    wall_id: str
    floor_id: str
    ends: tuple[tuple[float, float], tuple[float, float]]  # (x, z) centerline ends, interior frame
    thickness_m: float
    room_ids: list[str]  # the rooms it bounds: two for a partition, one for an outside wall
    exterior: bool  # one side is outside
    assembly: str  # construction, key in the shared acoustics table (fixtures/tables/acoustics.json)
    opening_ids: list[str] = Field(default_factory=list)


class FloorOpening(BaseModel):
    """An open stairwell or open-to-below space (Matterport "Void") joining rooms on two floors."""

    floor_opening_id: str  # the Matterport room id of the void
    floor_id: str  # the floor its outline is on
    polygon: list[tuple[float, float]]  # (x, z) in interior frame
    area_m2: float
    room_ids: list[str]  # rooms on the same floor that open onto it
    via_opening_ids: list[str]  # the passages between those rooms and the void
    other_floor_room_ids: list[str]  # rooms on the floor above or below that it overlaps


class Sweep(BaseModel):
    sweep_id: str
    floor_id: str
    room_id: str | None
    position: Vec3  # interior frame
    label: str | None = None
    # sweeps reachable along a clear straight line (symmetric); a viewer moves only along these
    neighbors: list[str] = Field(default_factory=list)
    # pano frame -> interior frame; the skybox faces are GL cube faces in the pano frame, so a
    # world direction d samples the panorama at inverse(rotation) * d
    rotation: Quat | None = None


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
    # "inferred": assemblies guessed from wall thickness and position (Matterport has no materials)
    walls: Literal["annotated", "inferred", "none"] = "none"


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
    walls: list[Wall] = Field(default_factory=list)
    floor_openings: list[FloorOpening] = Field(default_factory=list)
    sweeps: list[Sweep]
    assets: TwinAssets
    quality: TwinQuality
