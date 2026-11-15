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

    @property
    def memory_user(self) -> str:
        """Whose memory files and conversation this agent has. A member's agent: the member's own.
        The shared guide of a family: the family's, so a new family starts with nothing and one
        family never reads another's notes, although every big screen is the same user."""
        if self.household_id and self.member_name is None and self.house_user_id is None and not self.asked:
            return self.household_id
        if self.session_id and not self.household_id:
            # A big screen with no family named yet (also right after "Start a new family"): every
            # such screen is the same user, so what one visitor told the guide must not greet the
            # next. It gets a memory of its own, for this session only.
            return f"guest-{self.session_id}"
        return self.user_id

    @property
    def thread_id(self) -> str:
        """The conversation: one per person (or family guide) and house. It outlives the browser
        session, so a new visit continues the last one."""
        return f"{self.memory_user}:{self.property_id}"

    @property
    def furniture_user(self) -> str:
        """The house user: whose house and proposals the furniture tools read and write. New
        pieces belong to user_id (docs/specs/furniture.md)."""
        return self.house_user_id or self.user_id
