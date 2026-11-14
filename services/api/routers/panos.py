"""Scan-point panoramas as GL cube faces (twin/panos.py). Owner: C. Mounted under /api.

The first request for a sweep downloads its skybox from Matterport (needs the Model API keys,
else 503) and caches the re-cut faces under data/; later requests are plain file reads.
?defurnished=true serves Matterport's furniture-free panoramas; at 4k (which Matterport doesn't
serve for them) the furnished 4k faces with the painted-out regions filled from 2k.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Path, Query
from fastapi.responses import FileResponse

from services.api.routers.properties import _errors
from services.api.twin import panos, service

router = APIRouter(tags=["properties"])

Face = Literal["px", "nx", "py", "ny", "pz", "nz"]
Resolution = Literal["low", "2k", "high", "4k"]


@router.get("/properties/{pid}/sweeps/{sweep_id}/skybox/{face}", response_class=FileResponse,
            responses={200: {"content": {"image/jpeg": {}}}})
def get_skybox_face(pid: str, sweep_id: str, face: Face = Path(...),
                    res: Resolution = Query("2k", description="low: 256, high: 1024, 2k: 2048, 4k: 4096 px faces"),
                    defurnished: bool = Query(False, description="furniture painted out (Matterport's defurnished view)")):
    with _errors():
        twin = service.get_twin(pid)
        if not any(s.sweep_id == sweep_id for s in twin.sweeps):
            raise KeyError(f"unknown sweep {sweep_id}")
        path = panos.skybox_face(twin.sources.matterport_model_id, sweep_id, face, res, defurnished=defurnished)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
