"""Furniture tools: what the family owns, what stands in the house, proposing layouts, showing
them. Owner: B. Rules: docs/specs/furniture.md.

`propose_layouts` belongs to the designer subagent. It turns wishes into solved layouts
(sim/layout_solver.py) and saves them as proposals. Coordinates exist only between the solver and
the database; the agents handle pieces, rooms and layouts by id (B1).

The ids the agents see are short. A piece is its number ("#4821", F10), a layout the first eight
characters of its id: a fast model copies a 36-character UUID wrong often enough to matter. Every
tool takes the short form (or the full id) and looks the real one up (`_piece`, `_layout`).
"""

from __future__ import annotations

import math
import uuid
from typing import Literal

from langchain.tools import ToolRuntime, tool
from pydantic import BaseModel, Field

from packages.schema.inventory import InventoryItem
from packages.schema.layout import LayoutVariant, Placement, PlacePiece
from packages.schema.twin import Vec3
from services.api.agent import design_gate, house_plans
from services.api.agent import prompts, views
from services.api.agent.context import AgentContext
from services.api.furniture import house, ledger
from services.api.furniture.house import FurnitureError
from services.api.furniture.store import GUIDE, furniture_store
from services.api.sim import layout_solver
from services.api.sim.layout_solver import Constraint
from services.api.twin.service import get_twin

Runtime = ToolRuntime[AgentContext]

SHORT_LAYOUT = 8  # characters of a layout_id the agents are shown


def _piece(ctx: AgentContext, ref: str) -> InventoryItem | None:
    """A piece of the family's furniture by its number ("4821", "#4821") or its item_id."""
    found = furniture_store().find_item(ref)
    return house.pool_item(ctx.furniture_user, str(found.item_id)) if found else None


def _name(item: InventoryItem) -> str:
    """How a piece is named to the agents: its number first."""
    return f"#{item.tag} {item.label}"


def _layout(ctx: AgentContext, ref: str) -> LayoutVariant | None:
    """A saved layout by its id, or by the short form the agents are shown (the start of it)."""
    ref = str(ref).strip().lower().removeprefix("layout_id").removeprefix("layout").strip(" :#`")
    store = furniture_store()
    if len(ref) >= 32 and (exact := store.get_proposal(ctx.furniture_user, ref)) is not None:
        return exact
    if len(ref) < 6:
        return None
    from services.api.furniture.store import _sample_layouts

    known = store.list_proposals(ctx.furniture_user, ctx.property_id) + [l for l in _sample_layouts() if l.user_id == ctx.furniture_user]
    hits = {str(l.layout_id): l for l in known if str(l.layout_id).startswith(ref)}
    return next(iter(hits.values())) if len(hits) == 1 else None


def _short(layout_id) -> str:
    return str(layout_id)[:SHORT_LAYOUT]


def _room_now(ctx: AgentContext, room_id: str) -> str:
    """What stands in one room at this moment, in a sentence: said back after every change, so the
    guide's next words come from the house and not from what it meant to do."""
    reg = ledger.register(ctx.furniture_user, ctx.property_id)
    room = next((r for r in reg["rooms"] if r["room_id"] == room_id), None)
    if room is None:
        return "That room now has no furniture in it."
    listed = "; ".join(f"#{p['number']} {p['label']} {p['where']}" for p in room["pieces"])
    return f"{room['name']} now holds {len(room['pieces'])} piece(s): {listed}."


def _changed(runtime: Runtime) -> None:
    """Tell every screen to load the furniture again."""
    runtime.stream_writer({"type": "layout.changed", "payload": {}})


_briefs: dict[tuple[str, str], str] = {}  # (conversation, room_id) -> the brief the designer was last given


def _one_room_at_a_time(runtime: Runtime, room_id: str, brief: str | None = None) -> str | None:
    """What to tell the guide when this room is already being planned, else None. Other rooms
    never block: several are planned at once and shown one at a time (agent/design_gate.py). A
    new brief for a room still being arranged doesn't either: that run is replaced."""
    ctx = runtime.context
    conversation = design_gate.key(ctx.session_id, ctx.user_id)
    doing = design_gate.state(conversation, room_id)
    if doing is None or (doing == "arranging" and brief is not None and _briefs.get((conversation, room_id)) != brief):
        return None
    name = prompts.room_short_names(get_twin(ctx.property_id)).get(room_id, room_id)
    if doing == "arranging":
        return f"{name} is already being arranged. Don't start it again; you get layouts_ready when it is done."
    return f"{name} is already arranged and comes up next, once the user has decided on the room they are looking at. Don't start it again."


def _someone_elses_room(runtime: Runtime, room_id: str, go_ahead: bool) -> str | None:
    """The refusal to give when another person already has plans for this room and this agent's
    person has not yet been asked about it, else None."""
    theirs = house_plans.of_others(runtime.context, room_id)
    if not theirs or go_ahead:
        return None
    p = theirs[0]
    name = prompts.room_short_names(get_twin(runtime.context.property_id)).get(room_id, room_id)
    return (
        f"Hold on: {p.who} {p.status} for {name}. Nothing has been started. Tell your person that in one sentence and ask "
        f"whether to go ahead anyway or leave this room to {p.who}, then stop. Only if they then say to go ahead, call "
        "this again with go_ahead_over_other_plans=true."
    )


