"""Property routes (WORKSTREAMS.md section 2.4): twin, openings, sun, daylight, noise, area, ingest,
and the asset files the twin points to. Owner: C. Mounted under /api.

Times are ISO 8601 in the property's timezone (I-G13); a time without an offset is read as
property-local. Errors: 404 unknown property/room/job, 422 bad input, 503 feature not configured
(I-G21) with detail {"error": "not_configured", "service", "message"}.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from packages.schema.reports import (
    Amenity,
    AreaProfile,
    Comp,
    DaylightMoment,
    DaylightSpot,
    NoiseReport,
    NoiseScenario,
    SoundCatalog,
    SoundDoors,
    SoundField,
    SunMoment,
    SunReport,
    SunRoomDay,
    SunSpot,
)
from packages.schema.twin import Opening, PropertyTwin
from services.api.integrations.config import IntegrationNotConfigured, fixtures_dir
from services.api.sim.sun import SEATED_M
from services.api.twin import annotations, service
from services.api.twin.service import TwinNotFound

router = APIRouter(tags=["properties"])

ASSET_ROOT = "blobs"  # only files under fixtures/blobs/ are served
MEDIA_TYPES = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".jpg": "image/jpeg", ".png": "image/png",
               ".tif": "image/tiff", ".tiff": "image/tiff", ".json": "application/json"}
MAX_REPORT_DAYS = 366

Sky = Literal["clear", "typical"]
HeightM = Query(0.0, ge=0.0, le=3.0, description="sample height above the room's floor (m): 0 floor, 0.8 desk, "
                                                 "1.1 seated, 1.6 standing")
UserId = Header("demo", alias="X-User-Id")


@contextmanager
def _errors():
    try:
        yield
    except TwinNotFound as e:
        raise HTTPException(404, str(e)) from e
    except KeyError as e:
        raise HTTPException(404, e.args[0] if e.args else "not found") from e
    except IntegrationNotConfigured as e:
        raise HTTPException(503, {"error": "not_configured", "service": e.service, "message": str(e)}) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


def _datetime(when: str) -> str:
    try:
        datetime.fromisoformat(when)
    except ValueError as e:
        raise HTTPException(422, f"when must be ISO 8601, e.g. 2026-12-21T09:00 or 2026-12-21T09:00-06:00: {e}")
    return when


# --- twin -----------------------------------------------------------------------------------


@router.get("/properties/{pid}/twin", response_model=PropertyTwin)
def get_twin(pid: str, request: Request, x_user_id: str = UserId) -> PropertyTwin:
    """The house twin, with asset keys resolved to URLs. Also starts warming the sun/daylight setup."""
    with _errors():
        twin = service.get_twin(pid)
    service.warm(pid)
    return _with_asset_urls(twin, request)


@router.put("/properties/{pid}/openings", response_model=list[Opening])
def put_openings(pid: str, openings: list[Opening], x_user_id: str = UserId) -> list[Opening]:
    """Replace the openings with an annotated list (annotation mode). Re-ingestion keeps them."""
    with _errors():
        return annotations.save_openings(pid, openings).openings


def _with_asset_urls(twin: PropertyTwin, request: Request) -> PropertyTwin:
    def url(key: str | None) -> str | None:
        return None if key is None else str(request.url_for("get_asset", key=key))

    a = twin.assets
    assets = a.model_copy(update={
        "interior_glb": url(a.interior_glb),
        "context_glb": url(a.context_glb),
        "ortho": url(a.ortho),
        "obliques": [url(k) for k in a.obliques],
    })
    return twin.model_copy(update={"assets": assets})


@router.get("/assets/{key:path}", name="get_asset")
def get_asset(key: str) -> FileResponse:
    """A file the twin points to (interior.glb, ...). Keys are relative to fixtures/."""
    root = (fixtures_dir() / ASSET_ROOT).resolve()
    path = (fixtures_dir() / key).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(404, f"no asset {key}")
    if path.stat().st_size < 1024 and path.read_bytes().startswith(b"version https://git-lfs"):
        raise HTTPException(503, {"error": "not_configured", "service": "git-lfs",
                                  "message": f"{key} is a Git LFS pointer; run `git lfs pull`"})
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
                        headers={"Cache-Control": "public, max-age=3600"})


# --- sun ------------------------------------------------------------------------------------


@router.get("/properties/{pid}/sun", response_model=SunReport)
def get_sun_report(pid: str, room_id: str, start: date, end: date, height_m: float = HeightM,
                   x_user_id: str = UserId) -> SunReport:
    """Direct-sun hours per day over a date range, plus a mean-hours heatmap."""
    if (end - start).days > MAX_REPORT_DAYS:
        raise HTTPException(422, f"at most {MAX_REPORT_DAYS} days per request")
    with _errors():
        return service.sun_report(pid, room_id, start, end, height_m)


@router.get("/properties/{pid}/sun/at", response_model=SunMoment)
def get_sun_at(pid: str, when: str, height_m: float = HeightM, x_user_id: str = UserId) -> SunMoment:
    """Where direct sun is, room by room, at one instant (with lit-cell masks)."""
    with _errors():
        return service.sun_at(pid, _datetime(when), height_m)


@router.get("/properties/{pid}/sun/day", response_model=SunRoomDay)
def get_sun_day(pid: str, room_id: str, date: date, height_m: float = HeightM, x_user_id: str = UserId) -> SunRoomDay:
    """When direct sun reaches any part of a room on one day."""
    with _errors():
        return service.sun_room_day(pid, room_id, date, height_m)


@router.get("/properties/{pid}/sun/spot", response_model=SunSpot)
def get_sun_spot(pid: str, room_id: str, x: float, z: float, date: date,
                 height_m: float = Query(SEATED_M, ge=0.0, le=3.0), x_user_id: str = UserId) -> SunSpot:
    """When direct sun reaches one spot (x, z in the interior frame) on one day."""
    with _errors():
        return service.sun_spot(pid, room_id, x, z, date, height_m)


# --- daylight -------------------------------------------------------------------------------


@router.get("/properties/{pid}/daylight/at", response_model=DaylightMoment)
def get_daylight_at(pid: str, when: str, sky: Sky = "clear", height_m: float = HeightM,
                    room_id: str | None = None, grids: bool = True, x_user_id: str = UserId) -> DaylightMoment:
    """Daylight (lux: sky + sun + reflected) in every room with windows, at one instant.

    sky=typical uses the NSRDB typical-year weather (503 if it isn't available). grids=false drops
    the per-cell lux grids. The first call per height sets up each room (a few seconds)."""
    with _errors():
        return service.daylight_at(pid, _datetime(when), sky, height_m, room_id, grids)


@router.get("/properties/{pid}/daylight/spot", response_model=DaylightSpot)
def get_daylight_spot(pid: str, room_id: str, x: float, z: float, date: date, sky: Sky = "clear",
                      height_m: float = Query(SEATED_M, ge=0.0, le=3.0), x_user_id: str = UserId) -> DaylightSpot:
    """Daylight at one spot every 15 minutes through one day, with the moments in direct sun."""
    with _errors():
        return service.daylight_spot(pid, room_id, x, z, date, sky, height_m)


# --- sound placed in the house --------------------------------------------------------------


@router.get("/properties/{pid}/sound/presets", response_model=SoundCatalog)
def get_sound_presets(pid: str, x_user_id: str = UserId) -> SoundCatalog:
    """Sounds a user can place, and loudness choices for their own recording."""
    with _errors():
        service.get_twin(pid)
        return SoundCatalog(presets=service.sound_presets(), loudness_dba=service.acoustics().loudness)


@router.get("/properties/{pid}/sound/field", response_model=SoundField)
def get_sound_field(
    pid: str,
    room_id: str,
    x: float,
    z: float,
    preset: str = "tv",
    level_dba: float | None = Query(None, ge=20, le=120, description="at 1 m; default: the preset's"),
    doors: SoundDoors = "closed",
    include_furniture: bool = True,
    listener_room_id: str | None = None,
    listener_x: float | None = None,
    listener_z: float | None = None,
    x_user_id: str = UserId,
) -> SoundField:
    """A sound at (x, z) in room_id: every room's level, band gains and reverb time. With
    listener_*, also the level at that spot (direct sound added in the source's own room)."""
    given = [v is not None for v in (listener_room_id, listener_x, listener_z)]
    if any(given) and not all(given):
        raise HTTPException(422, "listener_room_id, listener_x and listener_z go together")
    listener = (listener_room_id, listener_x, listener_z) if all(given) else None
    with _errors():
        return service.sound_field(pid, room_id, x, z, preset, level_dba, doors, include_furniture, listener)


# --- noise (not built yet) and area ---------------------------------------------------------


@router.get("/properties/{pid}/noise", response_model=NoiseReport)
def get_noise(pid: str, room_id: str, glazing: Literal["single", "double", "triple"] | None = None,
              add_carpet: bool = False, x_user_id: str = UserId) -> NoiseReport:
    with _errors():
        return service.noise_report(pid, room_id, NoiseScenario(glazing=glazing, add_carpet=add_carpet))


@router.get("/properties/{pid}/area", response_model=AreaProfile)
def get_area(pid: str, x_user_id: str = UserId) -> AreaProfile:
    """The home's listing, comparable homes, nearby amenities and a markdown summary. Missing
    sources show up in `caveats`, not as errors."""
    with _errors():
        return service.area_profile(pid)


@router.get("/properties/{pid}/area/search", response_model=list[Amenity])
def get_area_search(pid: str, q: str = Query(..., min_length=1), limit: int = Query(10, ge=1, le=50),
                    x_user_id: str = UserId) -> list[Amenity]:
    """Amenities matching a free-text query ("coffee", "bus stop", "Kroger"), nearest first."""
    with _errors():
        return service.area_search(pid, q, limit)


@router.get("/properties/{pid}/area/comps", response_model=list[Comp])
def get_area_comps(pid: str, x_user_id: str = UserId) -> list[Comp]:
    with _errors():
        return service.get_comps(pid)


# --- ingestion ------------------------------------------------------------------------------


class IngestJob(BaseModel):
    job_id: str
    property_id: str
    status: Literal["queued", "running", "done", "failed"]
    version: int | None = None
    error: str | None = None


class JobRef(BaseModel):
    job_id: str


_jobs: dict[str, IngestJob] = {}
_jobs_lock = threading.Lock()


@router.post("/properties/{pid}/ingest", response_model=JobRef)
def post_ingest(pid: str, x_user_id: str = UserId) -> JobRef:
    """Rebuild the twin from Matterport (INTEGRATION_MODE applies). Poll GET .../ingest/{job_id}."""
    job = IngestJob(job_id=str(uuid.uuid4()), property_id=pid, status="queued")
    with _jobs_lock:
        _jobs[job.job_id] = job
    threading.Thread(target=_run_ingest, args=(job.job_id, pid), daemon=True).start()
    return JobRef(job_id=job.job_id)


@router.get("/properties/{pid}/ingest/{job_id}", response_model=IngestJob)
def get_ingest(pid: str, job_id: str, x_user_id: str = UserId) -> IngestJob:
    job = _jobs.get(job_id)
    if job is None or job.property_id != pid:
        raise HTTPException(404, f"no ingest job {job_id}")
    return job


def _run_ingest(job_id: str, pid: str) -> None:
    from services.api.twin.ingest import build_twin, write_twin

    def update(**fields):
        with _jobs_lock:
            _jobs[job_id] = _jobs[job_id].model_copy(update=fields)

    update(status="running")
    try:
        twin = write_twin(build_twin(pid), Path(service.twin_path(pid)))
        update(status="done", version=twin.version)
    except Exception as e:  # reported to the client through the job
        update(status="failed", error=f"{type(e).__name__}: {e}")
