"""The family board: what each person wants from a home (packages/schema/board.py). Owner: B.

    GET    /board?property_id=&household_id=         -> Board
    POST   /board/entries         BoardEntryCreate   -> BoardEntry
    PATCH  /board/entries/{id}    BoardEntryUpdate   -> BoardEntry
    DELETE /board/entries/{id}                       -> 204

The caller is X-User-Id. A family member (their id starts with "m-") reads their family's board
and writes only on their own card. Anyone else is a big screen's user: they name the family on
their screen with `household_id` (none: their own board, with one card) and may write on any card.

Entries made here are written "by hand". The guides write theirs with the board tools
(agent/family.py). These routes send no event: the caller dispatches `board.changed` on its
session, so every screen loads the board again.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Response

from packages.schema.board import BOARD_SHARED, Board, BoardEntry, BoardEntryCreate, BoardEntryUpdate, BoardNote, BoardPerson
from services.api.realtime.households import households, is_member
from services.api.twin.service import TwinNotFound, get_twin

router = APIRouter(tags=["board"])

NOTES_SHOWN = 30  # the newest notes about this house, under the cards


def _board(user_id: str, household_id: str | None) -> str:
    board = households().board_for(user_id, household_id)
    if board is None:
        raise HTTPException(404, "unknown family")
    return board


def _may_write(user_id: str, person_id: str) -> None:
    if is_member(user_id) and person_id != user_id:
        raise HTTPException(403, "You can only change your own card.")


def _entry(entry_id: str) -> BoardEntry:
    entry = households().board_entry(entry_id)
    if entry is None:
        raise HTTPException(404, "no such entry")
    return entry


@router.get("/board")
def get_board(property_id: str | None = None, household_id: str | None = None, x_user_id: str = Header("demo")) -> Board:
    """Everyone's cards. property_id: the house on screen, so only its rooms' wishes are listed
    (what is about no house is always listed) and only the notes about it."""
    store = households()
    board = _board(x_user_id, household_id)
    family = None if board.startswith("solo:") else store.get(board)
    entries: dict[str, list[BoardEntry]] = {}
    for e in store.board_entries(board, property_id):
        entries.setdefault(e.person_id, []).append(e)
    people = [
        BoardPerson(person_id=pid, name=name, shared=pid == BOARD_SHARED, entries=entries.get(pid, []))
        for pid, name in store.board_people(board)
    ]
    notes = [BoardNote.model_validate(n) for n in store.notes(board, NOTES_SHOWN, property_id)] if family else []
    return Board(board_id=board, household_id=family.household_id if family else None, name=family.name if family else None, people=people, notes=notes)


@router.post("/board/entries")
def add_entry(body: BoardEntryCreate, x_user_id: str = Header("demo")) -> BoardEntry:
    store = households()
    board = _board(x_user_id, body.household_id)
    _may_write(x_user_id, body.person_id)
    if body.person_id not in {pid for pid, _ in store.board_people(board)}:
        raise HTTPException(404, "no such person on this board")
    if body.kind == "room" and body.property_id and body.room_id:
        try:
            twin = get_twin(body.property_id)
        except TwinNotFound as e:
            raise HTTPException(404, str(e)) from e
        if not any(r.room_id == body.room_id for r in twin.rooms):
            raise HTTPException(422, f"no room {body.room_id!r} in this house")
    try:
        return store.add_entry(board, body.person_id, body.kind, body.text, "hand", body.room_id, body.property_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.patch("/board/entries/{entry_id}")
def edit_entry(entry_id: str, body: BoardEntryUpdate, x_user_id: str = Header("demo")) -> BoardEntry:
    _may_write(x_user_id, _entry(entry_id).person_id)
    try:
        edited = households().edit_entry(entry_id, body.text, "hand")
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if edited is None:  # deleted between the check and the edit
        raise HTTPException(404, "no such entry")
    return edited


@router.delete("/board/entries/{entry_id}", status_code=204)
def delete_entry(entry_id: str, x_user_id: str = Header("demo")) -> Response:
    _may_write(x_user_id, _entry(entry_id).person_id)
    households().delete_entry(entry_id)
    return Response(status_code=204)