def _someone_elses_piece(ctx: AgentContext, item: InventoryItem, go_ahead: bool) -> str | None:
    """The refusal to give when a piece belongs to, or was last placed by, another person of the
    family and this agent's person has not been asked about touching it, else None. The house's
    own pieces (the big screen's) are everyone's to move."""
    if go_ahead:
        return None
    standing = furniture_store().house_piece(ctx.furniture_user, ctx.property_id, str(item.item_id))
    others = {item.user_id, standing.placed_by if standing else item.user_id} - {ctx.user_id, ctx.furniture_user}
    if not others:
        return None
    from services.api.realtime.households import households

    names = households().names(others)
    facts = []
    if item.user_id in others:
        facts.append(f"it belongs to {names.get(item.user_id, 'someone else')}")
    if standing and standing.placed_by in others:
        facts.append(f"{names.get(standing.placed_by, 'someone else')} put it where it stands")
    return (
        f"Hold on, about {_name(item)}: {' and '.join(facts)}. Nothing has been changed. Tell your person that in one "
        "short sentence and ask whether to go ahead, then stop. Only if they then say yes, call this again with "
        "go_ahead_with_others_piece=true."
    )


# Not a UI event: the session bridge takes this off the stream and runs the designer itself.
BACKGROUND_DESIGN = "background.design"


# --- questions with pictures --------------------------------------------------------------------


class Choice(BaseModel):
    """One thing the user can pick. Name what it is with one of the ids, so the card gets a picture."""

    label: str = Field(description="What the card says: two to five words. Give an id below only when the option IS that room, set or piece; an answer in words (\"Just me\", \"Yes\") has none.")
    detail: str | None = Field(default=None, description="One short line under the label. Leave it out for a room, a set or a piece: the app fills it in.")
    room_id: str | None = Field(default=None, description="The option is a room of this house: its room_id. The card shows the room's plan.")
    set_id: str | None = Field(default=None, description="The option is a furniture set: its set_id from catalog_sets. The card shows its pieces.")
    catalog_id: str | None = Field(default=None, description="The option is one catalog piece: its catalog_id.")
    item_id: str | None = Field(default=None, description="The option is one of the family's pieces: its number.")


# choice_id -> option_id -> what the card stood for, in the words the guide needs back
# ("set_id sloane_living"). In memory: after a restart a tap still comes back with its label.
_choices: dict[str, dict[str, str]] = {}


def choice_note(choice_id: str, option_id: str) -> str | None:
    """What a tapped card stood for, for the turn that tells the guide about the tap."""
    return _choices.get(choice_id, {}).get(option_id)


@tool
def ask_choice(question: str, options: list[Choice], runtime: Runtime) -> str:
    """Ask the user to pick one of two to four things by tapping a card with a picture. The cards
    pop up on their phone, over whatever screen it is on, and show in the chat on the big screen.
    Use it whenever your question has a few definite answers: which room, which furniture set or
    style, which of a few pieces, this or that. Their tap comes back to you as their next message.

    question: the question as you would say it, short ("Which look for the living room?").
    options: two to four, best first. Give each the id of what it is (room_id, set_id, catalog_id
    or item_id) so its card shows a picture; an option with no id is a plain card.
    """
    from services.api.objects.providers import catalog

    ctx = runtime.context
    if not 2 <= len(options) <= 4:
        return "Error: give two to four options."
    # An id says what an option IS. The same id on two options ("Just me" and "Two of us", both
    # with this room's id) says nothing, and would draw the same picture on each: left out.
    for field in ("room_id", "set_id", "catalog_id", "item_id"):
        values = [getattr(o, field) for o in options if getattr(o, field)]
        if len(values) != len(set(values)):
            options = [o.model_copy(update={field: None}) for o in options]
    twin = get_twin(ctx.property_id)
    floors = {f.floor_id: f.label or f"Floor {f.index + 1}" for f in twin.floors}
    cards, notes = [], {}
    for n, option in enumerate(options, start=1):
        card: dict = {"option_id": str(n), "label": option.label.strip()[:60], "detail": option.detail, "image_urls": []}
        note = None
        if option.room_id:
            room = house.room_by_id(twin, option.room_id)
            if room is None:
                return f"Error: no room with id {option.room_id}. Use an id from the room directory."
            card["room_id"] = room.room_id
            card["detail"] = card["detail"] or " · ".join(
                part for part in (floors.get(room.floor_id), f"{room.area_m2 * prompts.SQFT_PER_M2:.0f} sq ft" if room.area_m2 else None) if part
            )
            note = f"room_id {room.room_id}"
        elif option.set_id:
            st = catalog.SET_BY_ID.get(option.set_id)
            if st is None:
                return f"Error: unknown set_id {option.set_id!r}. Use a set_id from catalog_sets."
            card["image_urls"] = [catalog.thumbnail_url(cid) for cid in st["pieces"][:4]]
            card["detail"] = card["detail"] or st.get("style") or st["look"]
            note = f"set_id {st['id']}"
        elif option.catalog_id:
            row = catalog.BY_ID.get(option.catalog_id)
            if row is None:
                return f"Error: unknown catalog_id {option.catalog_id!r}. Use a catalog_id from catalog_search."
            card["image_urls"] = [catalog.thumbnail_url(row[0])]
            card["detail"] = card["detail"] or f"{row[3]:.1f} × {row[4]:.1f} × {row[5]:.1f} m"
            note = f"catalog_id {row[0]}"
        elif option.item_id:
            item = _piece(ctx, option.item_id)
            if item is None:
                return f"Error: no piece {option.item_id!r}. Use a piece number from the house register."
            card["image_urls"] = [item.source_image_url] if item.source_image_url else []
            card["detail"] = card["detail"] or f"{item.dims_m.w:.1f} × {item.dims_m.d:.1f} × {item.dims_m.h:.1f} m"
            note = f"piece #{item.tag}"
        if note:
            notes[card["option_id"]] = note
        cards.append(card)
    choice_id = uuid.uuid4().hex[:12]
    _choices[choice_id] = notes
    while len(_choices) > 200:  # the oldest questions are long answered
        _choices.pop(next(iter(_choices)))
    runtime.stream_writer({"type": "choice.ask", "payload": {"choice_id": choice_id, "question": question.strip(), "options": cards}})
    return (
        f"{len(cards)} cards are on their screen. Now say the question itself in one short sentence and stop: do not read "
        "the options out or describe them, and do not act on a choice before their tap arrives as their next message."
    )

