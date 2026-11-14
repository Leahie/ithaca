"""Who has plans for which room of the house everyone is arranging.

Family members' agents are kept apart: none reads another's memory or conversation. But they all
furnish one house, so each is told the bare fact that someone else is already planning a room,
and has to raise it with its own person before planning over it. Nothing else crosses: no
preferences, no reasons, nothing that was said.

Kept in memory per house (the big screen's user and the property); gone when the API restarts.
"""

from __future__ import annotations

from dataclasses import dataclass

ARRANGING = "is having layouts drawn up"
CHOOSING = "is choosing between layouts"
KEPT = "has kept a layout"


@dataclass(frozen=True)
class Plan:
    room_id: str
    who_id: str
    who: str
    status: str


_plans: dict[tuple[str, str], dict[str, Plan]] = {}


def who(context) -> str:
    """How others hear of this agent's person."""
    return context.member_name or "whoever is at the big screen"


def note(context, room_id: str, status: str) -> None:
    house = (context.furniture_user, context.property_id)
    _plans.setdefault(house, {})[room_id] = Plan(room_id, context.user_id, who(context), status)


def of_others(context, room_id: str | None = None) -> list[Plan]:
    """Plans by anyone but this agent's person, for one room or the whole house."""
    plans = _plans.get((context.furniture_user, context.property_id), {}).values()
    return [p for p in plans if p.who_id != context.user_id and room_id in (None, p.room_id)]


def reset() -> None:
    _plans.clear()
