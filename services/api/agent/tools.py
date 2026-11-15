"""Agent tools. Property tools wrap twin/service.py (WORKSTREAMS 2.5). UI tools emit events.

UI tools don't know about the WebSocket hub. They write `{"type", "payload"}` to LangGraph's custom
stream; whoever runs the turn (runner.py) forwards it. Every argument is an id or a datetime (B1)
and is checked against the twin before anything is emitted.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

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

    from services.api.agent.design_tools import wall_sides

    pts = room.polygon
    walls = sorted(wall_sides(twin, room), key=lambda w: -w[1])
    xs, zs = [p[0] for p in pts], [p[1] for p in pts]
    lines = [
        f"{names[room_id]} (`{room_id}`)",
        f"- Area: {room.area_m2:.1f} m²" if room.area_m2 is not None else "- Area: unknown",
        f"- Bounding box: {max(xs) - min(xs):.2f} m × {max(zs) - min(zs):.2f} m, ceiling {room.height_m:.2f} m",
        f"- Walls, by the side of the room they are on (m): {', '.join(f'{side} {w:.2f}' for side, w in walls)}",
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
    if not any(o.kind == "window" and room_id in (o.room_id, o.other_room_id) for o in twin.openings):
        lines.append(
            "- No windows are listed for this room. That is a gap in the scan's data as often as a fact: glass walls and "
            "sliding glass doors are recorded as doors to outside, or missed. Never tell the user this room has no "
            "windows; if it matters, say the data lists none and that the doors to outside may be glass."
        )
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


# --- neighborhood and price (C's area profile) --------------------------------------------------


def _not_ready(e: IntegrationNotConfigured) -> str:
    return f"Not available for this property yet ({e}). Say you can't look that up yet."


@tool
def area_profile(runtime: Runtime) -> str:
    """This home's listing facts (price, size, year built), how its price compares with similar
    homes nearby, and the nearest grocery, school, park and transit. Start here for any question
    about price, value or the neighborhood."""
    try:
        profile = service.area_profile(runtime.context.property_id)
    except IntegrationNotConfigured as e:
        return _not_ready(e)
    caveats = "".join(f"\n- {c}" for c in profile.caveats)
    return profile.markdown + (f"\n\n### Caveats (tell the user when they matter){caveats}" if caveats else "")


@tool
def area_search(query: str, runtime: Runtime) -> str:
    """Places near the home, nearest first. query is everyday words or a name: "coffee", "bus",
    "gym", "pharmacy", "Kroger". Distances are straight lines."""
    try:
        places = service.area_search(runtime.context.property_id, query, limit=6)
    except IntegrationNotConfigured as e:
        return _not_ready(e)
    if not places:
        return f"Nothing matching {query!r} within 2 km in the map data. That doesn't prove there is none."
    return "\n".join(
        f"- {p.name} ({p.category}): {p.distance_m:.0f} m, about {p.distance_m * 1.3 / 80:.0f} min walk" for p in places
    )


# --- sound inside the house (C's room-to-room acoustics) ----------------------------------------

SoundName = Literal["conversation", "tv", "music", "baby", "dog", "vacuum"]


def _loudness_word(dba: float) -> str:
    for limit, word in ((25, "inaudible"), (35, "faint"), (45, "clearly audible"), (55, "noticeable, like talk in the next room"), (65, "loud")):
        if dba < limit:
            return word
    return "very loud"


def _room_center(twin, room_id: str) -> tuple[float, float]:
    from shapely.geometry import Polygon

    return Polygon(next(r.polygon for r in twin.rooms if r.room_id == room_id)).representative_point().coords[0]


@tool
def sound_heard_from(
    source_room_id: str,
    sound: SoundName,
    runtime: Runtime,
    listener_room_id: str | None = None,
    doors: Literal["closed", "open"] = "closed",
) -> str:
    """How loud a sound made in one room is in the other rooms, e.g. "can I hear the TV in the
    living room from the bedroom?" or "a baby crying in this room: who hears it?".

    listener_room_id: the room you care about. Leave it out for the six loudest rooms.
    """
    twin = get_twin(runtime.context.property_id)
    for rid in (source_room_id, listener_room_id):
        if rid is not None and (error := _room_or_error(twin, rid)):
            return error
    try:
        x, z = _room_center(twin, source_room_id)
        field = service.sound_field(twin.property_id, source_room_id, x, z, preset_id=sound, doors=doors)
    except IntegrationNotConfigured as e:
        return _not_ready(e)
    except (KeyError, ValueError) as e:
        return f"Error: {e}"
    names = prompts.room_display_names(twin)
    rooms = [r for r in field.rooms if r.room_id != source_room_id]
    if listener_room_id is not None:
        rooms = [r for r in rooms if r.room_id == listener_room_id]
    lines = [f"{sound} in the middle of {names[source_room_id]}, doors {doors}:"]
    lines += [f"- {names[r.room_id]}: {r.laeq_dba:.0f} dBA, {_loudness_word(r.laeq_dba)}" for r in rooms[:6]]
    if len(lines) == 1:
        lines.append("- No level computed for that room: it isn't connected to the source in the scan.")
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
    _emit(runtime, "viewer.setMode", {"mode": "tour"})  # a walk is watched in the photo tour
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
def viewer_set_mode(mode: Literal["tour", "sound", "neighborhood", "design"], runtime: Runtime) -> str:
    """Switch the user's screen: tour (the walk through the house, with its sun and shadows),
    sound (noise), neighborhood (map and comps), design (furniture layouts)."""
    _emit(runtime, "viewer.setMode", {"mode": mode})
    return f"The user's screen now shows the {mode} view."


@tool
def viewer_set_time(datetime_iso: str, runtime: Runtime) -> str:
    """Set the date and time of day the house is shown at: the sun, the shadows and the light in
    the rooms on the tour follow it.

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
    return f"The house is now shown at {dt.strftime('%B %d, %H:%M')} local time, with the sun where it stands then."


