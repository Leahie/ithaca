"""The invariants the database holds by itself, and the migration from the old tables
(docs/specs/furniture.md)."""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from packages.schema.layout import HousePiece, LayoutVariant, Placement
from packages.schema.twin import Vec3
from services.api.furniture.store import FurnitureStore, norm_yaw

PROPERTY = "onXQLXhfyQv"


def _piece(item_id: str, room_id: str = "r1", x: float = 1.0, yaw: float = 0.0) -> HousePiece:
    return HousePiece(item_id=item_id, property_id=PROPERTY, room_id=room_id, position=Vec3(x=x, y=0, z=2),
                      yaw_deg=yaw, placed_by="alice", updated_at="2026-11-14T00:00:00+00:00")


@pytest.fixture
def db() -> FurnitureStore:
    return FurnitureStore(":memory:")


def _raw(db: FurnitureStore, sql: str, args: tuple) -> None:
    db._db.execute(sql, args)


def test_the_database_refuses_what_breaks_the_rules(db):
    sofa = str(db.list_items("alice")[0].item_id)
    row = ("alice", PROPERTY, sofa, "r1", 0.0, 0.0, 0.0, 10.0, "alice", "t")
    _raw(db, "INSERT INTO in_house VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
    with pytest.raises(sqlite3.IntegrityError):  # F2: once per house
        _raw(db, "INSERT INTO in_house VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row[:3] + ("r2",) + row[4:])
    with pytest.raises(sqlite3.IntegrityError):  # F3: no row for a piece that isn't stored
        _raw(db, "INSERT INTO in_house VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("alice", PROPERTY, str(uuid.uuid4())) + row[3:])
    with pytest.raises(sqlite3.IntegrityError):  # F5
        _raw(db, "INSERT INTO in_house VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("bob",) + row[1:7] + (360.0,) + row[8:])
    with pytest.raises(sqlite3.IntegrityError):  # real sizes
        _raw(db, "INSERT INTO storage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(uuid.uuid4()), "alice", "x", "x", 0.0, 1.0, 1.0, "{}", "t"))


def test_a_piece_moves_instead_of_doubling_and_leaves_with_its_storage_row(db):
    sofa, table, _ = (str(i.item_id) for i in db.list_items("alice"))
    db.put_piece("alice", _piece(sofa, "r1"))
    db.put_piece("alice", _piece(sofa, "r2", x=3))  # F2: the same piece, somewhere else
    db.put_piece("bob", _piece(sofa, "r1"))  # another house may picture it too
    assert [(p.room_id, p.position.x) for p in db.house_pieces("alice", PROPERTY)] == [("r2", 3)]

    db.put_piece("alice", _piece(table, "r2", yaw=359.97))
    assert db.house_piece("alice", PROPERTY, table).yaw_deg == 0.0 == norm_yaw(-0.01)  # F5

    db.put_proposal(LayoutVariant(layout_id=uuid.uuid4(), user_id="alice", property_id=PROPERTY, room_id="r2", score=0, rationale="",
                                  status="proposed", placements=[Placement(item_id=sofa, position=Vec3(x=0, y=0, z=0), yaw_deg=0)]))
    assert db.delete_item(sofa)  # F3: out of every house and every proposal
    assert [str(p.item_id) for p in db.house_pieces("alice", PROPERTY)] == [table]
    assert db.house_pieces("bob", PROPERTY) == [] and db.list_proposals("alice")[0].placements == []
    assert not db.delete_item(sofa)


def test_applying_a_proposal_replaces_its_room_in_one_go(db):
    sofa, table, chair = (str(i.item_id) for i in db.list_items("alice"))
    db.put_piece("alice", _piece(sofa, "r1"))
    db.put_piece("alice", _piece(table, "r1"))
    db.put_piece("alice", _piece(chair, "r2"))  # elsewhere in the house
    layout = LayoutVariant(layout_id=uuid.uuid4(), user_id="alice", property_id=PROPERTY, room_id="r1", score=0, rationale="",
                           status="proposed", placements=[])
    db.put_proposal(layout)
    db.apply_proposal("alice", layout, [_piece(sofa, "r1", x=5), _piece(chair, "r1", x=6)])
    rooms = {str(p.item_id): (p.room_id, p.position.x) for p in db.house_pieces("alice", PROPERTY)}
    assert rooms == {sofa: ("r1", 5), chair: ("r1", 6)}  # the table went back to storage, the chair came over
    assert db.get_proposal("alice", str(layout.layout_id)).status == "chosen"


def test_old_tables_are_migrated_once(tmp_path):
    path = tmp_path / "furniture.sqlite"
    old = sqlite3.connect(path)
    item = json.loads((FurnitureStore(":memory:").list_items("alice")[0]).model_dump_json())
    old.executescript(
        "CREATE TABLE inventory_items (item_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, body TEXT NOT NULL);"
        "CREATE TABLE layouts (layout_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, property_id TEXT NOT NULL, room_id TEXT NOT NULL,"
        " created REAL NOT NULL DEFAULT (julianday('now')), body TEXT NOT NULL);"
    )
    old.execute("INSERT INTO inventory_items VALUES (?, ?, ?)", (item["item_id"], "alice", json.dumps(item)))
    for status in ("proposed", "chosen", "proposed"):  # the same sofa in three layouts of one room
        layout = LayoutVariant(layout_id=uuid.uuid4(), user_id="alice", property_id=PROPERTY, room_id="r1", score=0, rationale="",
                               status=status, placements=[Placement(item_id=item["item_id"], position=Vec3(x=1, y=0, z=1), yaw_deg=360.0)])
        old.execute("INSERT INTO layouts (layout_id, user_id, property_id, room_id, body) VALUES (?, ?, ?, ?, ?)",
                    (str(layout.layout_id), "alice", PROPERTY, "r1", layout.model_dump_json()))
    old.commit()
    old.close()

    db = FurnitureStore(path)
    assert [str(i.item_id) for i in db.list_owned_by(["alice"])][:1] == [item["item_id"]]
    assert len(db.list_proposals("alice")) == 3
    (piece,) = db.house_pieces("alice", PROPERTY)  # only the chosen layout is furniture
    assert piece.yaw_deg == 0.0
    FurnitureStore(path)  # opening again changes nothing
    assert len(FurnitureStore(path).list_proposals("alice")) == 3
