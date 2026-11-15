"""The house register: what stands in each room of a house right now, as the guides read it.

Family members' guides, the family's shared guide and the designer all furnish one house. None of
them may answer from what it remembers about the furniture: a piece may have been moved by hand,
by another person, or by another guide a minute ago. So the furniture is read back from the
database every time (docs/specs/furniture.md, F10 and F11):

- `register(...)`: the house as data. Every room with a piece in it; each piece with its number,
  its size, whose it is, where in the room it stands (in words), and who put it there; the
  family's pieces that stand in no room; and the last changes from the house's log.
- `render(...)`: the same as text. Every guide is shown it on every model call (agent/graph.py).
- `attach(...)`: from then on, each change to a house writes its register again to the memory
  store as `/memories/house/<property_id>.md`: one file per house, shared by everyone who
  furnishes it, read-only for the guides, and listed on the /me page.

Places are words only ("against the north wall, facing south"): the agents never see or produce a
coordinate (B1).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from zoneinfo import ZoneInfo

from packages.schema.inventory import InventoryItem
from packages.schema.twin import PropertyTwin, Room
from services.api.agent import prompts
from services.api.furniture.store import GUIDE, LAYOUT, PHOTO, furniture_store, house_key
from services.api.realtime.households import households
from services.api.twin.service import get_twin

log = logging.getLogger(__name__)

NAMESPACE = "house"  # the memory-store namespace of the saved registers: ("house", <house key>)
RECENT = 10  # changes shown
STORED_SHOWN = 24  # pieces in storage listed before "and N more"
NEAR_WALL_M = 0.35  # a piece this close to a wall (beyond its own half depth) stands against it
# Pieces with no front worth naming.
_NO_FACING = {"rug", "plant", "floor_lamp", "side_table", "coffee_table", "ottoman", "stool", "dining_table"}
_WORDS = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]


def namespace(key: str) -> tuple[str, ...]:
    """Where a house's saved register lives in the memory store. `key` is the house's key."""
    return (NAMESPACE, key)


def file_name(property_id: str) -> str:
    return f"{property_id}.md"


# --- where a piece stands, in words --------------------------------------------------------------


def compass_word(twin: PropertyTwin, dx: float, dz: float) -> str:
    """Compass word for a direction in the interior frame (I-G10: world is east, north, up)."""
    m = twin.interior_to_world  # column-major 4x4
    east, north = m[0] * dx + m[8] * dz, m[1] * dx + m[9] * dz
    return _WORDS[round(math.degrees(math.atan2(east, north)) % 360 / 45) % 8]


def _to_segment(px: float, pz: float, a: list[float], b: list[float]) -> float:
    ax, az, bx, bz = a[0], a[1], b[0], b[1]
    length2 = (bx - ax) ** 2 + (bz - az) ** 2
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((px - ax) * (bx - ax) + (pz - az) * (bz - az)) / length2))
    return math.hypot(px - (ax + t * (bx - ax)), pz - (az + t * (bz - az)))


def where(twin: PropertyTwin, room: Room, x: float, z: float, yaw_deg: float, item: InventoryItem | None) -> str:
    """Where in its room a piece stands: the wall or corner it is against, or which part of the
    open floor, and which way its front looks."""
    pts = [list(p) for p in room.polygon]
    turn = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))  # > 0: counter-clockwise in (x, z)
    reach = (max(item.dims_m.w, item.dims_m.d) / 2 if item else 0.5) + NEAR_WALL_M
    sides: dict[str, float] = {}
    for a, b in zip(pts, pts[1:] + pts[:1]):
        length = math.dist(a, b)
        if length < 0.3:
            continue
        d = _to_segment(x, z, a, b)
        if d > reach:
            continue
        nx, nz = (b[1] - a[1]) / length, -(b[0] - a[0]) / length  # the wall's outward side
        if turn < 0:
            nx, nz = -nx, -nz
        side = compass_word(twin, nx, nz)
        if "-" in side:  # a wall that runs diagonally: name it by its nearer main direction
            side = side.split("-")[0]
        sides[side] = min(d, sides.get(side, d))
    near = sorted(sides, key=sides.get)[:2]
    if len(near) == 2 and {near[0], near[1]} not in ({"north", "south"}, {"east", "west"}):
        ns = next(s for s in near if s in ("north", "south"))
        ew = next(s for s in near if s in ("east", "west"))
        place = f"in the {ns}-{ew} corner"
    elif near:
        place = f"against the {near[0]} wall"
    else:
        xs, zs = [p[0] for p in pts], [p[1] for p in pts]
        cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
        if math.hypot(x - cx, z - cz) < 0.2 * min(max(xs) - min(xs), max(zs) - min(zs)):
            place = "in the middle of the room"
        else:
            place = f"out on the floor, toward the {compass_word(twin, x - cx, z - cz)}"
    if item is None or item.item_class in _NO_FACING:
        return place
    r = math.radians(yaw_deg)  # a piece's front is +Z turned by its yaw, as in sim/layout_solver.Pose
    return f"{place}, facing {compass_word(twin, math.sin(r), math.cos(r))}"


# --- the register --------------------------------------------------------------------------------


def _people(key: str) -> set[str]:
    """Everyone whose furniture a house may hold: the family when the house is a family's, else
    the user and their own family (F6)."""
    store = households()
    return store.people_of(key) or store.family_of(key)


def _who(person_id: str, names: dict[str, str]) -> str:
    return names.get(person_id) or "the big screen"


def register(house_user_id: str, property_id: str, keyed: bool = False) -> dict:
    """The house's furniture as data. `house_user_id` is the big screen's user, or with `keyed`
    the house's own key (what a store listener is handed)."""
    key = house_user_id if keyed else house_key(house_user_id)
    twin = get_twin(property_id)
    store = furniture_store()
    pieces = store.house_pieces(key, property_id, keyed=True)
    people = _people(key)
    pool = {str(i.item_id): i for i in store.list_owned_by(people)}
    for p in pieces:  # a piece of someone who has since left the family still stands there
        if str(p.item_id) not in pool and (item := store.get_item(str(p.item_id))) is not None:
            pool[str(p.item_id)] = item
    history = store.house_log(key, property_id, RECENT * 4, keyed=True)
    names = households().names(people | {p.placed_by for p in pieces} | {h["by"] for h in history})
    room_names = prompts.room_short_names(twin)
    floors = {f.floor_id: f.index for f in twin.floors}

    def spot(p, item, room) -> str:
        """Where a piece is, in words: on the floor of its room, on another piece, or on a wall (F12)."""
        if p.on_item_id and (under := pool.get(str(p.on_item_id))) is not None:
            return f"on #{under.tag} {under.label}"
        on_floor = where(twin, room, p.position.x, p.position.z, p.yaw_deg, item)
        if p.lift_m > 0.05:
            wall = on_floor.split(",")[0].replace("against the", "on the").replace("in the", "on the wall in the")
            return f"{wall}, hung {p.lift_m:.1f} m above the floor"
        return on_floor

    def piece(p) -> dict:
        item = pool.get(str(p.item_id))
        room = next(r for r in twin.rooms if r.room_id == p.room_id)
        return {
            "number": item.tag if item else None,
            "item_id": str(p.item_id),
            "label": item.label if item else "a piece",
            "item_class": item.item_class if item else "other",
            "size_m": [round(v, 2) for v in (item.dims_m.w, item.dims_m.d, item.dims_m.h)] if item else None,
            "owner_id": item.user_id if item else None,
            "owner": names.get(item.user_id) if item else None,
            "where": spot(p, item, room),
            "on_item_id": str(p.on_item_id) if p.on_item_id else None,
            "lift_m": p.lift_m,
            "position": {"x": p.position.x, "z": p.position.z, "yaw_deg": p.yaw_deg},
            "placed_by_id": p.placed_by,
            "placed_by": _who(p.placed_by, names),
            "updated_at": p.updated_at,
        }

    rooms, empty = [], []
    for r in sorted(twin.rooms, key=lambda r: (floors.get(r.floor_id, 0), room_names[r.room_id])):
        here = [p for p in pieces if p.room_id == r.room_id]
        if here:
            rooms.append({"room_id": r.room_id, "name": room_names[r.room_id], "pieces": [piece(p) for p in here]})
        else:
            empty.append(room_names[r.room_id])
    standing = {str(p.item_id) for p in pieces}
    stored = [
        {"number": i.tag, "item_id": k, "label": i.label, "item_class": i.item_class,
         "size_m": [round(v, 2) for v in (i.dims_m.w, i.dims_m.d, i.dims_m.h)], "owner_id": i.user_id, "owner": names.get(i.user_id)}
        for k, i in pool.items() if k not in standing
    ]
    recent = [
        {**h, "by_id": h["by"], "by": _who(h["by"], names), "room": room_names.get(h["room_id"] or "", h["room_id"])}
        for h in history
    ]
    return {
        "property_id": property_id,
        "house": key,
        "timezone": twin.timezone,
        "pieces_standing": len(pieces),
        "rooms": rooms,
        "empty_rooms": empty,
        "in_storage": stored,
        "recent_changes": recent,
    }


