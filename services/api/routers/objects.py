"""Uploads, photo -> furniture jobs, and stored files (WORKSTREAMS.md section 2.4). Owner: B."""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from packages.schema.inventory import CatalogAddRequest, CatalogPiece, CatalogSet, InventoryItem, ItemDims
from packages.schema.session import JobRef, ObjectIngestRequest, UploadResult
from services.api.agent import memory_files
from services.api.furniture.store import furniture_store
from services.api.objects import files, pipeline, video
from services.api.objects.normalize import NormalizeError
from services.api.objects.providers import catalog
from services.api.runner_hooks import object_ready

router = APIRouter(tags=["objects"])

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
VIDEO_TYPES = {"video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm"}  # a walk through a room
VIDEO_FRAMES = 8


@router.post("/uploads")
async def upload(file: UploadFile, x_user_id: str = Header("demo")) -> UploadResult:
    kind = file.content_type or ""
    suffix = IMAGE_TYPES.get(kind) or VIDEO_TYPES.get(kind)
    if suffix is None:
        raise HTTPException(415, "upload a PNG, JPEG or WebP image, or an MP4, MOV or WebM video of a room")
    data = await file.read()
    if kind in IMAGE_TYPES and len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "the image is larger than 15 MB")
    if len(data) > MAX_VIDEO_BYTES:
        raise HTTPException(413, "the video is larger than 200 MB")
    upload_id = uuid.uuid4().hex
    name = Path(file.filename or "upload").stem[:60].replace("/", "_") or "upload"
    url = files.save(f"uploads/{x_user_id}/{upload_id}__{name}{suffix}", data)
    return UploadResult(upload_id=upload_id, url=url)


def find_upload(user_id: str, upload_id: str) -> Path | None:
    """A person's uploaded file by its id, or None."""
    folder = files.root() / "uploads" / user_id
    return next(iter(folder.glob(f"{upload_id}__*")), None) if folder.is_dir() and upload_id.isalnum() else None


@router.post("/objects/ingest")
def ingest(body: ObjectIngestRequest, request: Request, x_user_id: str = Header("demo")) -> JobRef:
    """Turn an uploaded image, or a video of a room, into inventory items. Returns at once;
    follow `job.progress`. With `at` (a spot tapped on the image) only the thing there is built,
    and the job fails with the reason if it can't be."""
    path = find_upload(x_user_id, body.upload_id)
    if path is None:
        raise HTTPException(404, "no such upload")
    if body.at and path.suffix in video.VIDEO_SUFFIXES:
        raise HTTPException(422, "a tapped spot only works on a photo")
    store, memory = furniture_store(), request.app.state.memory_store
    providers = request.app.state.object_providers or pipeline.default_providers()
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    source_url = f"/api/files/uploads/{x_user_id}/{path.name}"
    at = (min(max(body.at.x, 0.0), 1.0), min(max(body.at.y, 0.0), 1.0)) if body.at else None

    def work(progress):
        if path.suffix in video.VIDEO_SUFFIXES:
            frames = video.sharp_frames(path, VIDEO_FRAMES)
            result = pipeline.ingest_frames(frames, pipeline.save_frames(frames, x_user_id), x_user_id, store, providers, progress)
        else:
            result = pipeline.ingest_image(path.read_bytes(), mime, path.name.split("__", 1)[-1], source_url, x_user_id, store, providers, progress, at=at)
        if at and not result.items:
            raise RuntimeError(result.summary().removeprefix("could not add "))
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


@router.post("/objects/import")
async def import_model(
    request: Request,
    file: UploadFile,
    label: str = Form(...),
    item_class: str = Form("other"),
    width_m: float | None = Form(None),
    depth_m: float | None = Form(None),
    height_m: float | None = Form(None),
    x_user_id: str = Header("demo"),
) -> InventoryItem:
    """Add a .glb the user already has as a piece of furniture. Give all three dimensions to size
    it exactly; with none, it gets the typical size for its class."""
    data = await file.read()
    if len(data) > 60 * 1024 * 1024:
        raise HTTPException(413, "the 3D file is larger than 60 MB")
    try:
        item = pipeline.import_glb(data, label, item_class, (width_m, depth_m, height_m), x_user_id, furniture_store())
    except NormalizeError as e:
        raise HTTPException(422, str(e)) from e
    memory_files.sync_inventory(request.app.state.memory_store, x_user_id, furniture_store().list_items(x_user_id))
    return item


@router.get("/catalog")
def list_catalog(item_class: str | None = None, style: str | None = None) -> list[CatalogPiece]:
    """Ready-made furniture anyone can add: free models, at real size. Each piece says which look
    it belongs to and which sets it is part of. `style` keeps one look ("Farmhouse")."""
    return [
        CatalogPiece(
            catalog_id=cid, label=label, item_class=cls, dims_m=ItemDims(w=w, d=d, h=h),
            thumbnail_url=catalog.thumbnail_url(cid), source=catalog.SOURCE,
            style=catalog.style_of(cid), sets=catalog.sets_of(cid),
        )
        for cid, cls, label, w, d, h in catalog.PIECES
        if (item_class is None or cls == item_class) and (style is None or catalog.style_of(cid).lower() == style.lower())
    ]


@router.get("/catalog/sets")
def list_catalog_sets(style: str | None = None) -> list[CatalogSet]:
    """The catalog's sets: each a room's worth of pieces designed to go together, in one look."""
    return [
        CatalogSet(
            set_id=st["id"], name=st["name"], style=st["style"], rooms=st["rooms"], look=st["look"],
            piece_ids=st["pieces"], thumbnail_urls=[catalog.thumbnail_url(cid) for cid in st["pieces"][:4]],
        )
        for st in catalog.SETS
        if style is None or st["style"].lower() == style.lower()
    ]


@router.post("/catalog/{catalog_id}/add")
def add_catalog_piece(catalog_id: str, request: Request, body: CatalogAddRequest | None = None, x_user_id: str = Header("demo")) -> InventoryItem:
    """Put a catalog piece into the user's inventory. The first use of a piece downloads its model."""
    if catalog_id not in catalog.BY_ID:
        raise HTTPException(404, "no such catalog piece")
    body = body or CatalogAddRequest()
    try:
        item = pipeline.add_from_catalog(catalog_id, x_user_id, furniture_store(), body.label, (body.width_m, body.depth_m, body.height_m))
    except NormalizeError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # the model could not be downloaded
        raise HTTPException(503, f"could not load that model: {e}") from e
    memory_files.sync_inventory(request.app.state.memory_store, x_user_id, furniture_store().list_items(x_user_id))
    return item


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