@tool
def inventory_search(runtime: Runtime, query: str = "") -> str:
    """The family's furniture: each piece's number, what it is, whose it is, size, style, and the
    room it stands in now in this house (if any). query filters by a word in the name, kind, style
    or owner ("sofa", "oak", "Maya"). Leave it empty for everything. The house register in your
    context already lists every piece; call this for the look of a piece, or to search."""
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
        text = (f"#{i.tag} {i.label} ({i.item_class}) · {owner} · {stands} · "
                f"{i.dims_m.w:.2f} m wide × {i.dims_m.d:.2f} m deep × {i.dims_m.h:.2f} m high" + (f" · {look}" if look else ""))
        if all(w in text.lower() for w in words):
            rows.append("- " + text)
    if not items:
        return "The user has no furniture saved yet. They can add pieces by uploading a photo or product screenshot."
    return "\n".join(rows) if rows else f"Nothing matches {query!r}. Call again with no query to see everything."


@tool
def catalog_sets(runtime: Runtime, room_kind: str = "") -> str:
    """The furniture sets in the catalog: each is a room's worth of pieces designed to go together,
    in one style. room_kind narrows it to the sets for one kind of room ("bedroom", "living",
    "dining", "office"). Call this before furnishing a room with pieces the user does not own."""
    from services.api.objects.providers import catalog

    words = room_kind.lower().split()
    rows = []
    for st in catalog.SETS:
        if words and not any(w in f"{st['rooms']} {st['name']}".lower() for w in words):
            continue
        names = []
        for cid in st["pieces"]:
            n = catalog.SET_COUNTS.get((st["id"], cid), 1)
            names.append(catalog.BY_ID[cid][2].split(",")[0] + (f" ×{n}" if n > 1 else ""))
        style = f" · style: {st['style']}" if st.get("style") else ""
        rows.append(f"- {st['name']} · set_id {st['id']}{style} · for: {st['rooms']}\n  {st['look']}\n  Pieces: {', '.join(names)}")
    if not rows:
        return f"No set is made for {room_kind!r}. Call again with no room_kind to see every set."
    return "\n".join(rows)


