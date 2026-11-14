"""Talk to the agent in a terminal.

    python -m services.api.agent.repl --user alice --property onXQLXhfyQv

Memory and chat history persist in data/agent/*.sqlite, so a preference stated in one run is
there in the next. Commands:
    /new            start a new thread (same user, memory kept, chat history dropped)
    /memory         print this user's memory files
    /event TYPE     send an app event, e.g. /event property_opened
    /quit
"""

from __future__ import annotations

import argparse
import sqlite3
import uuid
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

from services.api.agent.context import AgentContext
from services.api.agent.graph import build_agent, memory_namespace
from services.api.agent.runner import event_message, stream_turn
from services.api.integrations.config import REPO_ROOT, IntegrationNotConfigured

DIM, BOLD, RESET = "\033[2m", "\033[1m", "\033[0m"


def open_dev_storage(db_dir: Path | None) -> tuple[SqliteStore, SqliteSaver]:
    """Memory and chat history in two SQLite files (they can't share a connection: each manages
    its own transactions). db_dir=None keeps both in RAM."""

    def connect(name: str) -> sqlite3.Connection:
        target = ":memory:" if db_dir is None else db_dir / name
        return sqlite3.connect(target, check_same_thread=False, isolation_level=None)

    store, saver = SqliteStore(connect("memory.sqlite")), SqliteSaver(connect("threads.sqlite"))
    store.setup()
    saver.setup()
    return store, saver


def print_memory(store: SqliteStore, user_id: str) -> None:
    items = store.search(memory_namespace(user_id), limit=100)
    if not items:
        print(f"{DIM}(no memory files yet){RESET}")
    for item in items:
        content = item.value.get("content", item.value)
        text = "\n".join(content) if isinstance(content, list) else str(content)
        print(f"{BOLD}/memories{item.key}{RESET}\n{text}\n")


def run_turn(agent, text: str, ctx: AgentContext, thread_id: str) -> None:
    streamed = False
    for kind, *rest in stream_turn(agent, text, ctx, thread_id):
        if kind == "token":
            print(rest[0], end="", flush=True)
            streamed = True
        elif kind == "action":
            if streamed:
                print()
                streamed = False
            print(f"{DIM}  → {rest[0]}({', '.join(f'{k}={v!r}' for k, v in rest[1].items())[:160]}){RESET}")
        elif kind == "ui":
            print(f"{BOLD}  ⇢ UI {rest[0]['type']} {rest[0]['payload']}{RESET}")
        elif kind == "message":
            if not streamed:  # the model didn't stream tokens
                print(rest[0], end="")
            streamed = True
    print("" if streamed else f"{DIM}(agent stayed silent){RESET}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--user", default="demo")
    parser.add_argument("--property", default="onXQLXhfyQv")
    parser.add_argument("--thread", default=None, help="resume a thread id; default is a new one")
    args = parser.parse_args()

    db_dir = REPO_ROOT / "data" / "agent"
    db_dir.mkdir(parents=True, exist_ok=True)
    store, saver = open_dev_storage(db_dir)

    try:
        agent = build_agent(args.property, store=store, checkpointer=saver)
    except IntegrationNotConfigured as e:
        raise SystemExit(f"{e}. Put it in {REPO_ROOT / '.env'} (see .env.example).")
    ctx = AgentContext(user_id=args.user, property_id=args.property)
    thread_id = args.thread or uuid.uuid4().hex[:8]
    print(f"{DIM}user={args.user} property={args.property} thread={thread_id}  (/new /memory /event TYPE /quit){RESET}")

    while True:
        try:
            line = input(f"\n{BOLD}you>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        if line == "/quit":
            break
        if line == "/new":
            thread_id = uuid.uuid4().hex[:8]
            print(f"{DIM}new thread {thread_id}{RESET}")
        elif line == "/memory":
            print_memory(store, args.user)
        elif line.startswith("/event "):
            run_turn(agent, event_message(line.split(maxsplit=1)[1]), ctx, thread_id)
        else:
            run_turn(agent, line, ctx, thread_id)


if __name__ == "__main__":
    main()
