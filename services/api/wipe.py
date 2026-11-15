"""Emptying a SQLite database through its open connection, for "Reset everything" (routers/reset.py).

The stores are process-wide and keep their files open. Deleting a file from under one would
break the running API, so a reset never does: each store empties its own tables with these
helpers, holding its own lock, and goes on working as a store that was just made.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable

log = logging.getLogger(__name__)


def empty_tables(db: sqlite3.Connection, keep: Iterable[str] = ()) -> dict[str, int]:
    """Delete every row of every table, in one transaction: all of it or none. The tables are
    read from sqlite_master, so one added later is emptied without anyone remembering to list it.
    `keep` names tables that hold no user data (a list of schema versions). Returns the rows
    deleted per table. The caller holds the store's lock, and no transaction is open."""
    kept = set(keep)
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()]
    deleted: dict[str, int] = {}
    db.execute("BEGIN IMMEDIATE")
    try:
        # Rows point at each other (a piece in a house at its piece in storage). The check waits
        # for the commit, when every table is empty, so the order of the tables does not matter.
        db.execute("PRAGMA defer_foreign_keys = ON")
        for name in tables:
            # sqlite_sequence holds the counters of AUTOINCREMENT columns: emptied, they start
            # again at 1. SQLite's other tables of its own are left to it.
            if name in kept or (name.startswith("sqlite_") and name != "sqlite_sequence"):
                continue
            quoted = name.replace('"', '""')
            deleted[name] = db.execute(f'DELETE FROM "{quoted}"').rowcount
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    return deleted


def compact(db: sqlite3.Connection) -> None:
    """Give the emptied pages back. A deleted row's bytes stay in the file until then, readable by
    anyone who opens it. Best effort: the rows are gone either way, so a failure is only logged."""
    try:
        db.execute("VACUUM")
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()  # a write-ahead log holds the old pages too
    except sqlite3.Error:
        log.warning("an emptied database could not be compacted; its deleted rows may still be in the file", exc_info=True)