@tool
def sound_place_source(room_id: str, sound: SoundName, runtime: Runtime) -> str:
    """Put a sound in the middle of a room and switch the user's screen to the sound view, so they
    hear what it is like from wherever they are standing in the tour."""
    twin = get_twin(runtime.context.property_id)
    if error := _room_or_error(twin, room_id):
        return error
    x, z = _room_center(twin, room_id)
    _emit(runtime, "viewer.setMode", {"mode": "sound"})
    _emit(runtime, "sound.placeSource", {"room_id": room_id, "x": round(x, 3), "z": round(z, 3), "preset_id": sound})
    return f"A {sound} sound is now playing from {prompts.room_display_names(twin)[room_id]}. The user hears it from the room they are in."


PROPERTY_TOOLS = [list_rooms, get_room_geometry]
SUN_TOOLS = [sun_by_room, sun_report, daylight_at]
AREA_TOOLS = [area_profile, area_search]
def _loudness(dba: float) -> str:
    """A level in words a buyer can picture."""
    if dba < 20:
        return "close to silent"
    if dba < 30:
        return "very quiet, quieter than a library"
    if dba < 40:
        return "quiet, like a library"
    if dba < 50:
        return "noticeable, like a quiet office"
    return "loud, like a conversation in the room"


# What people usually worry about, the hours it concerns, and the level usually taken as fine.
_CONCERNS = {
    "sleep": ("sleeping at night", [22, 23, 0, 1, 2, 3, 4, 5, 6], 30.0, "the usual guideline for a bedroom at night is 30 dBA"),
    "meetings": ("calls and meetings in the day", list(range(9, 18)), 35.0, "a room for calls wants about 35 dBA or less"),
    "evening": ("evenings at home", [18, 19, 20, 21], 40.0, "a living room in the evening is comfortable under about 40 dBA"),
}


@tool
def outside_noise(runtime: Runtime, room_id: str | None = None, concern: Literal["sleep", "meetings", "evening"] = "sleep", windows_open: bool = False) -> str:
    """How loud the outside (traffic, rail, the neighbourhood) is inside a room, judged for what the
    user cares about: "sleep" (22:00 to 07:00), "meetings" (calls and work, 09:00 to 18:00) or
    "evening" (18:00 to 22:00). room_id: one room. Leave it out to rank the rooms, quietest first
    (bedrooms only for sleep). windows_open: with the windows half open. Follow it with
    `sound_play_outside` when they want to hear it."""
    from packages.schema.reports import NoiseScenario

    twin = get_twin(runtime.context.property_id)
    names = prompts.room_display_names(twin)
    what, hours, fine, guideline = _CONCERNS[concern]
    scenario = NoiseScenario(windows_open=windows_open)

    def level(rid: str) -> tuple[float, int, float, list]:
        report = service.noise_report(twin.property_id, rid, scenario)
        at = [h for h in report.hourly if h.hour in hours]
        mean = 10 * math.log10(sum(10 ** (h.laeq_dba / 10) for h in at) / len(at))
        worst = max(at, key=lambda h: h.laeq_dba)
        return mean, worst.hour, worst.laeq_dba, report.sources

    try:
        if room_id is not None:
            if error := _room_or_error(twin, room_id):
                return error
            mean, hour, peak, sources = level(room_id)
            main = ", then ".join(s.name for s in sources[:2])
            verdict = "comfortably under" if mean <= fine - 5 else "under" if mean <= fine else "over"
            return (
                f"{names[room_id]}, {what}{' with the windows open' if windows_open else ''}: about {mean:.0f} dBA on average "
                f"({_loudness(mean)}), loudest around {hour:02d}:00 at {peak:.0f} dBA. That is {verdict} what is usually "
                f"wanted ({guideline}). Mostly: {main}. These are modelled from traffic counts and maps, good to about 3 dB."
            )
        rooms = [r for r in twin.rooms if _has_window(twin, r.room_id) and (concern != "sleep" or "bedroom" in r.label.lower())]
        rows = sorted((level(r.room_id)[0], names[r.room_id]) for r in rooms[:12])
    except Exception as e:  # no traffic data, no windows mapped
        return f"The outside noise can't be worked out here ({e})."
    lines = [f"Outside noise indoors for {what}, quietest first ({guideline}):"]
    lines += [f"- {name}: {mean:.0f} dBA, {_loudness(mean)}" for mean, name in rows]
    return "\n".join(lines)