@tool
def furnish_with_set(set_id: str, room_id: str, runtime: Runtime, go_ahead_over_other_plans: bool = False) -> str:
    """Furnish a room with one catalog set: adds the set's pieces to the user's furniture and asks
    the interior designer to arrange them (in the background; you get `layouts_ready`). Use this
    when a room is empty or the user asks you to furnish or style it. One set per room.
    go_ahead_over_other_plans: only after the tool told you someone else has plans for this room
    and your person said to go ahead anyway."""
    from services.api.agent import memory_files
    from services.api.objects import pipeline
    from services.api.objects.providers import catalog

    st = catalog.SET_BY_ID.get(set_id)
    if st is None:
        return f"Error: unknown set_id {set_id!r}. Use a set_id from catalog_sets."
    twin = get_twin(runtime.context.property_id)
    if not any(r.room_id == room_id for r in twin.rooms):
        return f"Error: no room with id {room_id}. Use an id from the room directory."
    if refusal := _one_room_at_a_time(runtime, room_id) or _someone_elses_room(runtime, room_id, go_ahead_over_other_plans):
        return refusal
    store, user_id = furniture_store(), runtime.context.user_id  # new pieces are the person's own (F1)
    owned: dict[str, list] = {}
    for item in store.list_items(user_id):
        owned.setdefault(item.source_image_url, []).append(item)
    lines = []
    try:
        for cid in st["pieces"]:
            want = catalog.SET_COUNTS.get((set_id, cid), 1)
            have = owned.get(catalog.thumbnail_url(cid), [])  # pieces from an earlier try are used again
            while len(have) < want:
                have.append(pipeline.add_from_catalog(cid, user_id, store))
            for item in have[:want]:
                lines.append(f"- #{item.tag} {item.label} ({item.item_class})")
    except Exception as e:
        return f"Error: the set could not be added ({e})."
    if runtime.store is not None:
        memory_files.sync_inventory(runtime.store, user_id, store.list_items(user_id))
    _changed(runtime)
    brief = (
        f"Furnish this room with the {st['name']} set and nothing else. {st['look']}\n"
        "Use these pieces, the first ones first; leave out the last ones if the room is too small for all of them. "
        "A rug goes under the seating or beside the bed. Pieces:\n" + "\n".join(lines)
    )
    design_gate.started(design_gate.key(runtime.context.session_id, runtime.context.user_id), room_id)
    runtime.stream_writer({"type": BACKGROUND_DESIGN, "payload": {"room_id": room_id, "brief": brief}})
    return (
        f"The {st['name']} set ({len(lines)} pieces) is in their furniture and the designer has started arranging it. "
        "Tell the user in one sentence which set you chose and why it suits them, and that the room will be ready in "
        "a moment. Do not wait, and do not describe the layout yet."
    )


@tool
def catalog_search(runtime: Runtime, query: str = "") -> str:
    """Single pieces in the furniture catalog, for adding one thing to a room that already has
    furniture (a chair for a desk, a lamp). query filters by a word in the name or kind ("bed",
    "side table"). Each piece says which sets it belongs to: add pieces from the set the room
    already uses. Leave query empty to see the kinds available."""
    from services.api.objects.providers import catalog

    words = query.lower().replace("_", " ").split()
    rows = [r for r in catalog.PIECES if all(w in f"{r[2]} {r[1].replace('_', ' ')}".lower() for w in words)]
    if not words:
        kinds = sorted({r[1].replace("_", " ") for r in catalog.PIECES})
        return f"{len(catalog.PIECES)} pieces in {len(catalog.SETS)} sets (see catalog_sets). Kinds: {', '.join(kinds)}. Search with one of these words to see the pieces."
    if not rows:
        return f"Nothing in the catalog matches {query!r}. Call with no query to see the kinds available."
    return "\n".join(
        f"- {label} ({cls}) · catalog_id {cid} · {w:.2f} m wide × {d:.2f} m deep × {h:.2f} m high · sets: {', '.join(catalog.sets_of(cid)) or 'none'}"
        for cid, cls, label, w, d, h in rows[:12]
    )


@tool
def catalog_suggest(catalog_ids: list[str], runtime: Runtime, room_id: str | None = None, near_item_id: str | None = None) -> str:
    """Show catalog pieces to the user as picture cards in the chat. They add one with a click, and
    it is placed in the room at once. Use this whenever you recommend catalog pieces: two to four
    good candidates, best first. Do not list them in words as well.

    room_id: the room the piece is for (default: the room the user is in).
    near_item_id: the number of one of the user's pieces already in that room to put the new
    piece next to, e.g. the desk when they asked for a desk chair.
    """
    from services.api.objects.providers import catalog

    unknown = [c for c in catalog_ids if c not in catalog.BY_ID]
    if unknown or not catalog_ids:
        return f"Error: unknown catalog_ids {unknown}. Use catalog_ids from catalog_search."
    if near_item_id:  # a hint: a piece that can't be found is left out, and the cards still show
        beside = _piece(runtime.context, near_item_id)
        near_item_id = str(beside.item_id) if beside else None
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


SPOTS = ("north", "east", "south", "west", "north-east", "north-west", "south-east", "south-west", "middle")
Spot = Literal["north", "east", "south", "west", "north-east", "north-west", "south-east", "south-west", "middle"]
# Pieces that stand with their back to a wall when nobody says where.
_ON_WALLS = {"sofa", "loveseat", "sectional", "armchair", "bench", "desk", "console_table", "tv_stand", "bookshelf", "cabinet",
             "dresser", "wardrobe", "nightstand", "bed", "crib", "floor_lamp", "plant"}


def _walls(twin, room) -> list[tuple[str, float, tuple[float, float], tuple[float, float]]]:
    """Each wall of a room: the side it is on, its length, its middle, and the way into the room."""
    pts = room.polygon
    turn = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))  # > 0: counter-clockwise in (x, z)
    axes = compass_axes(twin)
    out = []
    for a, b in zip(pts, pts[1:] + pts[:1]):
        length = math.dist(a, b)
        if length < 0.3:
            continue
        nx, nz = (b[1] - a[1]) / length, -(b[0] - a[0]) / length  # the wall's outward side
        if turn < 0:
            nx, nz = -nx, -nz
        side = max(axes, key=lambda k: axes[k][0] * nx + axes[k][1] * nz)
        out.append((side, length, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), (-nx, -nz)))
    return out


