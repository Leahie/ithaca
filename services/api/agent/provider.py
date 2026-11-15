"""Picks who answers a turn. AGENT_PROVIDER=deep|scripted; unset means deep if an LLM key is
configured, else scripted.

A turn runner is `(text, AgentContext, thread_id) -> iterator of runner tuples` (see runner.py).
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import Any

from services.api.agent.context import AgentContext
from services.api.integrations.config import REPO_ROOT, env
from services.api.wipe import compact, empty_tables

log = logging.getLogger(__name__)

TurnRunner = Callable[[str, AgentContext, str], Iterator[tuple[Any, ...]]]


def open_dev_storage(in_memory: bool = False):
    """Memory store and chat-history saver on SQLite in data/agent/ (Postgres comes with infra).

    Two files: the store and the saver each manage their own transactions and can't share a
    connection.
    """
    from langgraph.checkpoint.sqlite import SqliteSaver
    from langgraph.store.sqlite import SqliteStore

    db_dir = REPO_ROOT / "data" / "agent"

    def connect(name: str) -> sqlite3.Connection:
        if not in_memory:
            db_dir.mkdir(parents=True, exist_ok=True)
        target = ":memory:" if in_memory else db_dir / name
        return sqlite3.connect(target, check_same_thread=False, isolation_level=None)

    store, saver = SqliteStore(connect("memory.sqlite")), SqliteSaver(connect("threads.sqlite"))
    store.setup()
    saver.setup()
    return store, saver


def provider_name() -> str:
    name = env("AGENT_PROVIDER").lower()
    if name in ("deep", "scripted"):
        return name
    has_key = env("ANTHROPIC_API_KEY") if env("LLM_PROVIDER", "anthropic").lower() == "anthropic" else env("OPENROUTER_API_KEY")
    return "deep" if has_key else "scripted"


@lru_cache(maxsize=1)
def _storage():
    from services.api.furniture import ledger

    storage = open_dev_storage(in_memory=env("AGENT_STORAGE") == "memory")
    # From here on, every change to a house writes its furniture register to the memory store.
    ledger.attach(storage[0])
    return storage


def memory_store():
    """The store that holds every user's memory files."""
    return _storage()[0]


def conversation_store():
    """The saver that holds every conversation: one thread per person (or family guide) and house."""
    return _storage()[1]


# --- reset everything (routers/reset.py) ----------------------------------------------------------
# Both are langgraph's own classes, so the wipe is a function here and not a method on them. Each
# works through the store's open connection, holding the store's lock: the file stays, and the
# agents built on it (`_deep_agent`, `_observer`, `_designer`) go on using it.

# Tables of the memory database that are not the user's data: the versions of its own schema.
# Emptied, the store would run its migrations again at the next start and fail on a column that
# is already there.
_SCHEMA_TABLES = ("store_migrations", "vector_migrations")


def memory_count(store) -> int:
    """How many memory files there are, of every person, family and house."""
    with store.lock:
        return store.conn.execute("SELECT COUNT(*) FROM store").fetchone()[0]


def wipe_memory(store) -> int:
    """Delete every memory file of every person, family and house. Returns how many there were."""
    with store.lock:
        before = store.conn.execute("SELECT COUNT(*) FROM store").fetchone()[0]
        empty_tables(store.conn, keep=_SCHEMA_TABLES)
        compact(store.conn)
    return before


def conversation_count(saver) -> int:
    """How many conversations are kept (a conversation is one thread, however long)."""
    with saver.lock:
        return saver.conn.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints").fetchone()[0]


def wipe_conversations(saver) -> int:
    """Delete every conversation: every checkpoint and every pending write. Returns how many
    conversations there were."""
    with saver.lock:
        before = saver.conn.execute("SELECT COUNT(DISTINCT thread_id) FROM checkpoints").fetchone()[0]
        empty_tables(saver.conn)
        compact(saver.conn)
    return before


@lru_cache(maxsize=16)
def _deep_agent(property_id: str):
    from services.api.agent.graph import build_agent

    store, saver = _storage()
    return build_agent(property_id, store=store, checkpointer=saver)


@lru_cache(maxsize=16)
def _observer(property_id: str):
    from services.api.agent.observer import build_observer

    return build_observer(property_id, store=_storage()[0])


def get_observer():
    """The background tour observer, or None. Needs the real agent; AGENT_OBSERVER=0 turns it off."""
    if provider_name() != "deep" or env("AGENT_OBSERVER", "1") == "0":
        return None
    from services.api.agent.observer import run_observer

    return lambda context: run_observer(_observer(context.property_id), context)


@lru_cache(maxsize=16)
def _designer(property_id: str):
    from services.api.agent.graph import build_designer

    return build_designer(property_id, store=_storage()[0])


def get_designer():
    """Runs the interior designer once: (context, room_id, brief) -> its report. None without the real agent."""
    if provider_name() != "deep":
        return None
    from services.api.agent.graph import run_designer

    return lambda context, room_id, brief, on_progress=None: run_designer(_designer(context.property_id), context, room_id, brief, on_progress)


def get_turn_runner() -> TurnRunner:
    if provider_name() == "scripted":
        from services.api.agent.scripted import stream_turn_scripted

        log.info("agent provider: scripted (no LLM)")
        return stream_turn_scripted

    from services.api.agent.runner import stream_turn

    log.info("agent provider: deep")

    def run(text: str, context: AgentContext, thread_id: str):
        return stream_turn(_deep_agent(context.property_id), text, context, thread_id)

    return run