@tool
def sound_play_outside(room_id: str, hour: int, runtime: Runtime) -> str:
    """Let the user hear the outside as it sounds in a room at one hour of the day (0 to 23): takes
    them to the room, sets the clock and turns the outside sounds on. Use 2 for the dead of night,
    7 or 8 for the morning rush, 14 for a working afternoon, 20 for the evening."""
    twin = get_twin(runtime.context.property_id)
    if error := _room_or_error(twin, room_id):
        return error
    hour = max(0, min(23, int(hour)))
    when = datetime.now(ZoneInfo(twin.timezone)).replace(hour=hour, minute=0, second=0, microsecond=0)
    payload: dict = {"room_id": room_id}
    if view := views.best_view(twin, room_id):
        payload |= {"at_sweep_id": view.sweep_id, "look_at": view.look_at.model_dump()}
    _emit(runtime, "viewer.goto", payload)
    _emit(runtime, "viewer.setTime", {"datetime": when.isoformat()})
    _emit(runtime, "viewer.playback", {"listening": True, "playing": False})
    return (
        f"The user is in {prompts.room_display_names(twin)[room_id]} hearing the outside at {hour:02d}:00. The sound is "
        "playing on the big screen now: say one short sentence and then stay quiet so they can listen."
    )


SOUND_TOOLS = [sound_heard_from, outside_noise, sound_play_outside]
@tool
def viewer_step(direction: Literal["forward", "back", "left", "right"], runtime: Runtime, steps: int = 1) -> str:
    """Move the user a little from where they stand: "closer", "back up", "scoot left". The
    direction is relative to what they are looking at now. The house is walked in hops of about a
    metre between scan positions; `steps` is how many hops (1 to 4). Stays in the same room unless
    a hop goes through a doorway."""
    steps = max(1, min(4, steps))
    angle = {"forward": 0, "right": 90, "back": 180, "left": -90}[direction]
    _emit(runtime, "viewer.step", {"direction": "forward", "angle_deg": angle, "steps": steps})
    return f"Moved {steps} hop{'s' if steps > 1 else ''} {direction} (if there was somewhere to stand that way)."


@tool
def viewer_turn(direction: Literal["left", "right", "up", "down", "around"], runtime: Runtime, degrees: float = 45) -> str:
    """Turn the user's view where they stand: "look left", "look up at the ceiling", "turn around".
    `degrees` is how far (around is always half a turn)."""
    degrees = 180.0 if direction == "around" else max(5.0, min(180.0, degrees))
    yaw = {"left": -degrees, "right": degrees, "around": degrees}.get(direction, 0.0)
    pitch = {"up": min(degrees, 60.0), "down": -min(degrees, 60.0)}.get(direction, 0.0)
    _emit(runtime, "viewer.look", {"yaw_delta_deg": yaw, "pitch_delta_deg": pitch})
    return f"Turned the view {direction}."


@tool
def viewer_zoom(direction: Literal["in", "out"], runtime: Runtime) -> str:
    """Zoom the user's view in on what they are looking at, or back out. For a detail across the
    room; to actually get nearer, use viewer_step."""
    _emit(runtime, "viewer.zoom", {"delta": 0.7 if direction == "in" else -0.7})
    return f"Zoomed {direction}."


UI_TOOLS = [viewer_goto, viewer_walk, viewer_step, viewer_turn, viewer_zoom, viewer_set_mode, viewer_set_time, sound_place_source]