def _spots(twin, room, where: str | None, item: InventoryItem) -> list[tuple[float, float]]:
    """Places to try standing a piece, best first: the named side, corner or middle of the room;
    with nothing named, a wall for a piece that belongs on one (the longest first), then the middle."""
    from shapely.geometry import Polygon

    middle = Polygon(room.polygon).representative_point().coords[0]
    walls = sorted(_walls(twin, room), key=lambda w: -w[1])
    inward = item.dims_m.d / 2 + 0.15
    by_wall = {}
    for side, _length, (mx, mz), (ix, iz) in walls:
        by_wall.setdefault(side, (mx + ix * inward, mz + iz * inward))  # the longest wall on each side
    if where == "middle":
        return [middle]
    if where in by_wall:
        return [by_wall[where]]
    if where and "-" in where:  # a corner: the outline's point furthest that way, pulled a little into the room
        axes = compass_axes(twin)
        dx, dz = (sum(axes[part][k] for part in where.split("-")) for k in (0, 1))
        cx, cz = max(room.polygon, key=lambda p: p[0] * dx + p[1] * dz)
        d = math.dist((cx, cz), middle) or 1.0
        pull = min(0.8, d / 2)
        return [(cx + (middle[0] - cx) / d * pull, cz + (middle[1] - cz) / d * pull)]
    if where:
        return []  # a side this room has no wall on
    return ([*by_wall.values()] if item.item_class in _ON_WALLS else []) + [middle]


def _stand(runtime: Runtime, item: InventoryItem, room_id: str, near_item_id: str | None = None, where: str | None = None) -> str:
    """Stand one piece in a room at the nearest spot that breaks no rule, and say what the room
    holds afterwards. Starts with "Error:" when nothing was placed."""
    ctx = runtime.context
    twin = get_twin(ctx.property_id)
    room = house.room_by_id(twin, room_id)
    if room is None:
        return f"Error: no room with id {room_id}. Use an id from the room directory."
    store = furniture_store()
    if near_item_id:
        beside = _piece(ctx, near_item_id)
        at = store.house_piece(ctx.furniture_user, ctx.property_id, str(beside.item_id)) if beside else None
        if at is None or at.room_id != room_id:
            return f"Error: piece {near_item_id} does not stand in that room. The house register says what does."
        tries = [(at.position.x, at.position.z)]
    else:
        tries = _spots(twin, room, where, item)
        if not tries:
            sides = ", ".join(sorted({w[0] for w in _walls(twin, room)}))
            return f"Error: that room has no wall on its {where} side. Its walls are on the {sides}."
    piece, why = None, "it could not be placed"
    for x, z in tries:
        try:
            piece = house.place(
                ctx.furniture_user, ctx.property_id, str(item.item_id),
                PlacePiece(x=x, z=z, room_id=room_id, floor_id=room.floor_id, snap=True), ctx.user_id, GUIDE,
            )
            break
        except FurnitureError as e:
            why = "; ".join(e.detail.get("violations", [])) if isinstance(e.detail, dict) else str(e.detail)
    if piece is None:
        return f"Error: {why}. Nothing was placed."
    _changed(runtime)
    spot_words = ledger.where(twin, house.room_by_id(twin, piece.room_id) or room, piece.position.x, piece.position.z, piece.yaw_deg, item)
    return f"{_name(item)} now stands {spot_words}. {_room_now(ctx, piece.room_id)}"


@tool
def catalog_add(catalog_id: str, runtime: Runtime, room_id: str | None = None, near_item_id: str | None = None, where: Spot | None = None) -> str:
    """Add a catalog piece to the family's furniture. No need to ask first: the catalog is theirs to
    use. With room_id (the room it is for) it is also stood in that room at once: next to
    near_item_id (the number of a piece in that room) if given, else on the `where` side or corner
    of the room if given, else wherever such a piece usually goes. Returns the new piece's number.
    catalog_id comes from catalog_search."""
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
    added = f"Added {item.label} ({item.item_class}), {item.dims_m.w:.2f} × {item.dims_m.d:.2f} × {item.dims_m.h:.2f} m, as piece #{item.tag}."
    if not room_id:
        return added + " It is in storage, not in a room yet: place_piece stands it in one."
    stood = _stand(runtime, item, room_id, near_item_id, where)
    if stood.startswith("Error:"):
        return f"{added} It is in storage: it could not be stood in the room ({stood.removeprefix('Error: ')})"
    return f"{added} {stood} Say where it went in a few words; they can drag or turn it on their phone."


