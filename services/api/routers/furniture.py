"""Inventory, layouts and memory routes (WORKSTREAMS.md section 2.4). Owner: B.

Shapes follow the web app's reading of the contract (apps/web/README.md, "For Stream B"):
GET /layouts/{id} exists, GET /memory is a list of relative paths, memory bodies are text/plain.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from packages.schema.inventory import InventoryItem
from packages.schema.layout import LayoutVariant, PlacementMove
from packages.schema.twin import Vec3
from packages.schema.session import LayoutFeedbackRequest
from services.api.agent import memory_files
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


@router.put("/layouts/{layout_id}/placements/{item_id}")
def move_placement(layout_id: str, item_id: str, body: PlacementMove, x_user_id: str = Header("demo")) -> LayoutVariant:
    """The user moved or turned one piece. Saved only if the layout is still valid (B2): inside the
    room, no overlaps, doors, windows and the walking path clear. Otherwise 409 with the reasons,
    and the piece should snap back."""
    store = furniture_store()
    layout = _found(store.get_layout(x_user_id, layout_id), "layout")
    if all(str(p.item_id) != item_id for p in layout.placements):
        raise HTTPException(404, "that item is not in this layout")
    twin = get_twin(layout.property_id)
    room = next(r for r in twin.rooms if r.room_id == layout.room_id)
    owned = {str(i.item_id): i for i in store.list_items(x_user_id)}
    items, poses = [], {}
    for p in layout.placements:
        key = str(p.item_id)
        if key not in owned:
            continue  # the item was deleted from the inventory; it no longer takes up space
        o = owned[key]
        items.append(layout_solver.Item(key, o.label, o.item_class, o.dims_m.w, o.dims_m.d, o.dims_m.h))
        poses[key] = layout_solver.Pose(body.x, body.z, body.yaw_deg % 360) if key == item_id else layout_solver.Pose(p.position.x, p.position.z, p.yaw_deg)
    problems = layout_solver.violations(room, twin.openings, items, poses)
    if problems:
        raise HTTPException(409, {"error": "invalid_placement", "violations": problems})
    placements = [
        p.model_copy(update={"position": Vec3(x=round(body.x, 3), y=room.floor_y, z=round(body.z, 3)), "yaw_deg": round(body.yaw_deg % 360, 1)})
        if str(p.item_id) == item_id
        else p
        for p in layout.placements
    ]
    moved = layout.model_copy(update={"placements": placements})
    store.put_layout(moved)
    return moved


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