def _size(size: list[float] | None) -> str:
    return f"{size[0]:.2f} × {size[1]:.2f} × {size[2]:.2f} m" if size else "size unknown"


def _whose(owner_id: str | None, owner: str | None, viewer: str | None) -> str:
    if viewer is not None and owner_id == viewer:
        return "your person's"
    return f"{owner}'s" if owner else "the house's"


def _changes(reg: dict, viewer: str | None) -> list[str]:
    """The log as sentences, oldest first. Pieces one person changed in one go share a line: a
    layout that was kept is one line, not one per piece."""
    zone = ZoneInfo(reg["timezone"])
    lines: list[tuple[tuple, str, list[str]]] = []
    for h in reg["recent_changes"]:
        mine = viewer is not None and h["by_id"] == viewer
        who = "Your person" if mine else h["by"][:1].upper() + h["by"][1:]
        guide = "You, their guide," if mine else f"{who}'s guide"
        when = datetime.fromisoformat(h["at"]).astimezone(zone).strftime("%b %d %H:%M")
        room = h["room"] or "a room"
        actor = guide if h["via"] == GUIDE else who
        if h["via"] == PHOTO and h["action"] in ("placed", "moved"):
            kind, head = "photo", f"{who} brought into {room}, from a photo of {'their' if mine else 'their own'} room,"
        elif h["via"] == PHOTO and h["action"] == "removed":
            kind, head = "photo-out", f"Making room for that, these went from {room} back to storage"
        elif h["via"] == LAYOUT and h["action"] in ("placed", "moved"):
            kind, head = "layout", f"{who} kept a layout for {room}, which set out"
        elif h["action"] == "placed":
            kind, head = "placed", f"{actor} put in {room}"
        elif h["action"] == "moved":
            kind, head = "moved", f"{actor} moved within {room}"
        elif h["action"] == "removed":
            if h["via"] == LAYOUT:
                actor = f"The layout {'your person' if mine else who} kept"
            kind, head = "removed", f"{actor} took out of {room} (back to storage)"
        else:
            kind, head = "deleted", f"{actor} deleted for good, from {room}"
        group = (h["by_id"], h["room_id"], h["via"], kind)
        name = f"#{h['tag']} {h['label']}" if h["tag"] else h["label"]
        if lines and lines[-1][0] == group:
            lines[-1][2].append(name)
        else:
            lines.append((group, f"{when}: {head}", [name]))
    return [f"- {head}: {', '.join(names)}" for _group, head, names in lines][-RECENT:]


