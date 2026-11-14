"""Agent tools. Property tools wrap twin/service.py (WORKSTREAMS 2.5). UI tools emit events.

UI tools don't know about the WebSocket hub. They write `{"type", "payload"}` to LangGraph's custom
stream; whoever runs the turn (runner.py) forwards it. Every argument is an id or a datetime (B1)
and is checked against the twin before anything is emitted.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from langchain.tools import ToolRuntime, tool

from services.api.agent import prompts
from services.api.agent.context import AgentContext
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
            desc += f", to {names.get(far, 'outside or unlabeled space')}"
        else:
            desc += (
                f", sill {a.y - room.floor_y:.2f} m above floor, faces "
                f"{prompts.compass_point(o.facade_azimuth_deg)}, {o.glazing} glazing"
            )
        lines.append(desc)
    return "\n".join(lines)


# --- UI tools (WORKSTREAMS 2.3: one tool -> one event) ----------------------------------------


@tool
def viewer_goto(room_id: str, runtime: Runtime) -> str:
    """Move the user's view into a room. room_id must come from the room directory."""
    twin = get_twin(runtime.context.property_id)
    if not any(r.room_id == room_id for r in twin.rooms):
        return f"Error: unknown room_id {room_id!r}. Use a room_id from the room directory."
    _emit(runtime, "viewer.goto", {"room_id": room_id})
    return f"The user is now looking at {prompts.room_display_names(twin)[room_id]}."


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
UI_TOOLS = [viewer_goto, viewer_set_mode, viewer_set_time]
