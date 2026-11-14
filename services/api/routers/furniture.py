"""Inventory, layouts and memory routes (WORKSTREAMS.md section 2.4). Owner: B.

Shapes follow the web app's reading of the contract (apps/web/README.md, "For Stream B"):
GET /layouts/{id} exists, GET /memory is a list of relative paths, memory bodies are text/plain.
"""

from __future__ import annotations

import uuid

from shapely.geometry import Point, Polygon

from fastapi import APIRouter, Body, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from packages.schema.inventory import InventoryItem
from packages.schema.layout import LayoutVariant, Placement, PlacementDrop, PlacementMove
from packages.schema.twin import Vec3
from packages.schema.session import LayoutFeedbackRequest
from services.api.agent import memory_files, views
from services.api.furniture.store import furniture_store
from services.api.sim import layout_solver
from services.api.twin.service import get_twin

router = APIRouter(tags=["furniture"])


def _found(value, what: str):
    if value is None or value is False:
        raise HTTPException(404, f"no such {what}")
    return value


# --- inventory ----------------------------------------------------------------------------------


@router.get("/inventory")
def list_inventory(request: Request, x_user_id: str = Header("demo")) -> list[InventoryItem]:
    items = furniture_store().list_items(x_user_id)
    memory_files.sync_inventory(request.app.state.memory_store, x_user_id, items)
    return items


@router.get("/inventory/{item_id}")
def get_inventory_item(item_id: str, x_user_id: str = Header("demo")) -> InventoryItem:
    return _found(furniture_store().get_item(x_user_id, item_id), "item")


@router.delete("/inventory/{item_id}", status_code=204)
def delete_inventory_item(item_id: str, request: Request, x_user_id: str = Header("demo")) -> Response:
    _found(furniture_store().delete_item(x_user_id, item_id), "item")
    memory_files.sync_inventory(request.app.state.memory_store, x_user_id, furniture_store().list_items(x_user_id))
    return Response(status_code=204)


# --- layouts ------------------------------------------------------------------------------------


@router.get("/layouts")
def list_layouts(property_id: str | None = None, room_id: str | None = None, x_user_id: str = Header("demo")) -> list[LayoutVariant]:
    return furniture_store().list_layouts(x_user_id, property_id, room_id)


@router.get("/layouts/{layout_id}")
def get_layout(layout_id: str, x_user_id: str = Header("demo")) -> LayoutVariant:
    return _found(furniture_store().get_layout(x_user_id, layout_id), "layout")


@router.post("/layouts/{layout_id}/select")
def select_layout(layout_id: str, x_user_id: str = Header("demo")) -> LayoutVariant:
    return _found(furniture_store().choose_layout(x_user_id, layout_id), "layout")


@router.post("/layouts/{layout_id}/feedback")
def layout_feedback(layout_id: str, body: LayoutFeedbackRequest, x_user_id: str = Header("demo")) -> LayoutVariant:
    return _found(furniture_store().set_feedback(x_user_id, layout_id, body.feedback), "layout")


def _room_state(user_id: str, layout: LayoutVariant):
    """The room, and the solver's view of the pieces a layout already holds."""
    twin = get_twin(layout.property_id)
    room = next(r for r in twin.rooms if r.room_id == layout.room_id)
    room = room.model_copy(update={"floor_y": views.floor_height(twin, room.room_id)})  # sills from the real floor
    owned = {str(i.item_id): i for i in furniture_store().list_items(user_id)}
    items, poses = [], {}
    for p in layout.placements:
        key = str(p.item_id)
        if key in owned:  # a piece deleted from the inventory no longer takes up space
            o = owned[key]
            items.append(layout_solver.Item(key, o.label, o.item_class, o.dims_m.w, o.dims_m.d, o.dims_m.h))
            poses[key] = layout_solver.Pose(p.position.x, p.position.z, p.yaw_deg)
    return twin, room, owned, items, poses


def _placed(twin, room, item_id: str, pose: layout_solver.Pose) -> Placement:
    return Placement(
        item_id=item_id,
        position=Vec3(x=round(pose.x, 3), y=room.floor_y, z=round(pose.z, 3)),
        yaw_deg=round(pose.yaw % 360, 1),
    )


