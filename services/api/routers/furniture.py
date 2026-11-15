"""Storage, in-house furniture, proposals and memory routes (WORKSTREAMS.md section 2.4). Owner: B.

The furniture rules are in docs/specs/furniture.md. Storage routes act as the person (X-User-Id =
the desktop user or a family member); in-house and proposal routes act as the house user (the big
screen's user), so every phone in the session arranges the same house.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Header, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from packages.schema.inventory import InventoryItem
from packages.schema.layout import HousePiece, LayoutVariant, PlacePiece
from packages.schema.session import LayoutFeedbackRequest
from services.api.agent import memory_files
from services.api.furniture import house, ledger
from services.api.furniture.house import FurnitureError
from services.api.furniture.store import furniture_store, house_key

router = APIRouter(tags=["furniture"])


def _found(value, what: str):
    if value is None or value is False:
        raise HTTPException(404, f"no such {what}")
    return value


def _call(fn, *args):
    try:
        return fn(*args)
    except FurnitureError as e:
        raise HTTPException(e.status, e.detail) from e


# --- storage ------------------------------------------------------------------------------------


@router.get("/inventory")
def list_inventory(request: Request, x_user_id: str = Header("demo")) -> list[InventoryItem]:
    """The family's furniture (F6). `user_id` is each piece's owner, `owner_name` their name."""
    items = house.pool(x_user_id)
    memory_files.sync_inventory(request.app.state.memory_store, x_user_id, [i for i in items if i.user_id == x_user_id])
    return items


@router.get("/inventory/{item_id}")
def get_inventory_item(item_id: str, x_user_id: str = Header("demo")) -> InventoryItem:
    return _found(house.pool_item(x_user_id, item_id), "item")


@router.delete("/inventory/{item_id}", status_code=204)
def delete_inventory_item(item_id: str, request: Request, x_user_id: str = Header("demo")) -> Response:
    """Delete a piece for good: it also leaves every house and every proposal (F1, F3)."""
    item = _call(house.delete_item, x_user_id, item_id)
    memory_files.sync_inventory(request.app.state.memory_store, item.user_id, furniture_store().list_items(item.user_id))
    return Response(status_code=204)


# --- in the house -------------------------------------------------------------------------------


@router.get("/properties/{property_id}/furniture")
def list_house_furniture(property_id: str, room_id: str | None = None, x_user_id: str = Header("demo")) -> list[HousePiece]:
    """Every piece standing in this house (or in one room of it)."""
    return furniture_store().house_pieces(x_user_id, property_id, room_id)


@router.put("/properties/{property_id}/furniture/{item_id}")
def place_piece(property_id: str, item_id: str, body: PlacePiece, x_user_id: str = Header("demo"), x_person_id: str | None = Header(None)) -> HousePiece:
    """Place a piece of the family's furniture at a spot, or move it there. The room comes from the
    spot (F4). By hand (`snap: false`) any spot inside a room is saved and what a designer would
    flag comes back in `warnings` (F9); `snap: true` asks for the nearest spot that breaks no rule.
    409 `outside_room` or `no_room` when there is nowhere to put it."""
    return _call(house.place, x_user_id, property_id, item_id, body, x_person_id or x_user_id)


@router.delete("/properties/{property_id}/furniture/{item_id}", status_code=204)
def remove_piece(property_id: str, item_id: str, x_user_id: str = Header("demo"), x_person_id: str | None = Header(None)) -> Response:
    """Take one piece out of the house (F7). It stays in storage."""
    _call(house.remove, x_user_id, property_id, item_id, x_person_id or x_user_id)
    return Response(status_code=204)


@router.get("/properties/{property_id}/furniture/register")
def house_register(property_id: str, x_user_id: str = Header("demo")) -> dict:
    """The house's furniture as the guides are shown it (F10, F11): every room with a piece in it,
    each piece with its number, its size, whose it is, where in the room it stands and who put it
    there; the family's pieces in storage; and the last changes anyone made."""
    try:
        return ledger.register(x_user_id, property_id)
    except KeyError as e:
        raise HTTPException(404, f"no such property: {e}") from e


# --- proposals ----------------------------------------------------------------------------------


