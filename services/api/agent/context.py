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
