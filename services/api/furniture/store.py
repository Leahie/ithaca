"""Storage, in-house furniture and proposals. Owner: B. Rules: docs/specs/furniture.md (F1-F9).

SQLite for now (data/agent/furniture.sqlite). The rules the database can hold itself are in the
schema: once per house (F2), nothing dangles (F3), yaw range (F5), real sizes. The rules that need
the twin (F4, F6) are in furniture/house.py, which builds every in-house row this store writes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from packages.schema.inventory import InventoryItem
from packages.schema.layout import HousePiece, LayoutVariant
from packages.schema.twin import Vec3
from services.api.integrations.config import REPO_ROOT, env, fixtures_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS storage (
    item_id    TEXT PRIMARY KEY,
    owner_id   TEXT NOT NULL CHECK (owner_id <> ''),
    label      TEXT NOT NULL,
    item_class TEXT NOT NULL,
    w_m REAL NOT NULL CHECK (w_m > 0),
    d_m REAL NOT NULL CHECK (d_m > 0),
    h_m REAL NOT NULL CHECK (h_m > 0),
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS storage_owner ON storage(owner_id);

CREATE TABLE IF NOT EXISTS in_house (
    house_user_id TEXT NOT NULL,
    property_id   TEXT NOT NULL,
    item_id       TEXT NOT NULL REFERENCES storage(item_id) ON DELETE CASCADE,
    room_id       TEXT NOT NULL,
    x REAL NOT NULL, y REAL NOT NULL, z REAL NOT NULL,
    yaw_deg       REAL NOT NULL CHECK (yaw_deg >= 0 AND yaw_deg < 360),
    placed_by     TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (house_user_id, property_id, item_id)
);
CREATE INDEX IF NOT EXISTS in_house_room ON in_house(house_user_id, property_id, room_id);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id   TEXT PRIMARY KEY,
    house_user_id TEXT NOT NULL,
    property_id   TEXT NOT NULL,
    room_id       TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('proposed', 'chosen', 'rejected')),
    created       REAL NOT NULL DEFAULT (julianday('now')),
    body          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS proposals_room ON proposals(house_user_id, property_id, room_id);

CREATE TABLE IF NOT EXISTS seeded (user_id TEXT PRIMARY KEY);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm_yaw(deg: float) -> float:
    """F5: one decimal, in [0, 360). Rounding 359.96 gives 360.0, which is 0."""
    yaw = round(deg % 360, 1)
    return 0.0 if yaw >= 360 else yaw



def house_key(house_user_id: str) -> str:
    """Which house the rows belong to. A big screen with a family on shows that family's house, so
    each family furnishes its own and a new family starts with an empty one. Every caller still
    passes the screen's user; a screen with no family keeps its own house."""
    from services.api.realtime.households import households

    return households().current_for(house_user_id) or house_user_id


