"""Furniture tools: what the family owns, what stands in the house, proposing layouts, showing
them. Owner: B. Rules: docs/specs/furniture.md.

`propose_layouts` belongs to the designer subagent. It turns wishes into solved layouts
(sim/layout_solver.py) and saves them as proposals. Coordinates exist only between the solver and
the database; the agents handle item_ids, room_ids and layout_ids (B1).
"""

from __future__ import annotations

import math
import uuid

from langchain.tools import ToolRuntime, tool

from packages.schema.layout import LayoutVariant, Placement
from packages.schema.twin import Vec3
from services.api.agent import prompts, views
from services.api.agent.context import AgentContext
from services.api.furniture import house
from services.api.furniture.house import FurnitureError
from services.api.furniture.store import furniture_store
from services.api.sim import layout_solver
from services.api.sim.layout_solver import Constraint
from services.api.twin.service import get_twin

Runtime = ToolRuntime[AgentContext]


def _changed(runtime: Runtime) -> None:
    """Tell every screen to load the furniture again."""
    runtime.stream_writer({"type": "layout.changed", "payload": {}})


@tool
def inventory_search(runtime: Runtime, query: str = "") -> str:
    """The family's furniture: item_id, what it is, whose it is, size, style, and the room it
    stands in now in this house (if any). query filters by a word in the name, kind, style or
    owner ("sofa", "oak", "Maya"). Leave it empty for everything."""
    ctx = runtime.context
    items = house.pool(ctx.furniture_user)
    twin = get_twin(ctx.property_id)
    names = prompts.room_display_names(twin)
    where = {str(p.item_id): names.get(p.room_id, p.room_id) for p in furniture_store().house_pieces(ctx.furniture_user, ctx.property_id)}
    words = query.lower().split()
    rows = []
    for i in items:
        look = ", ".join(v for v in (i.attributes.style, i.attributes.color, i.attributes.material) if v)
        owner = "yours" if i.user_id == ctx.user_id else f"{i.owner_name or 'the house'}'s"
        stands = f"in {where[str(i.item_id)]}" if str(i.item_id) in where else "in storage"
        text = (f"{i.label} ({i.item_class}) · item_id {i.item_id} · {owner} · {stands} · "
                f"{i.dims_m.w:.2f} m wide × {i.dims_m.d:.2f} m deep × {i.dims_m.h:.2f} m high · {look}")
        if all(w in text.lower() for w in words):
            rows.append("- " + text)
    if not items:
        return "The user has no furniture saved yet. They can add pieces by uploading a photo or product screenshot."
    return "\n".join(rows) if rows else f"Nothing matches {query!r}. Call again with no query to see everything."


@tool
def catalog_search(runtime: Runtime, query: str = "") -> str:
    """Ready-made furniture the user can add without a photo: generic pieces at real size, not
    products to buy. query filters by a word in the name or kind ("bed", "round table", "bookshelf").
    Leave it empty to see the kinds available."""
    from services.api.objects.providers import catalog

    words = query.lower().replace("_", " ").split()
    rows = [r for r in catalog.PIECES if all(w in f"{r[2]} {r[1].replace('_', ' ')}".lower() for w in words)]
    if not words:
        kinds = sorted({r[1].replace("_", " ") for r in catalog.PIECES})
        return f"{len(catalog.PIECES)} pieces. Kinds: {', '.join(kinds)}. Search with one of these words to see the pieces."
    if not rows:
        return f"Nothing in the catalog matches {query!r}. Call with no query to see the kinds available."
    return "\n".join(f"- {label} ({cls}) · catalog_id {cid} · {w:.2f} m wide × {d:.2f} m deep × {h:.2f} m high" for cid, cls, label, w, d, h in rows[:12])


