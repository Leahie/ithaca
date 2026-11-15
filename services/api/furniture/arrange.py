"""Arranging a whole room, saved as proposals (docs/specs/furniture.md, F8). Owner: B.

One call takes everything meant for a room and returns up to three ways to arrange it:

1. the floor furniture goes to the layout solver (sim/layout_solver.py) with the wishes it was
   given; a piece nobody wished anything for gets the wishes any designer would have (the bed on
   a solid wall, the coffee table before the sofa, the chair at the desk), so it never ends up
   somewhere arbitrary;
2. dining chairs are seated round their table;
3. everything else (rugs, lamps, plants, pictures, mirrors, decorations, ceiling lights) is put
   in around that furniture by furniture/dress.py.

The designer agent's tool (`propose_layouts`) and the furniture menu's "Furnish this room" both
come through here, so a room laid out by either looks the same.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

from packages.schema.inventory import InventoryItem
from packages.schema.layout import LayoutVariant, Placement
from packages.schema.twin import Vec3
from services.api.agent import views
from services.api.furniture import dress, house, ledger
from services.api.furniture.store import furniture_store
from services.api.sim import layout_solver
from services.api.sim.layout_solver import Constraint, Pose
from services.api.twin.service import get_twin

# Wishes about a thing that is not floor furniture: which piece it goes on, over or under.
DRESS_KINDS = ("on", "above", "under")
WHERE_SHOWN = 4  # pieces named in an option's one-line description: the largest ones
MAX_DROPPED = 5  # floor pieces left out, one after another, before a room is given up as too small


@dataclass
class Arranged:
    """What came of arranging a room."""

    layouts: list[LayoutVariant] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # one per layout, for the agent that asked
    note: str | None = None  # why there are fewer options than asked, or none
    left: list[str] = field(default_factory=list)  # "label: why" for what has no place in the best option


def compass_axes(twin) -> dict[str, tuple[float, float]]:
    """North, east, south and west as unit (x, z) directions in the interior frame."""
    m = twin.interior_to_world  # (east, north) = [[m0, m8], [m1, m9]] @ (dx, dz)
    det = m[0] * m[9] - m[8] * m[1]
    north, east = (-m[8] / det, m[0] / det), (m[9] / det, -m[1] / det)
    unit = lambda v: (v[0] / math.hypot(*v), v[1] / math.hypot(*v))  # noqa: E731
    north, east = unit(north), unit(east)
    return {"north": north, "east": east, "south": (-north[0], -north[1]), "west": (-east[0], -east[1])}


def where_words(twin, items: list[layout_solver.Item], poses: dict[str, Pose]) -> str:
    """Plain words for where the largest pieces ended up, so the options can be told apart."""
    parts = []
    for item in sorted(items, key=lambda i: -(i.w * i.d))[:WHERE_SHOWN]:
        fx, fz = poses[item.item_id].front
        parts.append(f"{item.label} on the {ledger.compass_word(twin, -fx, -fz)} side, facing {ledger.compass_word(twin, fx, fz)}")
    return "; ".join(parts)


def auto_wishes(items: list[InventoryItem], given: list[Constraint], goes_with: dict[str, str] | None = None) -> list[Constraint]:
    """What a designer would ask of the floor pieces nobody wished anything for. `goes_with`:
    item_id -> item_id of the piece it was chosen for (a side table for that bed, not for the sofa)."""
    spoken = {c.item for c in given}
    here = {str(i.item_id) for i in items}
    chosen_for = {k: v for k, v in (goes_with or {}).items() if v in here}
    by_class: dict[str, list[InventoryItem]] = {}
    for i in sorted(items, key=lambda i: -(i.dims_m.w * i.dims_m.d)):
        by_class.setdefault(i.item_class, []).append(i)
    first = lambda *classes: next((str(i.item_id) for c in classes for i in by_class.get(c, [])), None)  # noqa: E731
    seat, coffee, bed, desk = first("sectional", "sofa", "loveseat"), first("coffee_table"), first("bed", "crib"), first("desk")
    dining, tv = first("dining_table"), first("tv_stand")
    armchairs = [str(i.item_id) for i in by_class.get("armchair", [])]
    beside_these = [t for t in (seat, *armchairs) if t]  # side tables go round: the sofa first, then each armchair
    wish = lambda kind, item, target, weight: Constraint(kind=kind, item=item, target=target, weight=weight)  # noqa: E731
    out: list[Constraint] = []
    side_tables = desk_chairs = 0
    for i in items:
        me, cls = str(i.item_id), i.item_class
        if me in spoken:
            continue
        if cls in ("bed", "crib"):
            out += [wish("against_wall", me, "solid", 3), wish("away_from", me, "door", 1)]
        elif me == seat:
            out.append(wish("against_wall", me, "solid", 3))
            if tv:
                out.append(wish("faces", me, tv, 2))
        elif cls in ("sofa", "sectional", "loveseat"):  # a second sofa: with the first, round the table
            out.append(wish("against_wall", me, "solid", 2))
            if coffee or seat:
                out.append(wish("near", me, coffee or seat, 2))
        elif cls == "coffee_table" and seat:
            out.append(wish("in_front_of", me, seat, 3))
        elif cls == "armchair":
            if coffee:
                out += [wish("near", me, coffee, 2), wish("faces", me, coffee, 2)]
            elif seat:
                out += [wish("near", me, seat, 2), wish("faces", me, seat, 1)]
            else:
                out.append(wish("against_wall", me, "any", 1))
        elif cls == "ottoman" and (armchairs or coffee):
            out.append(wish("in_front_of", me, armchairs[0], 1.5) if armchairs else wish("near", me, coffee, 1.5))
        elif cls == "side_table" and (me in chosen_for or beside_these or bed):
            out.append(wish("beside", me, chosen_for.get(me) or (beside_these[side_tables % len(beside_these)] if beside_these else bed), 2))
            side_tables += me not in chosen_for
        elif cls == "nightstand" and bed:
            out.append(wish("beside", me, bed, 2))
        elif cls == "bench" and bed:
            out.append(wish("in_front_of", me, bed, 2))
        elif cls in ("dresser", "wardrobe", "bookshelf", "cabinet", "console_table"):
            out.append(wish("against_wall", me, "solid", 2))
        elif cls == "tv_stand":
            out.append(wish("against_wall", me, "solid", 2))
            if seat:
                out.append(wish("faces", me, seat, 1))
        elif cls == "desk":
            out += [wish("against_wall", me, "solid", 2), wish("away_from", me, "door", 0.5)]
        elif cls in ("chair", "stool"):
            if dining:
                continue  # seated round the table (seat_around_table)
            if desk and not desk_chairs:
                out += [wish("in_front_of", me, desk, 3), wish("faces", me, desk, 2)]
                desk_chairs += 1
            else:
                out.append(wish("against_wall", me, "any", 1))
        elif cls == "dining_table":
            out.append(wish("faces", me, "window", 0.5))
        else:
            out.append(wish("against_wall", me, "any", 1))
    return out


def seat_around_table(room, twin, items: list, poses: dict) -> dict:
    """Dining chairs belong at the table: tucked in along its two long sides, then its ends, each
    facing it. The solver only knows "near" and "faces", which leaves chairs scattered. Returns
    the poses with the chairs seated. The table moves if it must (the middle of the room first);
    if not every chair fits anywhere, as many as do are seated and the rest are left out of the
    layout. The poses come back as they were only when not even two chairs fit."""
    from shapely.geometry import Polygon

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
    scene = layout_solver._scene(room, twin.openings, twin.walls, twin.sweeps)  # once: every try is tested against the same room
    for count in sorted({len(chairs), min(len(chairs), 6), min(len(chairs), 4), min(len(chairs), 2)}, reverse=True):
        for at in tries:
            new = seated(at, count)
            placed = [i for i in items if i.item_id in new]
            if not layout_solver._violations(scene, placed, new):
                return new
    return poses


def _finishing(things: list[InventoryItem], spots: dict) -> str:
    """ "a rug, 2 lamps, 3 pictures": what was put in around the furniture, for an option's description."""
    names = {"rug": "rug", "floor_lamp": "lamp", "table_lamp": "lamp", "ceiling_light": "ceiling light", "wall_art": "picture",
             "mirror": "mirror", "plant": "plant", "decor": "decoration"}
    counts: dict[str, int] = {}
    for t in things:
        if str(t.item_id) in spots:
            name = names.get(t.item_class, "accessory")
            counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{n} {name}s" if n > 1 else f"a {name}" for name, n in counts.items())


