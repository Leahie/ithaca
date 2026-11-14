"""Inventory and layouts storage. Owner: B. The database is canonical (B3).

SQLite for now (data/agent/furniture.sqlite); the methods are what a Postgres version will keep.
Rows hold the pydantic model as JSON plus the columns we filter on.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path

from packages.schema.inventory import InventoryItem
from packages.schema.layout import LayoutVariant
from services.api.integrations.config import REPO_ROOT, env, fixtures_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory_items (item_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS layouts (
    layout_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, property_id TEXT NOT NULL, room_id TEXT NOT NULL,
    created REAL NOT NULL DEFAULT (julianday('now')), body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seeded (user_id TEXT PRIMARY KEY);
"""


class FurnitureStore:
    def __init__(self, path: Path | str | None = None) -> None:
        if path is None:
            path = REPO_ROOT / "data" / "agent" / "furniture.sqlite"
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._db.executescript(SCHEMA)

    def _run(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    # --- inventory ------------------------------------------------------------------------------

    def _seed(self, user_id: str) -> None:
        """Dev convenience: a new user starts with the three sample items from fixtures/inventory.
        SEED_DEMO_INVENTORY=0 turns it off."""
        if self._run("SELECT 1 FROM seeded WHERE user_id = ?", (user_id,)):
            return
        self._run("INSERT INTO seeded VALUES (?)", (user_id,))
        path = fixtures_dir() / "inventory" / "demo.json"
        if env("SEED_DEMO_INVENTORY", "1") == "0" or not path.exists():
            return
        for row in json.loads(path.read_text(encoding="utf-8")):
            # the sample ids are kept for the demo user so the sample layouts still point at them
            item_id = row["item_id"] if user_id == row["user_id"] else str(uuid.uuid5(uuid.UUID(row["item_id"]), user_id))
            self.put_item(InventoryItem.model_validate({**row, "user_id": user_id, "item_id": item_id}))

    def put_item(self, item: InventoryItem) -> None:
        self._run(
            "INSERT OR REPLACE INTO inventory_items VALUES (?, ?, ?)",
            (str(item.item_id), item.user_id, item.model_dump_json()),
        )

    def list_items(self, user_id: str) -> list[InventoryItem]:
        self._seed(user_id)
        rows = self._run("SELECT body FROM inventory_items WHERE user_id = ? ORDER BY rowid", (user_id,))
        return [InventoryItem.model_validate_json(body) for (body,) in rows]

    def get_item(self, user_id: str, item_id: str) -> InventoryItem | None:
        return next((i for i in self.list_items(user_id) if str(i.item_id) == item_id), None)

    def delete_item(self, user_id: str, item_id: str) -> bool:
        found = self.get_item(user_id, item_id) is not None
        self._run("DELETE FROM inventory_items WHERE user_id = ? AND item_id = ?", (user_id, item_id))
        return found

    # --- layouts --------------------------------------------------------------------------------

    def put_layout(self, layout: LayoutVariant) -> None:
        self._run(
            "INSERT INTO layouts (layout_id, user_id, property_id, room_id, body) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(layout_id) DO UPDATE SET body = excluded.body",
            (str(layout.layout_id), layout.user_id, layout.property_id, layout.room_id, layout.model_dump_json()),
        )

    def list_layouts(self, user_id: str, property_id: str | None = None, room_id: str | None = None) -> list[LayoutVariant]:
        rows = self._run(
            "SELECT body FROM layouts WHERE user_id = ? AND (? IS NULL OR property_id = ?) AND (? IS NULL OR room_id = ?) "
            "ORDER BY created DESC, rowid DESC",
            (user_id, property_id, property_id, room_id, room_id),
        )
        return [LayoutVariant.model_validate_json(body) for (body,) in rows]

    def get_layout(self, user_id: str, layout_id: str) -> LayoutVariant | None:
        rows = self._run("SELECT body FROM layouts WHERE user_id = ? AND layout_id = ?", (user_id, layout_id))
        if rows:
            return LayoutVariant.model_validate_json(rows[0][0])
        return next((l for l in _sample_layouts() if str(l.layout_id) == layout_id and l.user_id == user_id), None)

    def choose_layout(self, user_id: str, layout_id: str) -> LayoutVariant | None:
        """Mark one layout chosen. Any other chosen layout for the same room goes back to proposed."""
        layout = self.get_layout(user_id, layout_id)
        if layout is None:
            return None
        for other in self.list_layouts(user_id, layout.property_id, layout.room_id):
            if other.status == "chosen" and other.layout_id != layout.layout_id:
                self.put_layout(other.model_copy(update={"status": "proposed"}))
        chosen = layout.model_copy(update={"status": "chosen"})
        self.put_layout(chosen)
        return chosen

    def set_feedback(self, user_id: str, layout_id: str, feedback: str) -> LayoutVariant | None:
        layout = self.get_layout(user_id, layout_id)
        if layout is None:
            return None
        updated = layout.model_copy(update={"feedback": feedback})
        self.put_layout(updated)
        return updated


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
