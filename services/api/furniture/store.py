"""Storage, in-house furniture and proposals. Owner: B. Rules: docs/specs/furniture.md (F1-F12).

SQLite for now (data/agent/furniture.sqlite). The rules the database can hold itself are in the
schema: once per house (F2), nothing dangles (F3), yaw range (F5), real sizes, one number per
piece (F10). The rules that need the twin (F4, F6) are in furniture/house.py, which builds every
in-house row this store writes.

A small thing may stand on a piece of furniture, or hang on a wall above the floor (F12): its
row names the piece it rests on and how high it is, and it moves, turns and leaves with that piece.

Every change to what stands in a house is also written to that house's log (F11): which piece,
which room, who, and how. furniture/ledger.py reads the house and the log back as the register
the guides are shown, and is told here (`subscribe`) each time a house changes.
"""

from __future__ import annotations

import json
import logging
import math
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from packages.schema.inventory import InventoryItem
from packages.schema.layout import HousePiece, LayoutVariant
from packages.schema.twin import Vec3
from services.api.integrations.config import REPO_ROOT, env, fixtures_dir

log = logging.getLogger(__name__)

# How a change to the house came about: by hand (a drag, a tap on a card), by keeping a layout
# the designer proposed, by a guide's own tool call, or from a photo of the person's own room.
HAND, LAYOUT, GUIDE, PHOTO = "hand", "layout", "guide", "photo"
# A piece nudged again by the same person within this long is one move in the log, not many.
SAME_MOVE_S = 120
LOG_KEPT = 400  # log rows kept per house

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
    created_at TEXT NOT NULL,
    tag        TEXT
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
    on_item_id    TEXT,
    lift_m        REAL NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS house_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    house_key   TEXT NOT NULL,
    property_id TEXT NOT NULL,
    at          TEXT NOT NULL,
    action      TEXT NOT NULL CHECK (action IN ('placed', 'moved', 'removed', 'deleted')),
    item_id     TEXT NOT NULL,
    tag         TEXT,
    label       TEXT NOT NULL,
    room_id     TEXT,
    by          TEXT NOT NULL,
    via         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS house_log_house ON house_log(house_key, property_id, seq);
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
        self._samples = env("SEED_DEMO_INVENTORY", "1" if str(path) == ":memory:" else "0") == "1"
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA foreign_keys = ON")  # F3: off by default in SQLite, per connection
        self._lock = threading.RLock()
        self._listeners: list[Callable[[str, str], None]] = []
        self._db.executescript(SCHEMA)
        self._migrate()

    # --- told when a house changes ----------------------------------------------------------------

    def subscribe(self, listener: Callable[[str, str], None]) -> None:
        """Call `listener(house_key, property_id)` after every change to what stands in a house."""
        if listener not in self._listeners:
            self._listeners.append(listener)

    def _changed(self, houses: Iterable[tuple[str, str]]) -> None:
        for house_key, property_id in dict.fromkeys(houses):
            for listener in self._listeners:
                try:
                    listener(house_key, property_id)
                except Exception:  # the change is saved; a register that could not be written must not undo it
                    log.exception("furniture listener failed for %s in %s", house_key, property_id)

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
        Off in the real database, where pieces nobody added read as invented; SEED_DEMO_INVENTORY=1
        turns it on. On for in-memory stores (tests)."""
        if user_id.startswith("m-") or self._run("SELECT 1 FROM seeded WHERE user_id = ?", (user_id,)):
            return
        self._run("INSERT OR IGNORE INTO seeded VALUES (?)", (user_id,))
        path = fixtures_dir() / "inventory" / "demo.json"
        if not self._samples or not path.exists():
            return
        for row in json.loads(path.read_text(encoding="utf-8")):
            # the sample ids are kept for the demo user so the sample layouts still point at them
            item_id = row["item_id"] if user_id == row["user_id"] else str(uuid.uuid5(uuid.UUID(row["item_id"]), user_id))
            if not self._run("SELECT 1 FROM storage WHERE item_id = ?", (item_id,)):
                self.put_item(InventoryItem.model_validate({**row, "user_id": user_id, "item_id": item_id}))

    @staticmethod
    def _new_tag(db: sqlite3.Connection) -> str:
        """F10: a random number no piece has or had: four digits while they last, then more."""
        taken = {t for (t,) in db.execute("SELECT tag FROM storage WHERE tag IS NOT NULL UNION SELECT tag FROM house_log WHERE tag IS NOT NULL")}
        digits = 4
        while True:
            low = 10 ** (digits - 1)
            for _ in range(50):
                tag = str(low + secrets.randbelow(9 * low))
                if tag not in taken:
                    return tag
            digits += 1

    @staticmethod
    def _item(body: str, tag: str | None) -> InventoryItem:
        return InventoryItem.model_validate_json(body).model_copy(update={"tag": tag})

    def put_item(self, item: InventoryItem) -> InventoryItem:
        """Save a piece. A new piece gets its number here (F10); a known one keeps the one it has.
        Returns the piece as stored, with its number."""
        with self._tx() as db:
            row = db.execute("SELECT tag FROM storage WHERE item_id = ?", (str(item.item_id),)).fetchone()
            tag = (row[0] if row else None) or self._new_tag(db)
            stored = item.model_copy(update={"owner_name": None, "tag": tag})  # names are looked up when listed
            db.execute(
                "INSERT INTO storage (item_id, owner_id, label, item_class, w_m, d_m, h_m, body, created_at, tag) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(item_id) DO UPDATE SET "
                "owner_id = excluded.owner_id, label = excluded.label, item_class = excluded.item_class, "
                "w_m = excluded.w_m, d_m = excluded.d_m, h_m = excluded.h_m, body = excluded.body, tag = excluded.tag",
                (str(item.item_id), item.user_id, item.label, item.item_class, item.dims_m.w, item.dims_m.d, item.dims_m.h,
                 stored.model_dump_json(), now(), tag),
            )
        return item.model_copy(update={"tag": tag})

    def seed(self, owner_ids: Iterable[str]) -> list[str]:
        """Give each owner the sample pieces the first time they are seen (_seed)."""
        owners = sorted(set(owner_ids))
        for owner in owners:
            self._seed(owner)
        return owners

    def get_item(self, item_id: str) -> InventoryItem | None:
        rows = self._run("SELECT body, tag FROM storage WHERE item_id = ?", (str(item_id),))
        return self._item(*rows[0]) if rows else None

    def find_item(self, ref: str) -> InventoryItem | None:
        """A piece by its number as people write it ("4821", "#4821", "piece 4821") or its item_id."""
        ref = str(ref).strip()
        digits = ref.lower().removeprefix("piece").strip().lstrip("#").strip()
        if digits.isdigit():
            rows = self._run("SELECT body, tag FROM storage WHERE tag = ?", (digits,))
            return self._item(*rows[0]) if rows else None
        return self.get_item(ref)

    def list_items(self, owner_id: str) -> list[InventoryItem]:
        """One person's own pieces."""
        return self.list_owned_by([owner_id])

    def list_owned_by(self, owner_ids: Iterable[str]) -> list[InventoryItem]:
        """The pieces of several people (a family pool, F6), oldest first."""
        owners = self.seed(owner_ids)
        if not owners:
            return []
        marks = ",".join("?" * len(owners))
        rows = self._run(f"SELECT body, tag FROM storage WHERE owner_id IN ({marks}) ORDER BY rowid", tuple(owners))
        return [self._item(body, tag) for body, tag in rows]

    def delete_item(self, item_id: str, by: str | None = None, via: str = HAND) -> bool:
        """Delete a piece for good. It leaves every house (F3, by the foreign key) and every
        proposal. False if there is no such piece."""
        item_id = str(item_id)
        with self._tx() as db:
            stood = db.execute("SELECT house_user_id, property_id, room_id FROM in_house WHERE item_id = ?", (item_id,)).fetchall()
            owner = db.execute("SELECT owner_id FROM storage WHERE item_id = ?", (item_id,)).fetchone()
            for house_key, property_id, room_id in stood:  # while the piece can still be named
                self._log(db, house_key, property_id, "deleted", item_id, room_id, by or (owner[0] if owner else "?"), via)
                self._drop_riders(db, house_key, property_id, item_id, by or (owner[0] if owner else "?"), via)
            if db.execute("DELETE FROM storage WHERE item_id = ?", (item_id,)).rowcount == 0:
                return False
            for pid, body in db.execute("SELECT proposal_id, body FROM proposals").fetchall():
                layout = LayoutVariant.model_validate_json(body)
                kept = [p for p in layout.placements if str(p.item_id) != item_id]
                if len(kept) != len(layout.placements):
                    db.execute("UPDATE proposals SET body = ? WHERE proposal_id = ?", (layout.model_copy(update={"placements": kept}).model_dump_json(), pid))
        self._changed((house_key, property_id) for house_key, property_id, _ in stood)
        return True

    # --- the house's log (F11) --------------------------------------------------------------------

    @staticmethod
    def _log(db: sqlite3.Connection, house_key: str, property_id: str, action: str, item_id: str, room_id: str | None, by: str, via: str) -> None:
        """One line of a house's history. Call inside the transaction that makes the change."""
        named = db.execute("SELECT tag, label FROM storage WHERE item_id = ?", (item_id,)).fetchone()
        tag, label = named if named else (None, "a piece")
        if action == "moved":
            # Dragging a piece about is one move: the last line is brought up to date instead.
            last = db.execute(
                "SELECT seq, action, item_id, by, via, at FROM house_log WHERE house_key = ? AND property_id = ? ORDER BY seq DESC LIMIT 1",
                (house_key, property_id),
            ).fetchone()
            if last and last[1:5] == ("moved", item_id, by, via) and (datetime.now(timezone.utc) - datetime.fromisoformat(last[5])).total_seconds() < SAME_MOVE_S:
                db.execute("UPDATE house_log SET at = ?, room_id = ? WHERE seq = ?", (now(), room_id, last[0]))
                return
        db.execute(
            "INSERT INTO house_log (house_key, property_id, at, action, item_id, tag, label, room_id, by, via) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (house_key, property_id, now(), action, item_id, tag, label, room_id, by, via),
        )
        db.execute(
            "DELETE FROM house_log WHERE house_key = ? AND property_id = ? AND seq <= "
            "(SELECT seq FROM house_log WHERE house_key = ? AND property_id = ? ORDER BY seq DESC LIMIT 1 OFFSET ?)",
            (house_key, property_id, house_key, property_id, LOG_KEPT),
        )

    def house_log(self, house_user_id: str, property_id: str, limit: int = 12, keyed: bool = False) -> list[dict]:
        """The last changes to a house, oldest first: at, action, item_id, tag, label, room_id, by, via."""
        house_key_ = house_user_id if keyed else house_key(house_user_id)
        rows = self._run(
            "SELECT at, action, item_id, tag, label, room_id, by, via FROM house_log WHERE house_key = ? AND property_id = ? ORDER BY seq DESC LIMIT ?",
            (house_key_, property_id, max(1, limit)),
        )
        names = ("at", "action", "item_id", "tag", "label", "room_id", "by", "via")
        return [dict(zip(names, r)) for r in reversed(rows)]

    # --- in the house ---------------------------------------------------------------------------

    @staticmethod
    def _piece(row: tuple) -> HousePiece:
        property_id, item_id, room_id, x, y, z, yaw, placed_by, updated_at, on_item_id, lift_m = row
        return HousePiece(item_id=item_id, property_id=property_id, room_id=room_id, position=Vec3(x=x, y=y, z=z),
                          yaw_deg=yaw, placed_by=placed_by, updated_at=updated_at, on_item_id=on_item_id, lift_m=lift_m or 0.0)

    _PIECE_COLUMNS = "property_id, item_id, room_id, x, y, z, yaw_deg, placed_by, updated_at, on_item_id, lift_m"

    def house_pieces(self, house_user_id: str, property_id: str, room_id: str | None = None, keyed: bool = False) -> list[HousePiece]:
        """What stands in a house (or one room of it): the pieces on the floor first, then what
        rests on them or hangs on the walls, so a list drawn in order puts a lamp over its table.
        keyed: `house_user_id` is already the house's key (what listeners are handed), not a screen's user."""
        house_user_id = house_user_id if keyed else house_key(house_user_id)
        rows = self._run(
            f"SELECT {self._PIECE_COLUMNS} FROM in_house WHERE house_user_id = ? AND property_id = ? "
            "AND (? IS NULL OR room_id = ?) ORDER BY (on_item_id IS NOT NULL OR lift_m > 0), updated_at, rowid",
            (house_user_id, property_id, room_id, room_id),
        )
        return [self._piece(r) for r in rows]

    def stands_in_house(self, house_user_id: str, item_id: str) -> bool:
        """True if this piece stands in this screen's house (any property)."""
        rows = self._run("SELECT 1 FROM in_house WHERE house_user_id = ? AND item_id = ? LIMIT 1", (house_key(house_user_id), str(item_id)))
        return bool(rows)

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
            "INSERT INTO in_house (house_user_id, property_id, item_id, room_id, x, y, z, yaw_deg, placed_by, updated_at, on_item_id, lift_m) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(house_user_id, property_id, item_id) DO UPDATE SET "
            "room_id = excluded.room_id, x = excluded.x, y = excluded.y, z = excluded.z, yaw_deg = excluded.yaw_deg, "
            "placed_by = excluded.placed_by, updated_at = excluded.updated_at, on_item_id = excluded.on_item_id, lift_m = excluded.lift_m",
            (house_user_id, piece.property_id, str(piece.item_id), piece.room_id, p.x, p.y, p.z, norm_yaw(piece.yaw_deg), piece.placed_by,
             piece.updated_at, str(piece.on_item_id) if piece.on_item_id else None, piece.lift_m),
        )

    # --- what rests on a piece (F12) --------------------------------------------------------------

    @staticmethod
    def _carry(db: sqlite3.Connection, house_user_id: str, was: tuple, now_: HousePiece) -> None:
        """A piece moved from `was` (room_id, x, y, z, yaw_deg) to where `now_` says: everything
        resting on it goes along, turned about it by as much as it turned."""
        _room, ox, oy, oz, oyaw = was
        turn = math.radians(now_.yaw_deg - oyaw)
        cos, sin = math.cos(turn), math.sin(turn)
        riders = db.execute(
            "SELECT item_id, x, y, z, yaw_deg FROM in_house WHERE house_user_id = ? AND property_id = ? AND on_item_id = ?",
            (house_user_id, now_.property_id, str(now_.item_id)),
        ).fetchall()
        for item_id, x, y, z, yaw in riders:
            dx, dz = x - ox, z - oz  # a piece's front is (sin yaw, cos yaw): turning by `turn` takes (dx, dz) here
            db.execute(
                "UPDATE in_house SET room_id = ?, x = ?, y = ?, z = ?, yaw_deg = ?, updated_at = ? WHERE house_user_id = ? AND property_id = ? AND item_id = ?",
                (now_.room_id, round(now_.position.x + dx * cos + dz * sin, 3), y + (now_.position.y - oy), round(now_.position.z - dx * sin + dz * cos, 3),
                 norm_yaw(yaw + math.degrees(turn)), now_.updated_at, house_user_id, now_.property_id, item_id),
            )

    def _drop_riders(self, db: sqlite3.Connection, house_user_id: str, property_id: str, item_id: str, by: str, via: str) -> None:
        """The piece `item_id` has left its spot for good: what rested on it goes back to storage."""
        for rider, room_id in db.execute(
            "SELECT item_id, room_id FROM in_house WHERE house_user_id = ? AND property_id = ? AND on_item_id = ?", (house_user_id, property_id, item_id)
        ).fetchall():
            db.execute("DELETE FROM in_house WHERE house_user_id = ? AND property_id = ? AND item_id = ?", (house_user_id, property_id, rider))
            self._log(db, house_user_id, property_id, "removed", rider, room_id, by, via)

    def put_piece(self, house_user_id: str, piece: HousePiece, via: str = HAND) -> HousePiece:
        """Place or move one piece (F2: a piece already in the house moves, it is never doubled).
        The caller has checked F4 and F6 (furniture/house.py)."""
        house_user_id = house_key(house_user_id)
        with self._tx() as db:
            was = db.execute(
                "SELECT room_id, x, y, z, yaw_deg FROM in_house WHERE house_user_id = ? AND property_id = ? AND item_id = ?",
                (house_user_id, piece.property_id, str(piece.item_id)),
            ).fetchone()
            self._upsert(db, house_user_id, piece)
            if was:
                if piece.on_item_id or piece.lift_m > 0:
                    # It now rests on something itself, or hangs on a wall: nothing can rest on it there.
                    self._drop_riders(db, house_user_id, piece.property_id, str(piece.item_id), piece.placed_by, via)
                else:
                    self._carry(db, house_user_id, was, piece)
            self._log(db, house_user_id, piece.property_id, "moved" if was else "placed", str(piece.item_id), piece.room_id, piece.placed_by, via)
        self._changed([(house_user_id, piece.property_id)])
        return piece.model_copy(update={"yaw_deg": norm_yaw(piece.yaw_deg), "warnings": None})

    def remove_piece(self, house_user_id: str, property_id: str, item_id: str, by: str | None = None, via: str = HAND) -> bool:
        """Take a piece out of the house (F7). It stays in storage. False if it wasn't there."""
        house_user_id = house_key(house_user_id)
        with self._tx() as db:
            was = db.execute(
                "SELECT room_id, placed_by FROM in_house WHERE house_user_id = ? AND property_id = ? AND item_id = ?",
                (house_user_id, property_id, str(item_id)),
            ).fetchone()
            if was is None:
                return False
            db.execute("DELETE FROM in_house WHERE house_user_id = ? AND property_id = ? AND item_id = ?", (house_user_id, property_id, str(item_id)))
            self._log(db, house_user_id, property_id, "removed", str(item_id), was[0], by or was[1], via)
            self._drop_riders(db, house_user_id, property_id, str(item_id), by or was[1], via)
        self._changed([(house_user_id, property_id)])
        return True

    def set_room(self, house_user_id: str, property_id: str, room_id: str, pieces: list[HousePiece], by: str, via: str = PHOTO) -> None:
        """One room becomes exactly `pieces`, in one transaction: on its floor, on those pieces and
        on its walls. Everything else that was in the room goes back to storage. The caller has
        built the rows (furniture/own_room.py): pieces on the floor come before what rests on them."""
        house_user_id = house_key(house_user_id)
        coming = {str(p.item_id) for p in pieces}
        with self._tx() as db:
            before = dict(db.execute("SELECT item_id, room_id FROM in_house WHERE house_user_id = ? AND property_id = ?", (house_user_id, property_id)).fetchall())
            for item_id, was_in in before.items():
                if was_in == room_id and item_id not in coming:
                    db.execute("DELETE FROM in_house WHERE house_user_id = ? AND property_id = ? AND item_id = ?", (house_user_id, property_id, item_id))
                    self._log(db, house_user_id, property_id, "removed", item_id, room_id, by, via)
            for piece in pieces:
                if str(piece.item_id) in before:  # it stood elsewhere in the house: what rested on it there stays behind, in storage
                    self._drop_riders(db, house_user_id, property_id, str(piece.item_id), by, via)
                self._upsert(db, house_user_id, piece)
                self._log(db, house_user_id, property_id, "moved" if str(piece.item_id) in before else "placed", str(piece.item_id), piece.room_id, by, via)
        self._changed([(house_user_id, property_id)])

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

    def apply_proposal(self, house_user_id: str, layout: LayoutVariant, pieces: list[HousePiece], by: str | None = None) -> LayoutVariant:
        """F8, in one transaction: the room's pieces become exactly `pieces` (built and checked by
        furniture/house.py from the proposal), the proposal is chosen and the room's previous
        choice goes back to proposed."""
        house_user_id = house_key(house_user_id)
        chosen = layout.model_copy(update={"status": "chosen"})
        who = by or (pieces[0].placed_by if pieces else layout.user_id)
        with self._tx() as db:
            rows = db.execute(
                "SELECT item_id, room_id, x, y, z, yaw_deg, on_item_id, lift_m FROM in_house WHERE house_user_id = ? AND property_id = ?",
                (house_user_id, layout.property_id),
            ).fetchall()
            before = {r[0]: r[1] for r in rows}
            on_floor = {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in rows if r[6] is None and not r[7]}
            staying = {str(p.item_id) for p in pieces}
            # The proposal arranges what stands on the floor (F8). What rests on a piece goes with
            # it, and what hangs on a wall stays where it hangs (F12).
            db.execute(
                "DELETE FROM in_house WHERE house_user_id = ? AND property_id = ? AND room_id = ? AND on_item_id IS NULL AND lift_m = 0",
                (house_user_id, layout.property_id, layout.room_id),
            )
            for piece in pieces:
                self._upsert(db, house_user_id, piece)
                if str(piece.item_id) in on_floor:
                    self._carry(db, house_user_id, on_floor[str(piece.item_id)], piece)
            for item_id, (room_id, *_pose) in on_floor.items():  # what the layout sent back to storage
                if room_id == layout.room_id and item_id not in staying:
                    self._log(db, house_user_id, layout.property_id, "removed", item_id, room_id, who, LAYOUT)
                    self._drop_riders(db, house_user_id, layout.property_id, item_id, who, LAYOUT)
            for piece in pieces:
                self._log(db, house_user_id, layout.property_id, "moved" if str(piece.item_id) in before else "placed", str(piece.item_id), piece.room_id, who, LAYOUT)
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
        self._changed([(house_user_id, layout.property_id)])
        return chosen

    # --- migration ------------------------------------------------------------------------------

    def _migrate(self) -> None:
        """Once, from the tables before docs/specs/furniture.md: items go to storage, layouts
        become proposals, and each room's chosen layout is written into the house."""
        # F12: a database from before pieces could rest on one another gets the two columns.
        columns = {row[1] for row in self._run("PRAGMA table_info(in_house)")}
        if "on_item_id" not in columns:
            self._run("ALTER TABLE in_house ADD COLUMN on_item_id TEXT")
        if "lift_m" not in columns:
            self._run("ALTER TABLE in_house ADD COLUMN lift_m REAL NOT NULL DEFAULT 0")
        # F10: a database from before pieces had numbers gets the column, and every piece a number.
        if "tag" not in {row[1] for row in self._run("PRAGMA table_info(storage)")}:
            self._run("ALTER TABLE storage ADD COLUMN tag TEXT")
        with self._tx() as db:
            for (item_id,) in db.execute("SELECT item_id FROM storage WHERE tag IS NULL ORDER BY rowid").fetchall():
                db.execute("UPDATE storage SET tag = ? WHERE item_id = ?", (self._new_tag(db), item_id))
        self._run("CREATE UNIQUE INDEX IF NOT EXISTS storage_tag ON storage(tag)")
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