@tool
def place_piece(
    item_id: str, room_id: str, runtime: Runtime, near_item_id: str | None = None, where: Spot | None = None,
    go_ahead_with_others_piece: bool = False,
) -> str:
    """Stand one piece of the family's furniture in a room, now, without redoing the room: a lamp
    by the sofa, a plant in the corner, a piece out of storage. A piece that already stands
    somewhere in this house moves. Only when the user asked for it in this message.

    item_id: the piece's number from the house register.
    near_item_id: the number of a piece in that room to put it next to.
    where: the side of the room ("north"), a corner ("south-west") or "middle". Leave both out and
    it goes where such a piece usually stands (a seat or a lamp against a wall, a rug in the middle).
    go_ahead_with_others_piece: only after the tool told you the piece is someone else's and your
    person said to go ahead.
    For a whole room, or several pieces together, use design_in_background."""
    item = _piece(runtime.context, item_id)
    if item is None:
        return f"Error: no piece {item_id!r} in the family's furniture. Use a piece number from the house register."
    if refusal := _someone_elses_piece(runtime.context, item, go_ahead_with_others_piece):
        return refusal
    return _stand(runtime, item, room_id, near_item_id, where)


@tool
def remove_from_room(item_id: str, room_id: str, runtime: Runtime, go_ahead_with_others_piece: bool = False) -> str:
    """Take one piece out of a room of this house. The piece stays in the family's furniture (it
    goes back to storage) and can be placed again. Use it for "take the armchair out" or "remove
    the table from here". item_id: the piece's number from the house register.
    go_ahead_with_others_piece: only after the tool told you the piece is someone else's and your
    person said to go ahead."""
    store, ctx = furniture_store(), runtime.context
    item = _piece(ctx, item_id)
    piece = store.house_piece(ctx.furniture_user, ctx.property_id, str(item.item_id)) if item else None
    if item is None or piece is None:
        return f"Error: piece {item_id!r} does not stand in this house. The house register says what stands where."
    if piece.room_id != room_id:
        name = prompts.room_short_names(get_twin(ctx.property_id)).get(piece.room_id, piece.room_id)
        return f"Error: {_name(item)} stands in {name} (room_id {piece.room_id}), not in that room. Nothing was taken out."
    if refusal := _someone_elses_piece(ctx, item, go_ahead_with_others_piece):
        return refusal
    store.remove_piece(ctx.furniture_user, ctx.property_id, str(item.item_id), by=ctx.user_id, via=GUIDE)
    _changed(runtime)
    return f"{_name(item)} is out of the room and back in storage. {_room_now(ctx, room_id)}"


@tool
def delete_from_inventory(item_id: str, runtime: Runtime) -> str:
    """Delete one of the user's own pieces for good. It also leaves every room it stood in. This
    cannot be undone: only call it after the user has clearly said to delete that piece.
    item_id: the piece's number from the house register."""
    from services.api.agent import memory_files

    ctx = runtime.context
    found = _piece(ctx, item_id)
    if found is None:
        return f"Error: no piece {item_id!r} in the family's furniture. Use a piece number from the house register."
    try:
        item = house.delete_item(ctx.user_id, str(found.item_id), GUIDE)
    except FurnitureError as e:
        return f"Error: {e.detail}."
    if runtime.store is not None:
        memory_files.sync_inventory(runtime.store, ctx.user_id, furniture_store().list_items(ctx.user_id))
    _changed(runtime)
    return f"Deleted {_name(found)} from the furniture for good; it is out of every room too."


@tool
def design_in_background(room_id: str, brief: str, runtime: Runtime, go_ahead_over_other_plans: bool = False) -> str:
    """Ask the interior designer to arrange a room. It works in the background, telling the user
    what it notices and showing its first options as it goes; you get a `layouts_ready` event with
    the layout_ids when it is done. Calling it again for the same room while it works replaces
    that run (say, with the user's answer to your question).

    brief: which pieces to use (or "whatever fits"), what the user wants from the room, and every
    relevant preference from memory word for word, marking the hard ones.
    go_ahead_over_other_plans: only after the tool told you someone else has plans for this room
    and your person said to go ahead anyway."""
    twin = get_twin(runtime.context.property_id)
    if not any(r.room_id == room_id for r in twin.rooms):
        return f"Error: no room with id {room_id}. Use an id from the room directory."
    if refusal := _one_room_at_a_time(runtime, room_id, brief) or _someone_elses_room(runtime, room_id, go_ahead_over_other_plans):
        return refusal
    house_plans.note(runtime.context, room_id, house_plans.ARRANGING)
    conversation = design_gate.key(runtime.context.session_id, runtime.context.user_id)
    design_gate.started(conversation, room_id)
    _briefs[(conversation, room_id)] = brief
    runtime.stream_writer({"type": BACKGROUND_DESIGN, "payload": {"room_id": room_id, "brief": brief}})
    return ("The designer has started on this room. If the user asked for more rooms, start those now too, in this same "
            "turn: they are all arranged at once and shown one at a time. Then, in at most two short sentences, say what "
            "you are going for, and ask your one question if something that matters is unknown. Do not wait, and do not "
            "describe any layout yet.")


