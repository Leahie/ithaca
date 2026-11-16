"""Scanned rooms: a photo of someone's own room with every thing in it modelled in 3D
(services/api/furniture/scans). List them, look at a thing's model, set a room of the house up
with the whole scan, or put one thing in a room or in storage."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response

from packages.schema.inventory import FurnishFromScanRequest, ItemDims, RoomScan, ScanThing, ScanThingPlaced
from packages.schema.session import JobRef
from services.api.agent import memory_files
from services.api.furniture import house, scans
from services.api.furniture.house import FurnitureError
from services.api.furniture.store import furniture_store
from services.api.runner_hooks import own_room_ready
from services.api.twin.service import TwinNotFound, get_twin

router = APIRouter(tags=["scans"])


def _scan(scan_id: str) -> scans.Scan:
    scan = scans.get(scan_id)
    if scan is None:
        raise HTTPException(404, "no such scan")
    return scan


def _thing(scan: scans.Scan, key: str):
    thing = scan.thing(key)
    if thing is None:
        raise HTTPException(404, "no such thing in this scan")
    return thing


def _card(scan: scans.Scan) -> RoomScan:
    pw, ph = scan.module.SIZE
    things = []
    for t in scan.things:
        _, (w, d, h) = scans.model(scan.scan_id, t.key)
        x0, y0, x1, y1 = t.box_px
        things.append(ScanThing(
            key=t.key, label=t.label, item_class=t.item_class, group=t.group, dims_m=ItemDims(w=w, d=d, h=h),
            box=(round(x0 / pw, 4), round(y0 / ph, 4), round(x1 / pw, 4), round(y1 / ph, 4)), rests_on=t.rests_on, on_wall=t.on_wall,
            note=t.note, model_url=f"/api/scans/{scan.scan_id}/things/{t.key}/model.glb",
        ))
    return RoomScan(
        scan_id=scan.scan_id, name=scan.module.NAME, blurb=scan.module.BLURB, photo_url=f"/api/scans/{scan.scan_id}/photo",
        width=pw, height=ph, finishes=list(scan.module.FINISHES), things=things,
    )


@router.get("/scans")
def list_scans() -> list[RoomScan]:
    """Every scanned room, with what is in it and where in the photo each thing is."""
    return [_card(s) for s in scans.all_scans()]


@router.get("/scans/{scan_id}/photo")
def scan_photo(scan_id: str) -> FileResponse:
    return FileResponse(_scan(scan_id).module.PHOTO, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/scans/{scan_id}/things/{key}/model.glb")
def thing_model(scan_id: str, key: str) -> Response:
    scan = _scan(scan_id)
    _thing(scan, key)
    glb, _ = scans.model(scan_id, key)
    return Response(glb, media_type="model/gltf-binary", headers={"Cache-Control": "public, max-age=3600"})


@router.post("/scans/{scan_id}/things/{key}/add")
def add_thing(scan_id: str, key: str, request: Request, x_user_id: str = Header("demo"), x_person_id: str | None = Header(None)) -> ScanThingPlaced:
    """Put one thing from the scan in the person's storage (a new piece, even if they have one)."""
    scan = _scan(scan_id)
    person = x_person_id or x_user_id
    item = scans.item_for(scan, _thing(scan, key), person, fresh=True)
    memory_files.sync_inventory(request.app.state.memory_store, person, furniture_store().list_items(person))
    return ScanThingPlaced(item=item)


@router.post("/properties/{property_id}/furniture/from-scan/{key}")
def put_thing(
    property_id: str, key: str, body: FurnishFromScanRequest, request: Request, x_user_id: str = Header("demo"), x_person_id: str | None = Header(None)
) -> ScanThingPlaced:
    """Put one thing from the scan in a room of the house, beside what is there: on the piece it
    stood on when that is in the room, else where its kind goes."""
    scan = _scan(body.scan_id)
    thing = _thing(scan, key)
    person = x_person_id or x_user_id
    try:
        get_twin(property_id)
        item, piece = scans.put_one(scan, thing, x_user_id, property_id, body.room_id, person)
    except TwinNotFound as e:
        raise HTTPException(404, str(e)) from e
    except FurnitureError as e:
        raise HTTPException(e.status, e.detail) from e
    memory_files.sync_inventory(request.app.state.memory_store, person, furniture_store().list_items(person))
    return ScanThingPlaced(item=item, room_id=piece.room_id)


@router.post("/properties/{property_id}/furniture/from-scan")
def furnish_from_scan(
    property_id: str, body: FurnishFromScanRequest, request: Request, x_user_id: str = Header("demo"), x_person_id: str | None = Header(None)
) -> JobRef:
    """Set a room of this house up with a scanned room: everything in it, or the things `keys`
    names. On the floor where it fits this room, on the piece it stood on, on the wall over the
    piece it hung over, curtains at the windows. What stood in the room before goes back to
    storage. Returns at once; follow `job.progress` (about half a minute)."""
    scan = _scan(body.scan_id)
    person = x_person_id or x_user_id
    try:
        twin = get_twin(property_id)
    except TwinNotFound as e:
        raise HTTPException(404, str(e)) from e
    if house.room_by_id(twin, body.room_id) is None:
        raise HTTPException(404, "no such room")
    unknown = [k for k in body.keys or [] if scan.thing(k) is None]
    if unknown:
        raise HTTPException(422, f"not in this scan: {', '.join(unknown)}")
    memory = request.app.state.memory_store

    def work(progress):
        outcome = scans.furnish(scan, x_user_id, property_id, body.room_id, person, body.keys, progress)
        memory_files.sync_inventory(memory, person, furniture_store().list_items(person))
        if not outcome.placed:
            raise RuntimeError("nothing from the scan could be put in this room" + (": " + "; ".join(outcome.left) if outcome.left else ""))
        return outcome

    job_id = request.app.state.jobs.submit(
        "room_scan",
        x_user_id,
        work,
        on_done=lambda outcome: own_room_ready(request.app.state.bridge, x_user_id, person, outcome.summary()),
        result_ref=lambda outcome: ",".join(str(i.item_id) for i in outcome.placed) or None,
    )
    return JobRef(job_id=job_id)