@router.put("/layouts/{layout_id}/placements/{item_id}")
def move_placement(layout_id: str, item_id: str, body: PlacementMove, x_user_id: str = Header("demo")) -> LayoutVariant:
    """The user moved or turned one piece by hand. Their call: any spot and any angle is saved as
    long as the piece's center stays inside its room. Anything a designer would flag (an overlap, a
    blocked door or path, a corner through a wall) comes back in `warnings`, for the UI to show
    without getting in the way. 409 only when the piece's center would leave the room."""
    store = furniture_store()
    layout = _found(store.get_layout(x_user_id, layout_id), "layout")
    if all(str(p.item_id) != item_id for p in layout.placements):
        raise HTTPException(404, "that item is not in this layout")
    twin, room, _owned, items, poses = _room_state(x_user_id, layout)
    pose = layout_solver.Pose(body.x, body.z, body.yaw_deg % 360)
    problems = layout_solver.violations(room, twin.openings, items, {**poses, item_id: pose}, walls=twin.walls, sweeps=twin.sweeps)
    if not Polygon(room.polygon).contains(Point(body.x, body.z)):  # its center left the room
        label = next((i.label for i in items if i.item_id == item_id), "That piece")
        raise HTTPException(409, {"error": "outside_room", "violations": [f"{label} has to stay inside the room"]})
    placements = [_placed(twin, room, item_id, pose) if str(p.item_id) == item_id else p for p in layout.placements]
    moved = layout.model_copy(update={"placements": placements})
    store.put_layout(moved)
    return moved.model_copy(update={"warnings": problems or None})


@router.post("/layouts/drop")
def drop_item(body: PlacementDrop, x_user_id: str = Header("demo")) -> LayoutVariant:
    """The user pointed at a spot (a pin on the floor plan) for one of their pieces. The piece is
    put at the closest spot near there that breaks no rule, backed against the wall if the pin is
    by one. With `layout_id` it joins or moves within that layout; without, a new layout starts."""
    store = furniture_store()
    twin = get_twin(body.property_id)
    owned = store.get_item(x_user_id, body.item_id)
    if owned is None:
        raise HTTPException(404, "no such item")
    layout = _found(store.get_layout(x_user_id, body.layout_id), "layout") if body.layout_id else None
    room_id = layout.room_id if layout else body.room_id
    if room_id is None:  # find the room under the pin
        here = Point(body.x, body.z)
        under = [r for r in twin.rooms if (body.floor_id is None or r.floor_id == body.floor_id) and Polygon(r.polygon).contains(here)]
        if not under:
            raise HTTPException(422, "that spot is not inside a room")
        room_id = min(under, key=lambda r: r.area_m2 or 0).room_id
    if layout is None:
        layout = LayoutVariant(
            layout_id=uuid.uuid4(), user_id=x_user_id, property_id=body.property_id, room_id=room_id,
            placements=[], score=0, rationale="Placed by hand.", status="proposed",
        )
    twin, room, _owned, items, poses = _room_state(x_user_id, layout)
    poses.pop(body.item_id, None)
    items = [i for i in items if i.item_id != body.item_id]
    items.append(layout_solver.Item(body.item_id, owned.label, owned.item_class, owned.dims_m.w, owned.dims_m.d, owned.dims_m.h))
    pose = layout_solver.nearest_valid_pose(room, twin.openings, items, poses, body.item_id, body.x, body.z, walls=twin.walls, sweeps=twin.sweeps)
    if pose is None:
        raise HTTPException(409, {"error": "no_room", "violations": [f"{owned.label} doesn't fit anywhere within 2 m of that spot"]})
    placements = [p for p in layout.placements if str(p.item_id) != body.item_id] + [_placed(twin, room, body.item_id, pose)]
    dropped = layout.model_copy(update={"placements": placements})
    store.put_layout(dropped)
    return dropped


# --- memory (the /me page) ----------------------------------------------------------------------


@router.get("/memory")
def list_memory(request: Request, x_user_id: str = Header("demo")) -> list[str]:
    return memory_files.list_files(request.app.state.memory_store, x_user_id)


@router.get("/memory/{path:path}", response_class=PlainTextResponse)
def get_memory(path: str, request: Request, x_user_id: str = Header("demo")) -> str:
    return _found(memory_files.read_file(request.app.state.memory_store, x_user_id, path), "memory file")


@router.put("/memory/{path:path}", status_code=204)
def put_memory(path: str, request: Request, text: str = Body(media_type="text/plain"), x_user_id: str = Header("demo")) -> Response:
    if path == memory_files.INVENTORY:
        raise HTTPException(409, "inventory.md is generated from the inventory database (B3)")
    memory_files.write_file(request.app.state.memory_store, x_user_id, path, text)
    return Response(status_code=204)


@router.delete("/memory/{path:path}", status_code=204)
def delete_memory(path: str, request: Request, x_user_id: str = Header("demo")) -> Response:
    _found(memory_files.delete_file(request.app.state.memory_store, x_user_id, path), "memory file")
    return Response(status_code=204)