@tool
def propose_layouts(room_id: str, item_ids: list[str], wishes: list[Constraint], idea: str, runtime: Runtime, n: int = 3) -> str:
    """Arrange the user's furniture in a room. Returns up to n saved layouts, each with a layout_id.

    You say what should be true; the solver finds positions. It always keeps pieces inside the
    room, apart from each other, clear of doors and windows, and leaves a path to walk.

    item_ids: the numbers of the pieces to place ("4821"), from the house register.
    idea: a few words naming the arrangement, e.g. "Conversation corner by the window".
    wishes: each is {kind, item, target, weight}. `item` is a piece number. Kinds and their targets:
      against_wall  target "any" | "longest" | "window" (a wall with a window) | "solid" (without)
                    | "north" | "east" | "south" | "west" (the wall on that side of the room)
      faces         target a piece number | "window" | "door" | "center"
      near          target a piece number
      in_front_of   target a piece number   (e.g. coffee table in front of the sofa)
      beside        target a piece number   (e.g. side table beside the armchair)
      away_from     target "door" | "window"
    weight (default 1) says how much a wish matters; use 3 for something the user insisted on.
    """
    ctx = runtime.context
    twin = get_twin(ctx.property_id)
    room = next((r for r in twin.rooms if r.room_id == room_id), None)
    if room is None:
        return f"Error: unknown room_id {room_id!r}. Use a room_id from the room directory."
    # The designer names pieces by number; the solver and the database know them by item_id.
    found = {ref: _piece(ctx, ref) for ref in dict.fromkeys(item_ids)}
    unknown = [ref for ref, item in found.items() if item is None]
    if unknown or not item_ids:
        return f"Error: unknown pieces {unknown}. Use the piece numbers in the house register."
    real = {ref: str(item.item_id) for ref, item in found.items()}
    owned = {str(item.item_id): item for item in found.values()}
    bad = sorted({w.item for w in wishes if w.item not in real and w.item not in owned})
    if bad:
        return f"Error: wishes mention pieces that are not in item_ids: {bad}."

    def target(value: str) -> str:
        """A wish's target as the solver knows it: a piece's item_id, or a word such as "window"."""
        if value in real:
            return real[value]
        if value.lstrip("#").isdigit() or len(value) >= 32:
            other = _piece(ctx, value)
            return str(other.item_id) if other else value
        return value

    wishes = [w.model_copy(update={"item": real.get(w.item, w.item), "target": target(w.target)}) for w in wishes]
    items = [layout_solver.Item(k, v.label, v.item_class, v.dims_m.w, v.dims_m.d, v.dims_m.h) for k, v in owned.items()]
    floor = views.floor_height(twin, room_id)
    on_real_floor = room.model_copy(update={"floor_y": floor})  # window sills are measured from here
    result = layout_solver.solve(
        on_real_floor, twin.openings, items, wishes, n=max(1, min(n, 4)), walls=twin.walls, sweeps=twin.sweeps, compass=compass_axes(twin)
    )
    name = prompts.room_display_names(twin)[room_id]
    if not result.variants:
        return f"No layout for {name}: {result.reason}. Try fewer or smaller pieces, or another room."

    lines = [f"{len(result.variants)} layout(s) saved for {name}:"]
    shown: list[str] = []
    for k, v in enumerate(result.variants, start=1):
        v.poses = seat_around_table(on_real_floor, twin, items, v.poses)
        left = [i.label for i in items if i.item_id not in v.poses]
        if left:
            v.unmet = [*v.unmet, f"no room at the table for {len(left)} more chair(s)"]
        where = _where(twin, [i for i in items if i.item_id in v.poses], v.poses)
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
        shown.append(str(layout.layout_id))
        lines.append(
            f"- layout_id {_short(layout.layout_id)} · score {v.score:.0f}/100 · where: {where} · "
            f"met: {'; '.join(v.met) or 'none'} · not met: {'; '.join(v.unmet) or 'none'}"
        )
    if result.reason:
        lines.append(f"Note: {result.reason}.")
    # The user sees these right away, before the designer is done; the guide shows the final set.
    runtime.stream_writer({"type": "design.showVariants", "payload": {"layout_ids": shown}})
    runtime.stream_writer({"type": "layout.select", "payload": {"layout_id": shown[0]}})
    lines.append("These are on the user's screen already.")
    return "\n".join(lines)


