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
    # In a household session: the user the big screen draws furniture for. A member's agent works on
    # that shared furniture and those layouts, while its memory stays the member's own.
    house_user_id: str | None = None
    # Set when another member's agent asked this one a question: it answers and asks nobody else.
    asked: bool = False
    # Whose memory this agent reads and writes, when that is not user_id. The big screen's guide in a
    # household remembers that household, so a new family starts knowing nothing.
    memory_user: str | None = None

    @property
    def memory_owner(self) -> str:
        return self.memory_user or self.user_id

    @property
    def furniture_user(self) -> str:
        """The house user: whose house and proposals the furniture tools read and write. New
        pieces belong to user_id (docs/specs/furniture.md)."""
        return self.house_user_id or self.user_id
