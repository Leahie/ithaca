"""Rooms are planned together and shown one at a time.

When the user asks for several rooms, the designer starts on all of them at once, so nobody waits
a minute per room. But the guide never shows a second room by itself: while one room's layouts
are on screen waiting for the user, the next room's finished layouts are held back. The user's
own words, or their choice of a layout, release the next one.

The tools report what is started (`started`); the session bridge reports what is finished
(`ready`) and what the user did (`user_spoke`), and shows whatever these hand back.

Kept per conversation (the session, or one family member's thread within it), in memory.
"""

from __future__ import annotations

_busy: dict[str, set[str]] = {}  # conversation -> rooms the designer is working on
_review: dict[str, str] = {}  # conversation -> the room whose layouts wait for the user
_held: dict[str, list[tuple[str, str]]] = {}  # conversation -> finished (room, message) not shown yet


def key(session_id: str | None, user_id: str) -> str:
    return f"{session_id or 'no-session'}:{user_id}"


def state(conversation: str, room_id: str) -> str | None:
    """"arranging" (the designer is on it) or "held" (done, waiting its turn to be shown), else None."""
    if room_id in _busy.get(conversation, ()):
        return "arranging"
    if any(room == room_id for room, _ in _held.get(conversation, ())):
        return "held"
    return None


def started(conversation: str, room_id: str) -> None:
    _busy.setdefault(conversation, set()).add(room_id)
    if _review.get(conversation) == room_id:  # being redone: the old options no longer wait
        _review.pop(conversation, None)


def dropped(conversation: str, room_id: str) -> None:
    _busy.get(conversation, set()).discard(room_id)


def ready(conversation: str, room_id: str, message: str) -> bool:
    """The designer finished a room. True: show it now. False: another room is waiting for the
    user, so this one is held until they have decided."""
    _busy.get(conversation, set()).discard(room_id)
    if conversation in _review and _review[conversation] != room_id:
        _held.setdefault(conversation, []).append((room_id, message))
        return False
    _review[conversation] = room_id
    return True


def user_spoke(conversation: str) -> str | None:
    """The user said something or kept a layout: what was waiting for them is settled. Returns
    the message of the next finished room to show, if one was held."""
    _review.pop(conversation, None)
    held = _held.get(conversation)
    if not held:
        return None
    room_id, message = held.pop(0)
    _review[conversation] = room_id
    return message


def others(conversation: str, room_id: str) -> tuple[list[str], list[str]]:
    """Besides this room: the rooms still being arranged, and the finished ones waiting their turn."""
    return (
        sorted(r for r in _busy.get(conversation, ()) if r != room_id),
        [r for r, _ in _held.get(conversation, ()) if r != room_id],
    )


def reset() -> None:
    _busy.clear()
    _review.clear()
    _held.clear()