def seat_around_table(room, twin, items: list, poses: dict) -> dict:
    """Dining chairs belong at the table: tucked in along its two long sides, then its ends, each
    facing it. The solver only knows "near" and "faces", which leaves chairs scattered. Returns
    the poses with the chairs seated. The table moves if it must (the middle of the room first);
    if not every chair fits anywhere, as many as do are seated and the rest are left out of the
    layout. The poses come back as they were only when not even two chairs fit."""
    from shapely.geometry import Polygon

    Pose = layout_solver.Pose
    table = next((i for i in items if i.item_class == "dining_table" and i.item_id in poses), None)
    chairs = [i for i in items if i.item_class == "chair" and i.item_id in poses]
    if table is None or not chairs:
        return poses

    def seated(at, count: int) -> dict:
        (fx, fz), (rx, rz) = at.front, at.right
        chair = chairs[0]
        per_side = max(1, int(table.w // (chair.w + 0.08)))
        gap = layout_solver.GAP_M + 0.01  # pulled up to the table: pieces may not touch
        out = table.d / 2 + chair.d / 2 + gap
        end = table.w / 2 + chair.d / 2 + gap
        seats = []  # (x, z, yaw) with the chair's front toward the table
        for k in range(per_side):
            along = (k - (per_side - 1) / 2) * (table.w / per_side)
            seats.append((at.x + fx * out + rx * along, at.z + fz * out + rz * along, at.yaw + 180))
            seats.append((at.x - fx * out + rx * along, at.z - fz * out + rz * along, at.yaw))
        seats.append((at.x + rx * end, at.z + rz * end, at.yaw - 90))
        seats.append((at.x - rx * end, at.z - rz * end, at.yaw + 90))
        new = {k: v for k, v in poses.items() if k not in {c.item_id for c in chairs}} | {table.item_id: at}
        for c, (x, z, yaw) in zip(chairs[:count], seats):
            new[c.item_id] = Pose(x, z, yaw % 360)
        return new

    here = poses[table.item_id]
    outline = Polygon(room.polygon)
    middle = outline.centroid
    minx, minz, maxx, maxz = outline.bounds
    # Where the solver put the table, then spots across the room, the middle first.
    spots = [(minx + 0.3 * i, minz + 0.3 * j) for i in range(int((maxx - minx) / 0.3) + 1) for j in range(int((maxz - minz) / 0.3) + 1)]
    spots.sort(key=lambda p: math.dist(p, (middle.x, middle.y)))
    tries = [here] + [Pose(x, z, yaw) for x, z in spots for yaw in (here.yaw, (here.yaw + 90) % 360)]
    for count in sorted({len(chairs), min(len(chairs), 4), min(len(chairs), 2)}, reverse=True):
        for at in tries:
            new = seated(at, count)
            placed = [i for i in items if i.item_id in new]
            if not layout_solver.violations(room, twin.openings, placed, new, walls=twin.walls, sweeps=twin.sweeps):
                return new
    return poses


SPIN_MS, SPIN_DELAY_MS = 10_000, 2_500


def _place_in_view(runtime: Runtime, layout: LayoutVariant) -> None:
    """Put one layout's furniture in the room, in whatever view the user is in, and take them to
    that room. The user stays where they are: no tab switch."""
    twin = get_twin(runtime.context.property_id)
    runtime.stream_writer({"type": "layout.select", "payload": {"layout_id": str(layout.layout_id)}})
    payload: dict = {"room_id": layout.room_id}
    if view := views.best_view(twin, layout.room_id):
        payload |= {"at_sweep_id": view.sweep_id, "look_at": view.look_at.model_dump()}
    runtime.stream_writer({"type": "viewer.goto", "payload": payload})
    # Once slowly around, after the walk there, so they see the whole arrangement before deciding.
    runtime.stream_writer({"type": "viewer.look", "payload": {"yaw_delta_deg": 360.0, "pitch_delta_deg": 0.0, "ms": SPIN_MS, "delay_ms": SPIN_DELAY_MS}})


@tool
def design_show_variants(layout_ids: list[str], runtime: Runtime) -> str:
    """Show the designer's layouts to the user. The first one is placed in the room right away, in
    the view the user is already in; all of them become cards the user can flip through.
    layout_ids come from the designer's answer, best first."""
    layouts = [_layout(runtime.context, i) for i in layout_ids]
    missing = [i for i, found in zip(layout_ids, layouts) if found is None]
    if missing or not layout_ids:
        return f"Error: unknown layout_ids {missing}. Use the layout_ids the designer returned."
    layout_ids = [str(l.layout_id) for l in layouts]
    runtime.stream_writer({"type": "design.showVariants", "payload": {"layout_ids": layout_ids}})
    _place_in_view(runtime, layouts[0])
    return (
        f"Option 1 is now placed in the room, and the view is turning once slowly around the room so the user sees all of it. "
        f"All {len(layout_ids)} option(s) are in the chat as small plans they can click to try, with a Keep button to settle "
        "on one. Say in one or two sentences what option 1 does, then ask one question: keep it, see another, or change "
        "something? Then stop and wait for their answer: do not start another room. "
        "Nothing is decided until they press Keep: don't record a choice in memory before the layout_chosen event."
    )


@tool
def design_apply_layout(layout_id: str, runtime: Runtime) -> str:
    """Show a different saved layout in the room, e.g. when the user says "show me option 2".
    It is a preview: the house keeps its furniture until the user presses Keep."""
    layout = _layout(runtime.context, layout_id)
    if layout is None:
        return f"Error: unknown layout_id {layout_id!r}. Use a layout_id the designer returned."
    _place_in_view(runtime, layout)
    return f"That layout is now shown in the room, and the view is turning once around it: {layout.rationale}"


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


_compass = ledger.compass_word  # compass word for a direction in the interior frame


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
    return f"layout_id {_short(layout.layout_id)} in {room}: {layout.rationale}"
