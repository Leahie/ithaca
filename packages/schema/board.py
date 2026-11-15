"""The family board: what each person wants from a home, kept up to date by their own guide.
Owner: B. Consumer: A.

One card per person, plus one for the big screen itself (what was said to the shared guide). An
entry is one short fact. The guides write most of them; anyone may add or delete one by hand.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from .common import Model

# about: who they are and how they live, only what bears on a home. must: must-haves, hard rules.
# like: style and taste. avoid: dislikes. room: a wish for one room of one house.
BoardKind = Literal["about", "must", "like", "avoid", "room"]
# Who wrote the entry's words: the person's guide, or someone typing on the board.
BoardBy = Literal["guide", "hand"]

# person_id of the big screen's own card. Every other person_id is a member_id.
BOARD_SHARED = "shared"


class BoardEntry(Model):
    entry_id: str
    person_id: str  # a member_id, or "shared" for the big screen's card
    kind: BoardKind
    text: str  # one short fact
    # Set on a "room" entry only: the room the wish is for, and the house that room is in.
    room_id: str | None = None
    property_id: str | None = None
    by: BoardBy
    created_at: datetime
    updated_at: datetime


class BoardPerson(Model):
    person_id: str
    name: str
    # True for the big screen's card: the family's shared guide writes it. Alone, it is "You".
    shared: bool = False
    entries: list[BoardEntry] = []


class BoardNote(Model):
    """A note about this house that one member's guide shared with the family (`family.note`)."""

    note_id: str
    member_id: str
    name: str | None = None  # the member's name; None when the shared guide wrote it
    text: str
    room_id: str | None = None
    created_at: datetime


class Board(Model):
    """Response of GET /board."""

    board_id: str  # the household_id, or one per big-screen user who has no family
    household_id: str | None = None
    name: str | None = None  # the family's name
    people: list[BoardPerson]  # the shared card first, then the members in the order they joined
    notes: list[BoardNote] = []  # oldest first


class BoardEntryCreate(Model):
    """Body of POST /board/entries."""

    person_id: str
    kind: BoardKind
    text: str
    room_id: str | None = None  # needed, with property_id, when kind is "room"
    property_id: str | None = None
    # The family on the caller's screen. A member's phone leaves it out: its family is known.
    household_id: str | None = None


class BoardEntryUpdate(Model):
    """Body of PATCH /board/entries/{entry_id}."""

    text: str
