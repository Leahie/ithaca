"""What the user did during one tour: rooms visited, time spent, views opened, things said.

The agent bridge writes to it from hub events. The observer agent reads `summary()`. Kept in
memory for the life of the session; nothing here is stored.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from packages.schema.twin import PropertyTwin
from services.api.agent import prompts

MAX_DWELL_S = 300.0  # one visit counts for at most 5 minutes: beyond that the user probably left the screen
KEPT_QUOTES = 12


def _mmss(seconds: float) -> str:
    return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"


@dataclass
class RoomStats:
    visits: int = 0
    led_by_guide: int = 0
    seconds: float = 0.0
    views: set[str] = field(default_factory=set)


@dataclass
class TourLog:
    started: float = field(default_factory=time.monotonic)
    rooms: dict[str, RoomStats] = field(default_factory=dict)
    said: list[str] = field(default_factory=list)
    sun_times: list[str] = field(default_factory=list)
    user_moves: int = 0  # room changes the user made themselves
    _room: str | None = None
    _entered: float = 0.0
    _view: str = "tour"

    def _close_visit(self, now: float) -> None:
        if self._room is not None:
            self.rooms[self._room].seconds += min(now - self._entered, MAX_DWELL_S)

    def enter_room(self, room_id: str, by_user: bool, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if room_id == self._room:
            return
        self._close_visit(now)
        stats = self.rooms.setdefault(room_id, RoomStats())
        stats.visits += 1
        stats.views.add(self._view)
        if by_user:
            self.user_moves += 1
        else:
            stats.led_by_guide += 1
        self._room, self._entered = room_id, now

    def set_view(self, view: str) -> None:
        self._view = view
        if self._room is not None:
            self.rooms[self._room].views.add(view)

    def set_time(self, datetime_iso: str) -> None:
        if datetime_iso not in self.sun_times:
            self.sun_times.append(datetime_iso)

    def heard(self, text: str) -> None:
        self.said = (self.said + [text.strip()])[-KEPT_QUOTES:]

    def summary(self, twin: PropertyTwin, now: float | None = None) -> str:
        now = time.monotonic() if now is None else now
        names = prompts.room_display_names(twin)
        seconds = {rid: s.seconds for rid, s in self.rooms.items()}
        if self._room is not None:  # count the visit in progress without closing it
            seconds[self._room] += min(now - self._entered, MAX_DWELL_S)

        lines = [f"Tour so far: {_mmss(now - self.started)}, {sum(s.visits for s in self.rooms.values())} room visits."]
        if self.rooms:
            lines.append("Rooms by time spent (name · room_id · visits · time · views opened there):")
            for rid in sorted(self.rooms, key=lambda r: -seconds[r]):
                s = self.rooms[rid]
                led = f" ({s.led_by_guide} led by the guide)" if s.led_by_guide else ""
                lines.append(
                    f"- {names.get(rid, rid)} · `{rid}` · {s.visits} visit{'s' if s.visits != 1 else ''}{led} · "
                    f"{_mmss(seconds[rid])} · {', '.join(sorted(s.views))}"
                )
        skipped = [names[r.room_id] for r in twin.rooms if r.room_id not in self.rooms and (r.area_m2 or 0) >= 6]
        if skipped:
            lines.append("Not visited yet: " + "; ".join(skipped))
        if self.sun_times:
            lines.append("Times of day they looked at in the sun view: " + ", ".join(self.sun_times))
        if self.said:
            lines.append("What the user said, oldest first:")
            lines += [f'- "{q}"' for q in self.said]
        return "\n".join(lines)


# session_id -> log. The bridge creates entries; the observer's tool reads them.
TOUR_LOGS: dict[str, TourLog] = {}
