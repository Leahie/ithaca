"""Households: a family looking at houses together. Owner: B.

A household has members, one reusable invite (the family QR code) and a board of notes that the
members' agents post for each other. Each member is a user in their own right: `member_id` is
their user_id, so their agent keeps its own memory namespace (B4). The board holds only what an
agent chose to share; nothing here reads a member's memory.

Kept in SQLite next to the agent's dev storage (data/agent/), until the Postgres tables exist.
Thread-safe: agent tools call it from worker threads.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from packages.schema.session import Household, Member
from services.api.integrations.config import REPO_ROOT

INVITE_TTL = timedelta(hours=24)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS households (
    household_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    invite_token TEXT NOT NULL UNIQUE,
    invite_expires_at TEXT NOT NULL,
    active_session_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS members (
    member_id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(household_id),
    name TEXT NOT NULL,
    joined_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS members_household ON members(household_id);
CREATE TABLE IF NOT EXISTS board_notes (
    note_id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(household_id),
    member_id TEXT NOT NULL,
    property_id TEXT,
    room_id TEXT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_household ON board_notes(household_id, created_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InviteInvalid(Exception):
    """The family QR code is unknown or has expired."""


class HouseholdStore:
    def __init__(self, path: Path | str | None = None) -> None:
        """path: the SQLite file; ":memory:" for tests. Default: data/agent/households.sqlite."""
        if path is None:
            path = REPO_ROOT / "data" / "agent" / "households.sqlite"
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._lock = threading.Lock()

    # --- households -----------------------------------------------------------------------------

    def create(self, name: str) -> Household:
        hid = f"h-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._db.execute(
                "INSERT INTO households VALUES (?, ?, ?, ?, NULL, ?)",
                (hid, name.strip() or "Our family", secrets.token_urlsafe(12), (_now() + INVITE_TTL).isoformat(), _now().isoformat()),
            )
        return self.get(hid)  # type: ignore[return-value]

    def get(self, household_id: str) -> Household | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM households WHERE household_id = ?", (household_id,)).fetchone()
            if row is None:
                return None
            members = self._db.execute(
                "SELECT member_id, name FROM members WHERE household_id = ? ORDER BY joined_at", (household_id,)
            ).fetchall()
        return Household(
            household_id=row["household_id"],
            name=row["name"],
            invite_token=row["invite_token"],
            invite_expires_at=datetime.fromisoformat(row["invite_expires_at"]),
            active_session_id=row["active_session_id"],
            members=[Member(member_id=m["member_id"], name=m["name"]) for m in members],
        )

    def rotate_invite(self, household_id: str) -> Household | None:
        """A new family QR code; the old one stops working."""
        with self._lock:
            self._db.execute(
                "UPDATE households SET invite_token = ?, invite_expires_at = ? WHERE household_id = ?",
                (secrets.token_urlsafe(12), (_now() + INVITE_TTL).isoformat(), household_id),
            )
        return self.get(household_id)

    def set_active_session(self, household_id: str, session_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE households SET active_session_id = ? WHERE household_id = ?", (session_id, household_id))

    # --- members --------------------------------------------------------------------------------

    def join(self, invite_token: str, name: str, member_id: str | None = None) -> tuple[Household, Member]:
        """Admit someone who scanned the family QR code. A phone that joined before sends its
        member_id and comes back as the same person; a known name (any case) is that person too,
        so a new phone can pick up where an old one left off. Raises InviteInvalid."""
        name = " ".join(name.split())[:40]
        if not name:
            raise ValueError("a name is needed")
        with self._lock:
            row = self._db.execute("SELECT household_id, invite_expires_at FROM households WHERE invite_token = ?", (invite_token,)).fetchone()
            if row is None or datetime.fromisoformat(row["invite_expires_at"]) <= _now():
                raise InviteInvalid("This family code is unknown or has expired. Ask for a new one on the big screen.")
            hid = row["household_id"]
            found = None
            if member_id:
                found = self._db.execute("SELECT member_id FROM members WHERE member_id = ? AND household_id = ?", (member_id, hid)).fetchone()
            if found is None:
                found = self._db.execute("SELECT member_id, name FROM members WHERE household_id = ? AND lower(name) = lower(?)", (hid, name)).fetchone()
            if found is None:
                mid = f"m-{uuid.uuid4().hex[:12]}"
                self._db.execute("INSERT INTO members VALUES (?, ?, ?, ?)", (mid, hid, name, _now().isoformat()))
            else:
                mid = found["member_id"]
                stored = self._db.execute("SELECT name FROM members WHERE member_id = ?", (mid,)).fetchone()["name"]
                if stored.lower() != name.lower():  # a real rename; "maya" for "Maya" keeps "Maya"
                    self._db.execute("UPDATE members SET name = ? WHERE member_id = ?", (name, mid))
                else:
                    name = stored
        household = self.get(hid)
        assert household is not None
        return household, Member(member_id=mid, name=name)

    def member(self, household_id: str, member_id: str) -> Member | None:
        with self._lock:
            row = self._db.execute("SELECT member_id, name FROM members WHERE member_id = ? AND household_id = ?", (member_id, household_id)).fetchone()
        return Member(member_id=row["member_id"], name=row["name"]) if row else None

    def member_by_name(self, household_id: str, name: str) -> Member | None:
        """The member called `name` (any case), or the only one whose name starts with it."""
        h = self.get(household_id)
        if h is None:
            return None
        wanted = name.strip().lower()
        exact = [m for m in h.members if m.name.lower() == wanted]
        if exact:
            return exact[0]
        prefix = [m for m in h.members if m.name.lower().startswith(wanted)]
        return prefix[0] if len(prefix) == 1 else None

    # --- the family board -----------------------------------------------------------------------

    def add_note(self, household_id: str, member_id: str, text: str, property_id: str | None = None, room_id: str | None = None) -> dict:
        note = {
            "note_id": uuid.uuid4().hex,
            "member_id": member_id,
            "text": text.strip()[:1000],
            "room_id": room_id,
        }
        with self._lock:
            self._db.execute(
                "INSERT INTO board_notes VALUES (?, ?, ?, ?, ?, ?, ?)",
                (note["note_id"], household_id, member_id, property_id, room_id, note["text"], _now().isoformat()),
            )
        return note

    def notes(self, household_id: str, limit: int = 20, property_id: str | None = None) -> list[dict]:
        """Newest last. With property_id, notes about that house and notes about no house."""
        sql = "SELECT n.*, m.name FROM board_notes n LEFT JOIN members m ON m.member_id = n.member_id WHERE n.household_id = ?"
        args: list = [household_id]
        if property_id:
            sql += " AND (n.property_id = ? OR n.property_id IS NULL)"
            args.append(property_id)
        sql += " ORDER BY n.created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 100)))
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [dict(r) for r in reversed(rows)]


@lru_cache(maxsize=1)
def households() -> HouseholdStore:
    """The process's store. Routers and agent tools share it."""
    return HouseholdStore()