@tool
def catalog_suggest(catalog_ids: list[str], runtime: Runtime, room_id: str | None = None, near_item_id: str | None = None) -> str:
    """Show catalog pieces to the user as picture cards in the chat. They add one with a click, and
    it is placed in the room at once. Use this whenever you recommend catalog pieces: two to four
    good candidates, best first. Do not list them in words as well.

    room_id: the room the piece is for (default: the room the user is in).
    near_item_id: one of the user's pieces already in that room to put the new piece next to, e.g.
    the desk when they asked for a desk chair.
    """
    from services.api.objects.providers import catalog

    unknown = [c for c in catalog_ids if c not in catalog.BY_ID]
    if unknown or not catalog_ids:
        return f"Error: unknown catalog_ids {unknown}. Use catalog_ids from catalog_search."
    pieces = [
        {
            "catalog_id": cid, "label": label, "item_class": cls, "dims_m": {"w": w, "d": d, "h": h},
            "thumbnail_url": catalog.thumbnail_url(cid), "source": catalog.SOURCE,
        }
        for cid, cls, label, w, d, h in (catalog.BY_ID[c] for c in catalog_ids[:4])
    ]
    runtime.stream_writer({
        "type": "catalog.suggest",
        "payload": {"property_id": runtime.context.property_id, "room_id": room_id, "near_item_id": near_item_id, "pieces": pieces},
    })
    return (
        f"The user now sees {len(pieces)} picture card(s) and can add one with a click. Say in one sentence why these, "
        "and do not call catalog_add for them: the click adds and places the piece."
    )


@tool
def catalog_add(catalog_id: str, runtime: Runtime) -> str:
    """Add a catalog piece to the user's furniture so it can be placed in a room. Ask first unless
    they told you to add it. Returns the new item_id. catalog_id comes from catalog_search."""
    from services.api.agent import memory_files
    from services.api.objects import pipeline
    from services.api.objects.providers import catalog

    if catalog_id not in catalog.BY_ID:
        return f"Error: unknown catalog_id {catalog_id!r}. Use a catalog_id from catalog_search."
    store, user_id = furniture_store(), runtime.context.user_id  # the new piece is the person's own (F1)
    try:
        item = pipeline.add_from_catalog(catalog_id, user_id, store)
    except Exception as e:
        return f"Error: that piece could not be added ({e})."
    if runtime.store is not None:
        memory_files.sync_inventory(runtime.store, user_id, store.list_items(user_id))
    _changed(runtime)
    return (
        f"Added {item.label} ({item.item_class}), {item.dims_m.w:.2f} × {item.dims_m.d:.2f} × {item.dims_m.h:.2f} m, "
        f"item_id {item.item_id}. It is a generic stand-in, not a specific product: say so."
    )


@tool
def remove_from_room(item_id: str, room_id: str, runtime: Runtime) -> str:
    """Take one piece out of a room of this house. The piece stays in the family's furniture and
    can be placed again. Use it for "take the armchair out" or "remove the table from here"."""
    store, ctx = furniture_store(), runtime.context
    piece = store.house_piece(ctx.furniture_user, ctx.property_id, item_id)
    if piece is None or piece.room_id != room_id:
        return "That piece is not in that room. Check with inventory_search where it stands, and the room_id in the room directory."
    store.remove_piece(ctx.furniture_user, ctx.property_id, item_id)
    _changed(runtime)
    item = store.get_item(item_id)
    return f"{item.label if item else 'The piece'} is out of the room. It is still in the family's furniture."


@tool
def delete_from_inventory(item_id: str, runtime: Runtime) -> str:
    """Delete one of the user's own pieces for good. It also leaves every room it stood in. This
    cannot be undone: only call it after the user has clearly said to delete that piece."""
    from services.api.agent import memory_files

    ctx = runtime.context
    try:
        item = house.delete_item(ctx.user_id, item_id)
    except FurnitureError as e:
        return f"Error: {e.detail}." if e.status != 404 else f"Error: unknown item_id {item_id!r}. Use an item_id from inventory_search."
    if runtime.store is not None:
        memory_files.sync_inventory(runtime.store, ctx.user_id, furniture_store().list_items(ctx.user_id))
    _changed(runtime)
    return f"Deleted {item.label} from the furniture; it is out of every room too."