def render(reg: dict, viewer: str | None = None) -> str:
    """The register as text. `viewer`: the person the reader (a guide) speaks for. Their pieces and
    doings read "your person's", never a bare "you", which a guide takes to mean itself."""
    out = [
        "## The furniture in this house, right now",
        "Read from the house's database for this reply: the only truth about furniture. A piece stands where it is "
        "listed; a piece not listed in a room is not in it, whatever was said or planned earlier. Tools take a piece's "
        "number (#1234) as its item_id."
        + (' "Your person" is the one you are talking with.' if viewer is not None else ""),
        "",
    ]
    if not reg["rooms"]:
        out.append("No room of this house has any furniture in it yet.")
    for room in reg["rooms"]:
        n = len(room["pieces"])
        out.append(f"{room['name']} (room_id `{room['room_id']}`): {n} piece{'s' if n != 1 else ''}")
        for p in room["pieces"]:
            by = "your person (or you, for them)" if viewer is not None and p["placed_by_id"] == viewer else p["placed_by"]
            out.append(
                f"- #{p['number']} {p['label']} ({p['item_class']}, {_size(p['size_m'])}): {p['where']}. "
                f"{_whose(p['owner_id'], p['owner'], viewer).capitalize()}; last placed by {by}."
            )
    if reg["rooms"] and reg["empty_rooms"]:
        out.append(f"Every other room ({len(reg['empty_rooms'])} of them) has no furniture in it.")
    stored = reg["in_storage"]
    if stored:
        shown = "; ".join(
            f"#{s['number']} {s['label']} ({s['item_class']}, {_size(s['size_m'])}, {_whose(s['owner_id'], s['owner'], viewer)})" for s in stored[:STORED_SHOWN]
        )
        more = f"; and {len(stored) - STORED_SHOWN} more (see `inventory_search`)" if len(stored) > STORED_SHOWN else ""
        out += ["", f"In storage (the family's, standing in no room of this house): {shown}{more}."]
    else:
        out += ["", "In storage: nothing. Every piece the family has stands in a room above."]
    changes = _changes(reg, viewer)
    if changes:
        out += ["", "Last changes to this house, newest last:", *changes]
    return "\n".join(out)


