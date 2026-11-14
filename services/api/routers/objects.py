"""Uploads, photo -> furniture jobs, and stored files (WORKSTREAMS.md section 2.4). Owner: B."""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from packages.schema.session import JobRef, ObjectIngestRequest, UploadResult
from services.api.agent import memory_files
from services.api.furniture.store import furniture_store
from services.api.objects import files, pipeline
from services.api.runner_hooks import object_ready

router = APIRouter(tags=["objects"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


@router.post("/uploads")
async def upload(file: UploadFile, x_user_id: str = Header("demo")) -> UploadResult:
    suffix = IMAGE_TYPES.get(file.content_type or "")
    if suffix is None:
        raise HTTPException(415, "upload a PNG, JPEG or WebP image")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "the image is larger than 15 MB")
    upload_id = uuid.uuid4().hex
    name = Path(file.filename or "upload").stem[:60].replace("/", "_") or "upload"
    url = files.save(f"uploads/{x_user_id}/{upload_id}__{name}{suffix}", data)
    return UploadResult(upload_id=upload_id, url=url)


def _find_upload(user_id: str, upload_id: str) -> Path | None:
    folder = files.root() / "uploads" / user_id
    return next(iter(folder.glob(f"{upload_id}__*")), None) if folder.is_dir() and upload_id.isalnum() else None


@router.post("/objects/ingest")
def ingest(body: ObjectIngestRequest, request: Request, x_user_id: str = Header("demo")) -> JobRef:
    """Turn an uploaded image into inventory items. Returns at once; follow `job.progress`."""
    path = _find_upload(x_user_id, body.upload_id)
    if path is None:
        raise HTTPException(404, "no such upload")
    store, memory = furniture_store(), request.app.state.memory_store
    providers = request.app.state.object_providers or pipeline.default_providers()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    source_url = f"/api/files/uploads/{x_user_id}/{path.name}"

    def work(progress):
        result = pipeline.ingest_image(path.read_bytes(), mime, path.name.split("__", 1)[-1], source_url, x_user_id, store, providers, progress)
        memory_files.sync_inventory(memory, x_user_id, store.list_items(x_user_id))
        return result

    job_id = request.app.state.jobs.submit(
        "object_ingest",
        x_user_id,
        work,
        on_done=lambda result: object_ready(request.app.state.bridge, x_user_id, result.summary()),
        result_ref=lambda result: ",".join(str(i.item_id) for i in result.items) or None,
    )
    return JobRef(job_id=job_id)


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request) -> dict:
    status = request.app.state.jobs.status.get(job_id)
    if status is None:
        raise HTTPException(404, "no such job")
    return status


@router.get("/files/{path:path}")
def get_file(path: str) -> FileResponse:
    found = files.find(path)
    if found is None:
        raise HTTPException(404, "no such file")
    media = "model/gltf-binary" if found.suffix == ".glb" else None
    return FileResponse(found, media_type=media)