@router.get("/layouts")
def list_layouts(property_id: str | None = None, room_id: str | None = None, x_user_id: str = Header("demo")) -> list[LayoutVariant]:
    return furniture_store().list_proposals(x_user_id, property_id, room_id)


@router.get("/layouts/{layout_id}")
def get_layout(layout_id: str, x_user_id: str = Header("demo")) -> LayoutVariant:
    return _found(furniture_store().get_proposal(x_user_id, layout_id), "layout")


@router.post("/layouts/{layout_id}/select")
def select_layout(layout_id: str, x_user_id: str = Header("demo"), x_person_id: str | None = Header(None)) -> LayoutVariant:
    """Put this proposal in the house: its room's furniture becomes exactly its pieces (F8)."""
    return _call(house.apply, x_user_id, layout_id, x_person_id or x_user_id)


@router.post("/layouts/{layout_id}/feedback")
def layout_feedback(layout_id: str, body: LayoutFeedbackRequest, x_user_id: str = Header("demo")) -> LayoutVariant:
    return _found(furniture_store().set_feedback(x_user_id, layout_id, body.feedback), "layout")


class PlacementOptions(BaseModel):
    """Body of POST /layouts/options: where could this piece go in this room?"""

    property_id: str
    room_id: str
    item_id: str


@router.post("/layouts/options")
def placement_options(body: PlacementOptions, x_user_id: str = Header("demo")) -> list[LayoutVariant]:
    """Up to three proposals that add one piece to a room, each with the piece at a different
    valid spot and the room's other furniture where it is. Nothing is in the house until the user
    keeps one (POST /layouts/{id}/select)."""
    return _call(house.options, x_user_id, body.property_id, body.room_id, body.item_id)


# --- memory (the /me page) ----------------------------------------------------------------------


def _memory_owner(user_id: str) -> str:
    """Whose files the /me page shows: a person's own, or, for a big screen with a family, that
    family's shared guide's (the same rule as AgentContext.memory_user)."""
    from services.api.realtime.households import households

    return households().current_for(user_id) or user_id


HOUSE_FILES = "house/"  # the furniture registers: one file per house, kept by the app (furniture/ledger.py)


def _house_files(user_id: str) -> tuple[str, ...]:
    """Where the registers of the house this person furnishes are saved: the big screen's house,
    for the screen's user and for a member of the family on it."""
    from services.api.realtime.households import households

    hosts = sorted(households().hosts_of(user_id))
    return ledger.namespace(house_key(hosts[0] if hosts else user_id))


@router.get("/memory")
def list_memory(request: Request, x_user_id: str = Header("demo")) -> list[str]:
    store = request.app.state.memory_store
    registers = [HOUSE_FILES + name for name in memory_files.list_files(store, _house_files(x_user_id))]
    return memory_files.list_files(store, _memory_owner(x_user_id)) + registers


@router.get("/memory/{path:path}", response_class=PlainTextResponse)
def get_memory(path: str, request: Request, x_user_id: str = Header("demo")) -> str:
    store = request.app.state.memory_store
    if path.startswith(HOUSE_FILES):
        return _found(memory_files.read_file(store, _house_files(x_user_id), path.removeprefix(HOUSE_FILES)), "memory file")
    return _found(memory_files.read_file(store, _memory_owner(x_user_id), path), "memory file")


@router.put("/memory/{path:path}", status_code=204)
def put_memory(path: str, request: Request, text: str = Body(media_type="text/plain"), x_user_id: str = Header("demo")) -> Response:
    if path == memory_files.INVENTORY:
        raise HTTPException(409, "inventory.md is generated from the inventory database (B3)")
    if path.startswith(HOUSE_FILES):
        raise HTTPException(409, "a house register is written by the app each time the furniture changes")
    memory_files.write_file(request.app.state.memory_store, _memory_owner(x_user_id), path, text)
    return Response(status_code=204)


@router.delete("/memory/{path:path}", status_code=204)
def delete_memory(path: str, request: Request, x_user_id: str = Header("demo")) -> Response:
    if path.startswith(HOUSE_FILES):
        raise HTTPException(409, "a house register is written by the app each time the furniture changes")
    _found(memory_files.delete_file(request.app.state.memory_store, _memory_owner(x_user_id), path), "memory file")
    return Response(status_code=204)
