"""Per-run context for the agent. Passed as `context=` on every invoke/stream call.

Tools and the memory backend read identity from here, never from model-supplied arguments (B4).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentContext:
    user_id: str
    property_id: str
    session_id: str | None = None
    # In a household session: whose agent this is. user_id is then the member's id, so the agent
    # reads and writes only that member's memory (B4).
    household_id: str | None = None
    member_name: str | None = None
    # Set when another member's agent asked this one a question: it answers and asks nobody else.
    asked: bool = False