def for_agent(house_user_id: str, property_id: str, viewer: str | None) -> str:
    """What a guide is shown on a model call. Never raises: a house that cannot be read says so."""
    try:
        return render(register(house_user_id, property_id), viewer)
    except Exception as e:  # no twin for this property, or the database is away
        log.warning("house register unavailable: %s", e)
        return ("## The furniture in this house, right now\nThe register could not be read just now. Say nothing about where "
                "furniture stands until `inventory_search` has told you.")


# --- the saved copy ------------------------------------------------------------------------------

_memory = None  # the memory store the registers are saved to


def save(key: str, property_id: str) -> None:
    """Write a house's register to the memory store (called after every change to that house)."""
    if _memory is None:
        return
    from services.api.agent import memory_files

    text = (
        "<!-- Written by the app each time furniture in this house changes. Not edited by hand. -->\n"
        + render(register(key, property_id, keyed=True))
        + "\n"
    )
    if memory_files.read_file(_memory, namespace(key), file_name(property_id)) != text:
        memory_files.write_file(_memory, namespace(key), file_name(property_id), text)


def attach(memory_store) -> None:
    """Save every house's register to `memory_store` from now on, each time that house changes."""
    global _memory
    _memory = memory_store
    furniture_store().subscribe(save)


def saved(house_user_id: str, property_id: str) -> str | None:
    """The saved register of a screen's house, writing it first if it was never saved."""
    if _memory is None:
        return None
    from services.api.agent import memory_files

    key = house_key(house_user_id)
    if memory_files.read_file(_memory, namespace(key), file_name(property_id)) is None:
        save(key, property_id)
    return memory_files.read_file(_memory, namespace(key), file_name(property_id))