@tool
def propose_layouts(room_id: str, item_ids: list[str], wishes: list[Constraint], idea: str, runtime: Runtime, n: int = 3) -> str:
    """Arrange the user's furniture in a room. Returns up to n saved layouts, each with a layout_id.

    You say what should be true; the solver finds positions. It always keeps pieces inside the
    room, apart from each other, clear of doors and windows, and leaves a path to walk.

    item_ids: pieces to place, from inventory_search.
    idea: a few words naming the arrangement, e.g. "Conversation corner by the window".
    wishes: each is {kind, item, target, weight}. `item` is an item_id. Kinds and their targets:
      against_wall  target "any" | "longest" | "window" (a wall with a window) | "solid" (without)
                    | "north" | "east" | "south" | "west" (the wall on that side of the room)
      faces         target an item_id | "window" | "door" | "center"
      near          target an item_id
      in_front_of   target an item_id   (e.g. coffee table in front of the sofa)
      beside        target an item_id   (e.g. side table beside the armchair)
      away_from     target "door" | "window"
    weight (default 1) says how much a wish matters; use 3 for something the user insisted on.
    """
    ctx = runtime.context
    twin = get_twin(ctx.property_id)
    room = next((r for r in twin.rooms if r.room_id == room_id), None)
    if room is None:
        return f"Error: unknown room_id {room_id!r}. Use a room_id from the room directory."
    owned = {str(i.item_id): i for i in house.pool(ctx.furniture_user)}
    unknown = [i for i in item_ids if i not in owned]
    if unknown or not item_ids:
        return f"Error: unknown item_ids {unknown}. Use item_ids from inventory_search."
    bad = [w.item for w in wishes if w.item not in item_ids]
    if bad:
        return f"Error: wishes mention items that are not in item_ids: {sorted(set(bad))}."

    items = [
        layout_solver.Item(k, v.label, v.item_class, v.dims_m.w, v.dims_m.d, v.dims_m.h) for k, v in owned.items() if k in item_ids
    ]
    floor = views.floor_height(twin, room_id)
    on_real_floor = room.model_copy(update={"floor_y": floor})  # window sills are measured from here
    result = layout_solver.solve(
        on_real_floor, twin.openings, items, wishes, n=max(1, min(n, 4)), walls=twin.walls, sweeps=twin.sweeps, compass=compass_axes(twin)
    )
    name = prompts.room_display_names(twin)[room_id]
    if not result.variants:
        return f"No layout for {name}: {result.reason}. Try fewer or smaller pieces, or another room."

    lines = [f"{len(result.variants)} layout(s) saved for {name}:"]
    for k, v in enumerate(result.variants, start=1):
        where = _where(twin, items, v.poses)
        rationale = f"{idea} (option {k}). {where[0].upper()}{where[1:]}. " + ("Meets: " + "; ".join(v.met) + ". " if v.met else "")
        rationale += "Could not fully meet: " + "; ".join(v.unmet) + "." if v.unmet else ""
        layout = LayoutVariant(
            layout_id=uuid.uuid4(),
            user_id=ctx.furniture_user,
            property_id=ctx.property_id,
            room_id=room_id,
            placements=[
                Placement(item_id=item_id, position=Vec3(x=round(p.x, 3), y=floor, z=round(p.z, 3)), yaw_deg=round(p.yaw, 1))
                for item_id, p in v.poses.items()
            ],
            score=v.score,
            rationale=rationale.strip(),
            status="proposed",
        )
        furniture_store().put_proposal(layout)
        lines.append(
            f"- layout_id {layout.layout_id} · score {v.score:.0f}/100 · where: {where} · "
            f"met: {'; '.join(v.met) or 'none'} · not met: {'; '.join(v.unmet) or 'none'}"
        )
    if result.reason:
        lines.append(f"Note: {result.reason}.")
    return "\n".join(lines)


def _place_in_view(runtime: Runtime, layout: LayoutVariant) -> None:
    """Put one layout's furniture in the room, in whatever view the user is in, and take them to
    that room. The user stays where they are: no tab switch."""
    twin = get_twin(runtime.context.property_id)
    runtime.stream_writer({"type": "layout.select", "payload": {"layout_id": str(layout.layout_id)}})
    payload: dict = {"room_id": layout.room_id}
    if view := views.best_view(twin, layout.room_id):
        payload |= {"at_sweep_id": view.sweep_id, "look_at": view.look_at.model_dump()}
    runtime.stream_writer({"type": "viewer.goto", "payload": payload})


