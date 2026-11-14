"""Agent tools. Property tools wrap twin/service.py (WORKSTREAMS 2.5). UI tools emit events.

UI tools don't know about the WebSocket hub. They write `{"type", "payload"}` to LangGraph's custom
stream; whoever runs the turn (runner.py) forwards it. Every argument is an id or a datetime (B1)
and is checked against the twin before anything is emitted.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Literal

from langchain.tools import ToolRuntime, tool

from services.api.agent import prompts, views
from services.api.agent.context import AgentContext
from services.api.integrations.config import IntegrationNotConfigured
from services.api.twin import service
from services.api.twin.service import get_twin

Runtime = ToolRuntime[AgentContext]


def _emit(runtime: Runtime, event_type: str, payload: dict) -> None:
    runtime.stream_writer({"type": event_type, "payload": payload})


# --- property tools -------------------------------------------------------------------------


@tool
def list_rooms(runtime: Runtime) -> str:
    """List every room in the current house: name, room_id, size, floor material, windows."""
    return prompts.render_room_directory(get_twin(runtime.context.property_id))


@tool
def get_room_geometry(room_id: str, runtime: Runtime) -> str:
    """Dimensions of one room in meters: area, ceiling height, wall lengths, windows and doors.

    Use it to judge whether furniture fits (compare item widths with wall lengths) and where the
    light comes from. room_id must come from the room directory.
    """
    twin = get_twin(runtime.context.property_id)
    room = next((r for r in twin.rooms if r.room_id == room_id), None)
    if room is None:
        return f"Error: unknown room_id {room_id!r}. Use a room_id from the room directory."
    names = prompts.room_display_names(twin)

    pts = room.polygon
    walls = sorted(
        (math.dist(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))), reverse=True
    )
    xs, zs = [p[0] for p in pts], [p[1] for p in pts]
    lines = [
        f"{names[room_id]} (`{room_id}`)",
        f"- Area: {room.area_m2:.1f} m²" if room.area_m2 is not None else "- Area: unknown",
        f"- Bounding box: {max(xs) - min(xs):.2f} m × {max(zs) - min(zs):.2f} m, ceiling {room.height_m:.2f} m",
        f"- Wall lengths (m): {', '.join(f'{w:.2f}' for w in walls if w >= 0.3)}",
        f"- Floor: {room.floor_material}",
        "- Openings:",
    ]
    for o in twin.openings:
        if o.room_id != room_id and o.other_room_id != room_id:
            continue
        a, b, _, d = o.polygon[:4]
        width = math.dist((a.x, a.z), (b.x, b.z))
        desc = f"  - {o.kind}, {width:.2f} m wide, {d.y - a.y:.2f} m tall"
        if o.kind == "door":
            far = o.other_room_id if o.room_id == room_id else o.room_id
            nowhere = {True: "outside", False: "unlabeled space"}.get(o.exterior, "outside or unlabeled space")
            desc += f", to {names.get(far, nowhere)}"
        else:
            desc += (
                f", sill {a.y - room.floor_y:.2f} m above floor, faces "
                f"{prompts.compass_point(o.facade_azimuth_deg)}, {o.glazing} glazing"
            )
        lines.append(desc)
    return "\n".join(lines)


# --- sun and daylight tools (C's engines, WORKSTREAMS 2.5) --------------------------------------
# Results are short text, in clock times and brightness levels. No lux grids, no coordinates.


def _room_or_error(twin, room_id: str) -> str | None:
    if any(r.room_id == room_id for r in twin.rooms):
        return None
    return f"Error: unknown room_id {room_id!r}. Use a room_id from the room directory."


def _parse_day(text: str) -> date | str:
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return f"Error: {text!r} is not a date like 2026-12-21."


def _facing(twin, opening_ids: list[str]) -> str:
    by_id = {o.opening_id: o for o in twin.openings}
    points = sorted({prompts.compass_point(by_id[i].facade_azimuth_deg) for i in opening_ids if i in by_id})
    return f" through {'/'.join(points)}-facing windows" if points else ""


def _sun_line(twin, room_id: str, day: date) -> tuple[float, str]:
    floor = service.sun_room_day(twin.property_id, room_id, day)
    if not floor.intervals:
        return 0.0, "no direct sun"
    seated = service.sun_room_day(twin.property_id, room_id, day, height_m=1.1)
    spans = ", ".join(f"{i.start:%H:%M}-{i.end:%H:%M}" for i in floor.intervals)
    return floor.total_hours, (
        f"direct sun on the floor {spans} ({floor.total_hours:.1f} h){_facing(twin, floor.through_opening_ids)}; "
        f"at seated height {seated.total_hours:.1f} h"
    )


@tool
def sun_by_room(day: str, runtime: Runtime, room_id: str | None = None) -> str:
    """When direct sunlight comes into the rooms on one day (clear sky).

    day: a date like 2026-12-21. Use Dec 21 for winter and Jun 21 for summer.
    room_id: one room. Leave it out to compare every room with a window, sunniest first.
    """
    twin = get_twin(runtime.context.property_id)
    when = _parse_day(day)
    if isinstance(when, str):
        return when
    names = prompts.room_display_names(twin)
    if room_id is not None:
        return _room_or_error(twin, room_id) or f"{names[room_id]} on {when:%B %d}: {_sun_line(twin, room_id, when)[1]}."
    rows = sorted(
        ((*_sun_line(twin, r.room_id, when), names[r.room_id]) for r in twin.rooms if _has_window(twin, r.room_id)),
        reverse=True,
    )
    lines = [f"Direct sun on {when:%B %d}, sunniest room first:"]
    lines += [f"- {name}: {text}" for _hours, text, name in rows]
    return "\n".join(lines)


def _has_window(twin, room_id: str) -> bool:
    return any(o.room_id == room_id and o.kind != "door" and o.exterior is not False for o in twin.openings)


@tool
def sun_report(room_id: str, start_date: str, end_date: str, runtime: Runtime) -> str:
    """Hours of direct sun per day in one room over a date range (clear sky), e.g. a month or a
    whole year. Dates like 2026-12-01. Use it for "how sunny is this room in December / all year"."""
    twin = get_twin(runtime.context.property_id)
    start, end = _parse_day(start_date), _parse_day(end_date)
    for bad in (start, end, _room_or_error(twin, room_id)):
        if isinstance(bad, str):
            return bad
    if not 0 <= (end - start).days <= 366:
        return "Error: end_date must be on or after start_date, at most a year later."
    report = service.sun_report(twin.property_id, room_id, start, end)
    hours = [d.direct_sun_hours for d in report.daily]
    name = prompts.room_display_names(twin)[room_id]
    lines = [
        f"{name}, {start:%b %d, %Y} to {end:%b %d, %Y}: on average {sum(hours) / len(hours):.1f} h of direct sun "
        f"a day (least {min(hours):.1f} h, most {max(hours):.1f} h)."
    ]
    months: dict[str, list[float]] = {}
    for d in report.daily:
        months.setdefault(f"{d.date:%b}", []).append(d.direct_sun_hours)
    if len(months) > 1:
        lines.append("By month: " + ", ".join(f"{m} {sum(v) / len(v):.1f} h" for m, v in months.items()))
    return "\n".join(lines)


@tool
def daylight_at(
    datetime_iso: str,
    runtime: Runtime,
    room_id: str | None = None,
    sky: Literal["typical", "clear"] = "typical",
) -> str:
    """How bright the rooms are at one moment, from daylight alone (no lamps).

    datetime_iso: local time at the property, e.g. 2026-12-21T15:00.
    room_id: one room. Leave it out for every room with a window.
    sky: "typical" = usual weather for that date and hour; "clear" = a cloudless day.
    Levels: dark, dim, moderate, bright, very_bright. A room that is not listed has no window.
    """
    twin = get_twin(runtime.context.property_id)
    if room_id is not None and (error := _room_or_error(twin, room_id)):
        return error
    note = ""
    try:
        try:
            moment = service.daylight_at(twin.property_id, datetime_iso, sky=sky, room_id=room_id, grids=False)
        except IntegrationNotConfigured:
            moment = service.daylight_at(twin.property_id, datetime_iso, sky="clear", room_id=room_id, grids=False)
            note = " Typical weather isn't available for this property, so this is a clear day."
    except ValueError as e:
        return f"Error: {e}"
    if not moment.sun_up:
        return f"At {moment.datetime:%B %d, %H:%M} the sun is down. Every room is dark without lamps."
    names = prompts.room_display_names(twin)
    lines = [f"Daylight at {moment.datetime:%B %d, %H:%M} ({moment.sky} sky).{note}"]
    for r in sorted(moment.rooms, key=lambda r: -r.median_lux):
        sun_patch = f", with about {r.sun_area_m2:.0f} m² in direct sun" if r.sun_area_m2 >= 0.5 else ""
        lines.append(f"- {names[r.room_id]}: {r.level.replace('_', ' ')}{sun_patch}")
    if not moment.rooms:
        lines.append("- This room has no window of its own.")
    return "\n".join(lines)


# --- UI tools (WORKSTREAMS 2.3: one tool -> one event) ----------------------------------------


@tool
def viewer_goto(room_id: str, runtime: Runtime) -> str:
    """Move the user's view into a room, standing where the room shows best. room_id must come
    from the room directory."""
    twin = get_twin(runtime.context.property_id)
    if error := _room_or_error(twin, room_id):
        return error
    payload: dict = {"room_id": room_id}
    if view := views.best_view(twin, room_id):
        payload |= {"at_sweep_id": view.sweep_id, "look_at": view.look_at.model_dump()}
    _emit(runtime, "viewer.goto", payload)
    return f"The user is now looking at {prompts.room_display_names(twin)[room_id]}."


@tool
def viewer_walk(from_room_id: str, to_room_id: str, runtime: Runtime) -> str:
    """Walk the user's view from one room to another, the way a person would go, and report the
    distance. Use it for questions like "what's the walk from the bedroom to the bathroom?" or
    "how far is the kitchen from the garage door?". Both ids must come from the room directory."""
    twin = get_twin(runtime.context.property_id)
    for rid in (from_room_id, to_room_id):
        if error := _room_or_error(twin, rid):
            return error
    names = prompts.room_display_names(twin)
    if from_room_id == to_room_id:
        return "Error: from_room_id and to_room_id are the same room."
    path = views.route(twin, from_room_id, to_room_id)
    if path is None:
        return (
            f"The scan has no walkable path recorded between {names[from_room_id]} and {names[to_room_id]}. "
            "Say you can't trace that walk, and offer to show each room instead."
        )
    _emit(
        runtime,
        "viewer.walk",
        {
            "from_room_id": from_room_id,
            "to_room_id": to_room_id,
            "sweep_ids": path.sweep_ids,
            "look_at": path.look_at.model_dump(),
            "distance_m": round(path.distance_m, 1),
            "duration_s": round(path.seconds, 1),
        },
    )
    through = [names[r] for r in path.room_ids[1:-1]]
    text = (
        f"The user is watching the walk from {names[from_room_id]} to {names[to_room_id]}: "
        f"{path.distance_m:.1f} m, about {path.seconds:.0f} seconds at a normal pace"
    )
    text += f", through {', '.join(through)}" if through else ", directly next door"
    text += f", with {'stairs' if path.floor_changes else 'no stairs'}."
    if path.unmapped_steps:
        text += " Part of the route crosses between rooms where the scan recorded no door, so it is approximate."
    return text


@tool
def viewer_set_mode(mode: Literal["tour", "sun", "sound", "neighborhood", "design"], runtime: Runtime) -> str:
    """Switch the user's screen: tour (photo walkthrough), sun (light and shadows), sound (noise),
    neighborhood (map and comps), design (furniture layouts)."""
    _emit(runtime, "viewer.setMode", {"mode": mode})
    return f"The user's screen now shows the {mode} view."


@tool
def viewer_set_time(datetime_iso: str, runtime: Runtime) -> str:
    """Set the date and time of day shown in the sun view.

    datetime_iso: ISO 8601 in the property's local time, with its UTC offset,
    e.g. 2026-12-21T08:00:00-06:00.
    """
    try:
        dt = datetime.fromisoformat(datetime_iso)
    except ValueError:
        return f"Error: {datetime_iso!r} is not an ISO 8601 datetime."
    if dt.tzinfo is None:
        return "Error: the datetime needs a UTC offset, e.g. 2026-12-21T08:00:00-06:00."
    _emit(runtime, "viewer.setTime", {"datetime": dt.isoformat()})
    return f"The sun view now shows {dt.strftime('%B %d, %H:%M')} local time."


PROPERTY_TOOLS = [list_rooms, get_room_geometry]
SUN_TOOLS = [sun_by_room, sun_report, daylight_at]
UI_TOOLS = [viewer_goto, viewer_walk, viewer_set_mode, viewer_set_time]
