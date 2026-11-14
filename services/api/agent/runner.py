"""Runs one agent turn and yields what happened as plain tuples.

This is the seam between the graph and whatever shows the output. The REPL prints these; the
WebSocket layer will map them to events (WORKSTREAMS 2.3):

    ("token", str)            -> agent.token
    ("action", tool, args)    -> agent.action
    ("ui", {"type","payload"})-> the UI event itself (viewer.goto, ...)
    ("message", str)          -> agent.message (final text; absent when the agent stays silent)
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk

from services.api.agent.context import AgentContext
from services.api.agent.prompts import SILENT


def event_message(event_type: str, body: str = "") -> str:
    """An app event as the agent sees it (see "App events" in the persona)."""
    return f'<event type="{event_type}">{body}</event>'


def stream_turn(agent, text: str, context: AgentContext, thread_id: str) -> Iterator[tuple[Any, ...]]:
    config = {"configurable": {"thread_id": thread_id}}
    held = ""  # text held back while it could still turn out to be SILENT
    released = False
    final = ""

    for mode, data in agent.stream(
        {"messages": [{"role": "user", "content": text}]},
        config=config,
        context=context,
        stream_mode=["messages", "updates", "custom"],
    ):
        if mode == "custom":
            yield ("ui", data)
        elif mode == "messages":
            chunk, _meta = data
            if not isinstance(chunk, AIMessageChunk) or not chunk.text:
                continue
            if released:
                yield ("token", chunk.text)
                continue
            held += chunk.text
            if not SILENT.startswith(held.strip()):
                released = True
                yield ("token", held)
        elif mode == "updates":
            for update in data.values():
                for msg in (update or {}).get("messages", []) if isinstance(update, dict) else []:
                    if not isinstance(msg, AIMessage):
                        continue
                    for call in msg.tool_calls:
                        yield ("action", call["name"], call["args"])
                    if msg.tool_calls:
                        held, released = "", False  # text before a tool call is not the final answer
                    else:
                        final = msg.text

    if final.strip() and final.strip() != SILENT:
        yield ("message", final)