def arrange(
    house_user_id: str, property_id: str, room_id: str, items: list[InventoryItem], wishes: list[Constraint], idea: str, n: int = 3,
    goes_with: dict[str, str] | None = None,
) -> Arranged:
    """Arrange `items` in a room and save up to n proposals (best first). `wishes` name pieces by
    item_id. `goes_with`: item_id of a thing -> item_id of the piece it was chosen for (a lamp for
    that table, a picture for over that sofa); a wish of kind on, above or under says the same and
    wins. Raises KeyError for an unknown room."""
    twin = get_twin(property_id)
    room = house.room_by_id(twin, room_id)
    if room is None:
        raise KeyError(room_id)
    floor_y = views.floor_height(twin, room_id)
    solver_room = room.model_copy(update={"floor_y": floor_y})  # window sills are measured from the real floor
    furniture = [i for i in items if dress.kind_of(i) == "solve"]
    things = [i for i in items if dress.kind_of(i) != "solve"]
    ids = {str(i.item_id) for i in furniture}
    # Which piece a thing goes with: what it was chosen for, then any wish about it that names a
    # piece ("the floor lamp beside the armchair" says where a lamp goes as well as "on" does).
    targets = {**(goes_with or {}), **{c.item: c.target for c in wishes if c.item not in ids and c.target in ids}}
    asked = [c for c in wishes if c.kind not in DRESS_KINDS and c.item in ids]
    asked += auto_wishes(furniture, asked, goes_with)
    solver_items = [house._solver_item(i) for i in furniture]
    # Dining chairs are not the solver's to scatter: the table is solved as a table with its
    # chairs pulled out round it (a box that much larger), and the chairs are seated afterwards.
    # That keeps the room for them free, and eight chairs out of the search.
    table = next((i for i in solver_items if i.item_class == "dining_table"), None)
    diners = [i for i in solver_items if i.item_class == "chair"] if table else []
    to_solve = solver_items
    if diners:
        pulled = max(i.d for i in diners) + 0.12
        with_chairs = layout_solver.Item(table.item_id, table.label, table.item_class, table.w + 2 * pulled, table.d + 2 * pulled, table.h)
        to_solve = [with_chairs if i is table else i for i in solver_items if i not in diners]
        asked = [c for c in asked if c.item not in {i.item_id for i in diners}]

    out = Arranged()
    dropped: list[str] = []
    if solver_items:
        def solve(these: list, wishes_: list) -> layout_solver.SolveResult:
            return layout_solver.solve(
                solver_room, twin.openings, these, wishes_, n=max(1, min(n, 4)), walls=twin.walls, sweeps=twin.sweeps, compass=compass_axes(twin),
            )

        result = solve(to_solve, asked)
        # Too much for this room: the least important floor pieces (the last ones) are left out,
        # one at a time, until the rest fits. A room is never left empty for one piece too many.
        while not result.variants and len(to_solve) > 1 and len(dropped) < MAX_DROPPED:
            gone = to_solve[-1]
            to_solve = to_solve[:-1]
            dropped.append(gone.item_id)
            out.left.append(f"{gone.label}: no room for it on the floor with the rest")
            asked = [c for c in asked if gone.item_id not in (c.item, c.target)]
            result = solve(to_solve, asked)
        if not result.variants:
            out.note = result.reason
            return out
        out.note = result.reason
        solver_items = [i for i in solver_items if i.item_id not in dropped]
        variants = result.variants
    else:  # only things that hang, lie or stand on furniture: one option, round nothing
        variants = [layout_solver.Variant(poses={}, score=100.0, met=[], unmet=[])]

    by_id = {str(i.item_id): i for i in furniture}
    store = furniture_store()
    for k, v in enumerate(variants, start=1):
        poses = dict(v.poses)
        if diners:  # each chair starts at the table; seat_around_table gives it its seat
            poses |= {i.item_id: poses[table.item_id] for i in diners}
        poses = seat_around_table(solver_room, twin, solver_items, poses)
        if diners and any(poses[i.item_id] == poses[table.item_id] for i in diners if i.item_id in poses):
            poses = {k: p for k, p in poses.items() if k not in {i.item_id for i in diners}}  # not even two chairs fit: the table alone
        unmet = list(v.unmet)
        unseated = [i.label for i in solver_items if i.item_id not in poses]
        if unseated:
            unmet.append(f"no room at the table for {len(unseated)} more chair(s)")
        standing = {key: by_id[key] for key in poses}
        spots, left = dress.dress(twin, room, standing, poses, things, targets)
        if k == 1:
            out.left += left
        where = where_words(twin, [i for i in solver_items if i.item_id in poses], poses)
        rationale = f"{idea} (option {k})."
        if where:
            rationale += f" {where[0].upper()}{where[1:]}."
        if finishing := _finishing(things, spots):
            rationale += f" With {finishing}."
        if unmet:
            rationale += " Could not fully meet: " + "; ".join(unmet) + "."
        placements = [
            Placement(item_id=key, position=Vec3(x=round(p.x, 3), y=floor_y, z=round(p.z, 3)), yaw_deg=round(p.yaw, 1)) for key, p in poses.items()
        ] + [
            Placement(
                item_id=key, position=Vec3(x=round(s.x, 3), y=round(floor_y + s.lift, 3), z=round(s.z, 3)), yaw_deg=round(s.yaw % 360, 1),
                on_item_id=s.on_item_id, lift_m=round(s.lift, 3),
            )
            for key, s in spots.items()
        ]
        layout = LayoutVariant(
            layout_id=uuid.uuid4(), user_id=house_user_id, property_id=property_id, room_id=room_id, placements=placements,
            score=v.score, rationale=rationale.strip(), status="proposed",
        )
        store.put_proposal(layout)
        out.layouts.append(layout)
        out.lines.append(
            f"- layout_id {str(layout.layout_id)[:8]} · score {v.score:.0f}/100 · where: {where or 'no floor furniture'} · "
            f"met: {'; '.join(v.met) or 'none'} · not met: {'; '.join(unmet) or 'none'}"
            + (f" · around it: {finishing}" if finishing else "")
            + (f" · no place for: {'; '.join(left)}" if left else "")
        )
    return out
