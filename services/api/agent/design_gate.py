"""One room at a time, with the user deciding between rooms.

The guide may furnish many rooms, but never in a row by itself: while one room is being arranged,
or its layouts are on screen waiting for the user, a design for a different room is refused. The
user's own words, or their choice of a layout, open the gate again. The tools ask (`blocker`), the
session bridge reports what happens (`started`, `shown`, `dropped`, `user_spoke`).

Kept per conversation (the session, or one family member's thread within it), in memory.
"""

from __future__ import annotations

_busy: dict[str, set[str]] = {}  # conversation -> rooms the designer is working on
_review: dict[str, set[str]] = {}  # conversation -> rooms whose layouts wait for the user


def key(session_id: str | None, user_id: str) -> str:
    return f"{session_id or 'no-session'}:{user_id}"


def blocker(conversation: str, room_id: str) -> tuple[str, str] | None:
    """Another room that has to be settled first: (room_id, "arranging" | "waiting")."""
    for other in _busy.get(conversation, ()):
        if other != room_id:
            return other, "arranging"
    for other in _review.get(conversation, ()):
        if other != room_id:
            return other, "waiting"
    return None


def started(conversation: str, room_id: str) -> None:
    _busy.setdefault(conversation, set()).add(room_id)
    _review.get(conversation, set()).discard(room_id)


def shown(conversation: str, room_id: str) -> None:
    """The designer is done and the guide is about to show the room's layouts."""
    _busy.get(conversation, set()).discard(room_id)
    _review.setdefault(conversation, set()).add(room_id)


def dropped(conversation: str, room_id: str) -> None:
    _busy.get(conversation, set()).discard(room_id)


def user_spoke(conversation: str) -> None:
    """The user said something or kept a layout: whatever was waiting for them is settled."""
    _review.pop(conversation, None)


def reset() -> None:
    _busy.clear()
    _review.clear()