class FurnitureStore:
    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path = REPO_ROOT / "data" / "agent" / "furniture.sqlite"
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA foreign_keys = ON")  # F3: off by default in SQLite, per connection
        self._lock = threading.RLock()
        self._db.executescript(SCHEMA)
        self._migrate()

    def _run(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One transaction: all of it happens or none of it."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    # --- storage --------------------------------------------------------------------------------

    def _seed(self, user_id: str) -> None:
        """Dev convenience: a new user starts with the three sample items from fixtures/inventory.
        Family members (m-...) bring their own pieces; the samples come with the house user.
        SEED_DEMO_INVENTORY=0 turns it off."""
        if user_id.startswith("m-") or self._run("SELECT 1 FROM seeded WHERE user_id = ?", (user_id,)):
            return
        self._run("INSERT OR IGNORE INTO seeded VALUES (?)", (user_id,))
        path = fixtures_dir() / "inventory" / "demo.json"
        if env("SEED_DEMO_INVENTORY", "1") == "0" or not path.exists():
            return
        for row in json.loads(path.read_text(encoding="utf-8")):
            # the sample ids are kept for the demo user so the sample layouts still point at them
            item_id = row["item_id"] if user_id == row["user_id"] else str(uuid.uuid5(uuid.UUID(row["item_id"]), user_id))
            if not self._run("SELECT 1 FROM storage WHERE item_id = ?", (item_id,)):
                self.put_item(InventoryItem.model_validate({**row, "user_id": user_id, "item_id": item_id}))

    def put_item(self, item: InventoryItem) -> None:
        body = item.model_copy(update={"owner_name": None}).model_dump_json()  # names are looked up when listed
        self._run(
            "INSERT INTO storage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(item_id) DO UPDATE SET "
            "owner_id = excluded.owner_id, label = excluded.label, item_class = excluded.item_class, "
            "w_m = excluded.w_m, d_m = excluded.d_m, h_m = excluded.h_m, body = excluded.body",
            (str(item.item_id), item.user_id, item.label, item.item_class, item.dims_m.w, item.dims_m.d, item.dims_m.h, body, now()),
        )

    def seed(self, owner_ids: Iterable[str]) -> list[str]:
        """Give each owner the sample pieces the first time they are seen (_seed)."""
        owners = sorted(set(owner_ids))
        for owner in owners:
            self._seed(owner)
        return owners

    def get_item(self, item_id: str) -> InventoryItem | None:
        rows = self._run("SELECT body FROM storage WHERE item_id = ?", (str(item_id),))
        return InventoryItem.model_validate_json(rows[0][0]) if rows else None

    def list_items(self, owner_id: str) -> list[InventoryItem]:
        """One person's own pieces."""
        return self.list_owned_by([owner_id])

    def list_owned_by(self, owner_ids: Iterable[str]) -> list[InventoryItem]:
        """The pieces of several people (a family pool, F6), oldest first."""
        owners = self.seed(owner_ids)
        if not owners:
            return []
        marks = ",".join("?" * len(owners))
        rows = self._run(f"SELECT body FROM storage WHERE owner_id IN ({marks}) ORDER BY rowid", tuple(owners))
        return [InventoryItem.model_validate_json(body) for (body,) in rows]

    def delete_item(self, item_id: str) -> bool:
        """Delete a piece for good. It leaves every house (F3, by the foreign key) and every
        proposal. False if there is no such piece."""
        item_id = str(item_id)
        with self._tx() as db:
            if db.execute("DELETE FROM storage WHERE item_id = ?", (item_id,)).rowcount == 0:
                return False
            for pid, body in db.execute("SELECT proposal_id, body FROM proposals").fetchall():
                layout = LayoutVariant.model_validate_json(body)
                kept = [p for p in layout.placements if str(p.item_id) != item_id]
                if len(kept) != len(layout.placements):
                    db.execute("UPDATE proposals SET body = ? WHERE proposal_id = ?", (layout.model_copy(update={"placements": kept}).model_dump_json(), pid))
        return True

    # --- in the house ---------------------------------------------------------------------------

    @staticmethod
    def _piece(row: tuple) -> HousePiece:
        property_id, item_id, room_id, x, y, z, yaw, placed_by, updated_at = row
        return HousePiece(item_id=item_id, property_id=property_id, room_id=room_id, position=Vec3(x=x, y=y, z=z),
                          yaw_deg=yaw, placed_by=placed_by, updated_at=updated_at)

    _PIECE_COLUMNS = "property_id, item_id, room_id, x, y, z, yaw_deg, placed_by, updated_at"

    def house_pieces(self, house_user_id: str, property_id: str, room_id: str | None = None) -> list[HousePiece]:
        house_user_id = house_key(house_user_id)
        rows = self._run(
            f"SELECT {self._PIECE_COLUMNS} FROM in_house WHERE house_user_id = ? AND property_id = ? "
            "AND (? IS NULL OR room_id = ?) ORDER BY updated_at, rowid",
            (house_user_id, property_id, room_id, room_id),
        )
        return [self._piece(r) for r in rows]

    def house_piece(self, house_user_id: str, property_id: str, item_id: str) -> HousePiece | None:
        house_user_id = house_key(house_user_id)
        rows = self._run(
            f"SELECT {self._PIECE_COLUMNS} FROM in_house WHERE house_user_id = ? AND property_id = ? AND item_id = ?",
            (house_user_id, property_id, str(item_id)),
        )
        return self._piece(rows[0]) if rows else None

    @staticmethod
    def _upsert(db: sqlite3.Connection, house_user_id: str, piece: HousePiece) -> None:
        p = piece.position
        db.execute(
            "INSERT INTO in_house VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(house_user_id, property_id, item_id) DO UPDATE SET "
            "room_id = excluded.room_id, x = excluded.x, y = excluded.y, z = excluded.z, yaw_deg = excluded.yaw_deg, "
            "placed_by = excluded.placed_by, updated_at = excluded.updated_at",
            (house_user_id, piece.property_id, str(piece.item_id), piece.room_id, p.x, p.y, p.z, norm_yaw(piece.yaw_deg), piece.placed_by, piece.updated_at),
        )

    def put_piece(self, house_user_id: str, piece: HousePiece) -> HousePiece:
        house_user_id = house_key(house_user_id)
        """Place or move one piece (F2: a piece already in the house moves, it is never doubled).
        The caller has checked F4 and F6 (furniture/house.py)."""
        with self._tx() as db:
            self._upsert(db, house_user_id, piece)
        return piece.model_copy(update={"yaw_deg": norm_yaw(piece.yaw_deg), "warnings": None})

    def remove_piece(self, house_user_id: str, property_id: str, item_id: str) -> bool:
        house_user_id = house_key(house_user_id)
        """Take a piece out of the house (F7). It stays in storage. False if it wasn't there."""
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM in_house WHERE house_user_id = ? AND property_id = ? AND item_id = ?",
                (house_user_id, property_id, str(item_id)),
            )
            return cur.rowcount > 0

    # --- proposals ------------------------------------------------------------------------------

    def put_proposal(self, layout: LayoutVariant) -> None:
        self._run(
            "INSERT INTO proposals (proposal_id, house_user_id, property_id, room_id, status, body) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(proposal_id) DO UPDATE SET status = excluded.status, body = excluded.body",
            (str(layout.layout_id), house_key(layout.user_id), layout.property_id, layout.room_id, layout.status, layout.model_dump_json()),
        )

    def list_proposals(self, house_user_id: str, property_id: str | None = None, room_id: str | None = None) -> list[LayoutVariant]:
        house_user_id = house_key(house_user_id)
        rows = self._run(
            "SELECT body FROM proposals WHERE house_user_id = ? AND (? IS NULL OR property_id = ?) AND (? IS NULL OR room_id = ?) "
            "ORDER BY created DESC, rowid DESC",
            (house_user_id, property_id, property_id, room_id, room_id),
        )
        return [LayoutVariant.model_validate_json(body) for (body,) in rows]

    def get_proposal(self, house_user_id: str, layout_id: str) -> LayoutVariant | None:
        rows = self._run("SELECT body FROM proposals WHERE house_user_id = ? AND proposal_id = ?", (house_key(house_user_id), str(layout_id)))
        if rows:
            return LayoutVariant.model_validate_json(rows[0][0])
        return next((l for l in _sample_layouts() if str(l.layout_id) == str(layout_id) and l.user_id == house_user_id), None)

    def set_feedback(self, house_user_id: str, layout_id: str, feedback: str) -> LayoutVariant | None:
        layout = self.get_proposal(house_user_id, layout_id)
        if layout is None:
            return None
        updated = layout.model_copy(update={"feedback": feedback})
        self.put_proposal(updated)
        return updated

    def apply_proposal(self, house_user_id: str, layout: LayoutVariant, pieces: list[HousePiece]) -> LayoutVariant:
        house_user_id = house_key(house_user_id)
        """F8, in one transaction: the room's pieces become exactly `pieces` (built and checked by
        furniture/house.py from the proposal), the proposal is chosen and the room's previous
        choice goes back to proposed."""
        chosen = layout.model_copy(update={"status": "chosen"})
        with self._tx() as db:
            db.execute(
                "DELETE FROM in_house WHERE house_user_id = ? AND property_id = ? AND room_id = ?",
                (house_user_id, layout.property_id, layout.room_id),
            )
            for piece in pieces:
                self._upsert(db, house_user_id, piece)
            for pid, body in db.execute(
                "SELECT proposal_id, body FROM proposals WHERE house_user_id = ? AND property_id = ? AND room_id = ? AND status = 'chosen'",
                (house_user_id, layout.property_id, layout.room_id),
            ).fetchall():
                if pid != str(layout.layout_id):
                    back = LayoutVariant.model_validate_json(body).model_copy(update={"status": "proposed"})
                    db.execute("UPDATE proposals SET status = 'proposed', body = ? WHERE proposal_id = ?", (back.model_dump_json(), pid))
            db.execute(
                "INSERT INTO proposals (proposal_id, house_user_id, property_id, room_id, status, body) VALUES (?, ?, ?, ?, 'chosen', ?) "
                "ON CONFLICT(proposal_id) DO UPDATE SET status = 'chosen', body = excluded.body",
                (str(layout.layout_id), house_user_id, layout.property_id, layout.room_id, chosen.model_dump_json()),
            )
        return chosen

    # --- migration ------------------------------------------------------------------------------

    def _migrate(self) -> None:
        """Once, from the tables before docs/specs/furniture.md: items go to storage, layouts
        become proposals, and each room's chosen layout is written into the house."""
        tables = {name for (name,) in self._run("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "inventory_items" in tables:
            for (body,) in self._run("SELECT body FROM inventory_items ORDER BY rowid"):
                item = InventoryItem.model_validate_json(body)
                if self.get_item(str(item.item_id)) is None:
                    self.put_item(item)
            self._run("ALTER TABLE inventory_items RENAME TO inventory_items_old")
        if "layouts" in tables:
            for (body,) in self._run("SELECT body FROM layouts ORDER BY created, rowid"):
                layout = LayoutVariant.model_validate_json(body)
                self.put_proposal(layout)
                if layout.status != "chosen":
                    continue
                with self._tx() as db:
                    for p in layout.placements:
                        if db.execute("SELECT 1 FROM storage WHERE item_id = ?", (str(p.item_id),)).fetchone():
                            piece = HousePiece(item_id=p.item_id, property_id=layout.property_id, room_id=layout.room_id,
                                               position=p.position, yaw_deg=p.yaw_deg, placed_by=layout.user_id, updated_at=now())
                            self._upsert(db, layout.user_id, piece)
            self._run("ALTER TABLE layouts RENAME TO layouts_old")


def _sample_layouts() -> list[LayoutVariant]:
    """Hand-placed layouts in fixtures/layouts/*.json (what the scripted agent shows)."""
    out: list[LayoutVariant] = []
    for path in sorted((fixtures_dir() / "layouts").glob("*.json")):
        out += [LayoutVariant.model_validate(row) for row in json.loads(path.read_text(encoding="utf-8"))]
    return out


_default: FurnitureStore | None = None


def furniture_store() -> FurnitureStore:
    """The process-wide store. Tools and routes share it."""
    global _default
    if _default is None:
        _default = FurnitureStore()
    return _default