@tool
def design_show_variants(layout_ids: list[str], runtime: Runtime) -> str:
    """Show the designer's layouts to the user. The first one is placed in the room right away, in
    the view the user is already in; all of them become cards the user can flip through.
    layout_ids come from the designer's answer, best first."""
    store, ctx = furniture_store(), runtime.context
    layouts = [store.get_proposal(ctx.furniture_user, i) for i in layout_ids]
    missing = [i for i, found in zip(layout_ids, layouts) if found is None]
    if missing or not layout_ids:
        return f"Error: unknown layout_ids {missing}. Use the layout_ids the designer returned."
    runtime.stream_writer({"type": "design.showVariants", "payload": {"layout_ids": layout_ids}})
    _place_in_view(runtime, layouts[0])
    return (
        f"Option 1 is now placed in the room and the user is looking at it. All {len(layout_ids)} option(s) are in the chat "
        "as small plans they can click to try, with a Keep button to settle on one. Say briefly what option 1 does. "
        "Nothing is decided until they press Keep: don't record a choice in memory before the layout_chosen event."
    )


@tool
def design_apply_layout(layout_id: str, runtime: Runtime) -> str:
    """Show a different saved layout in the room, e.g. when the user says "show me option 2".
    It is a preview: the house keeps its furniture until the user presses Keep."""
    layout = furniture_store().get_proposal(runtime.context.furniture_user, layout_id)
    if layout is None:
        return f"Error: unknown layout_id {layout_id!r}."
    _place_in_view(runtime, layout)
    return f"That layout is now shown in the room: {layout.rationale}"


def compass_axes(twin) -> dict[str, tuple[float, float]]:
    """North, east, south and west as unit (x, z) directions in the interior frame."""
    m = twin.interior_to_world  # (east, north) = [[m0, m8], [m1, m9]] @ (dx, dz)
    det = m[0] * m[9] - m[8] * m[1]
    north, east = (-m[8] / det, m[0] / det), (m[9] / det, -m[1] / det)
    unit = lambda v: (v[0] / math.hypot(*v), v[1] / math.hypot(*v))  # noqa: E731
    north, east = unit(north), unit(east)
    return {"north": north, "east": east, "south": (-north[0], -north[1]), "west": (-east[0], -east[1])}


def wall_sides(twin, room) -> list[tuple[str, float]]:
    """(side, length) for each wall of a room outline, the side being which way it looks out."""
    pts = room.polygon
    turn = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))  # > 0: counter-clockwise in (x, z)
    axes = compass_axes(twin)
    out = []
    for a, b in zip(pts, pts[1:] + pts[:1]):
        length = math.dist(a, b)
        if length < 0.3:
            continue
        nx, nz = (b[1] - a[1]) / length, -(b[0] - a[0]) / length
        if turn < 0:
            nx, nz = -nx, -nz
        out.append((max(axes, key=lambda k: axes[k][0] * nx + axes[k][1] * nz), length))
    return out


def _compass(twin, dx: float, dz: float) -> str:
    """Compass word for a direction in the interior frame (I-G10: world is east, north, up)."""
    m = twin.interior_to_world  # column-major 4x4
    east, north = m[0] * dx + m[8] * dz, m[1] * dx + m[9] * dz
    words = ["north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west"]
    return words[round(math.degrees(math.atan2(east, north)) % 360 / 45) % 8]


def _where(twin, items: list[layout_solver.Item], poses: dict[str, layout_solver.Pose]) -> str:
    """Plain words for where the pieces ended up, so the agents can tell the options apart."""
    parts = []
    for item in sorted(items, key=lambda i: -(i.w * i.d)):
        fx, fz = poses[item.item_id].front
        parts.append(f"{item.label} on the {_compass(twin, -fx, -fz)} side, facing {_compass(twin, fx, fz)}")
    return "; ".join(parts)


def describe_layout(user_id: str, property_id: str, layout_id: str) -> str | None:
    """One line about a saved layout, for telling the agent what the user picked."""
    layout = furniture_store().get_proposal(user_id, layout_id)
    if layout is None:
        return None
    room = prompts.room_display_names(get_twin(property_id)).get(layout.room_id, layout.room_id)
    return f"layout_id {layout.layout_id} in {room}: {layout.rationale}"
