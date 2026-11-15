"""Furnishing a room from the library: choose the pieces, add them, have them arranged. Owner: B.

Two ways in, one result (proposals for the room, as after any layout):

- the furniture menu's "Furnish this room" (POST /properties/{id}/furniture/design): `furnish`
  chooses, adds and arranges in one go, with the wishes any designer would have;
- the guide's `furnish_room` tool: `gather` chooses and adds, and the interior designer agent
  then arranges the pieces with wishes of its own (`brief` is what it is told).

Pieces are the person's own once added (F1). Pieces of an earlier try that stand in no room are
used again, so furnishing a room twice does not fill the storage with doubles.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from packages.schema.inventory import InventoryItem
from packages.schema.layout import LayoutVariant
from services.api.agent import prompts
from services.api.furniture import arrange, dress, house
from services.api.furniture.store import furniture_store
from services.api.library import compose, sources
from services.api.twin.service import get_twin

Progress = Callable[[float, str], None]


@dataclass
class Furnished:
    """What came of furnishing a room: the pieces meant for it, and (after `furnish`) the layouts."""

    room_id: str
    room_name: str
    look: compose.Look
    level: str
    items: list[InventoryItem] = field(default_factory=list)  # everything meant for the room, the main pieces first
    roles: dict[str, str] = field(default_factory=dict)  # item_id -> what it is for ("picture over the sofa")
    goes_with: dict[str, str] = field(default_factory=dict)  # item_id -> item_id of the piece it goes on, over, under or beside
    new: int = 0  # how many were added to the person's furniture just now
    skipped: list[str] = field(default_factory=list)  # roles the library had nothing for
    failed: list[str] = field(default_factory=list)  # "name: why it could not be added"
    note: str | None = None  # why nothing was chosen
    layouts: list[LayoutVariant] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)  # "label: why it has no place" in the best layout

    def summary(self) -> str:
        """For the guide: what was done, in a few lines."""
        if not self.items:
            return f"Nothing was chosen for {self.room_name}: {self.note or 'the library had nothing that fits'}."
        text = f"{self.room_name} (room_id {self.room_id}) was furnished in the look \"{self.look.name}\" ({self.look.blurb}) with {len(self.items)} pieces"
        text += ": " + ", ".join(f"#{i.tag} {i.label}" for i in self.items[:8]) + (f" and {len(self.items) - 8} more" if len(self.items) > 8 else "") + "."
        if self.layouts:
            text += f"\n{len(self.layouts)} layout(s) are on the user's screen already:\n" + "\n".join(self.lines)
        if self.left:
            text += "\nNo place was found for: " + "; ".join(self.left) + "."
        if self.failed:
            text += "\nCould not be added: " + "; ".join(self.failed) + "."
        return text


def gather(
    person_id: str, house_user_id: str, property_id: str, room_id: str, look: str | None = None, level: str = "full", use: str | None = None,
    keep_item_ids: tuple[str, ...] | list[str] = (), avoid: tuple[str, ...] | list[str] = (), progress: Progress = lambda pct, note: None,
) -> Furnished:
    """Choose the pieces for a room in a look and put them into the person's furniture.
    Raises KeyError for an unknown room. `keep_item_ids`: the family's own pieces the room is
    planned round. Nothing is arranged yet."""
    twin = get_twin(property_id)
    room = house.room_by_id(twin, room_id)
    if room is None:
        raise KeyError(room_id)
    store = furniture_store()
    the_look = compose.look_for(look)
    out = Furnished(room_id, prompts.room_short_names(twin).get(room_id, room.label), the_look, level)
    keep = [i for ref in keep_item_ids if (i := house.pool_item(house_user_id, ref)) is not None]
    progress(4, f"Choosing pieces for {out.room_name}")
    plan = compose.plan(twin, room, the_look, level, use, keep, tuple(avoid), seed=time.time_ns() % 1_000_000)
    out.skipped, out.note = plan.skipped, plan.note
    if not plan.chosen:
        return out

    # The person's own pieces from an earlier try that stand in no room are used again.
    standing = {str(p.item_id) for p in store.house_pieces(house_user_id, property_id)}
    spare: dict[str, list[InventoryItem]] = {}
    for item in store.list_items(person_id):
        if str(item.item_id) not in standing and item.source_image_url:
            spare.setdefault(item.source_image_url, []).append(item)
    wanted: list[tuple[compose.Chosen, InventoryItem | None]] = []  # one line per single piece, in plan order
    to_add: list[int] = []
    for chosen in plan.chosen:
        for _ in range(chosen.count):
            if chosen.own is not None:
                have = chosen.own
            else:
                again = spare.get(chosen.piece.img, [])
                have = again.pop(0) if again else None
            if have is None:
                to_add.append(len(wanted))
            wanted.append((chosen, have))
    if to_add:
        total = len(to_add)
        progress(10, f"Fetching {total} 3D model{'s' if total != 1 else ''} from the library")
        added, out.failed = sources.add_many(
            [wanted[n][0].piece for n in to_add], person_id, store,
            lambda done, of, piece: progress(10 + 60 * done / of, f"Fetched {done} of {of}: {piece.name}"),
        )
        for n, item in zip(to_add, added):
            wanted[n] = (wanted[n][0], item)
        out.new = sum(1 for item in added if item is not None)
    first_of: dict[str, str] = {}
    for chosen, item in wanted:
        if item is None:
            continue
        out.items.append(item)
        out.roles[str(item.item_id)] = chosen.role
        first_of.setdefault(chosen.role, str(item.item_id))
    for chosen, item in wanted:
        if item is not None and chosen.with_role in first_of and first_of[chosen.with_role] != str(item.item_id):
            out.goes_with[str(item.item_id)] = first_of[chosen.with_role]
    return out


def furnish(
    person_id: str, house_user_id: str, property_id: str, room_id: str, look: str | None = None, level: str = "full", use: str | None = None,
    keep_item_ids: tuple[str, ...] | list[str] = (), avoid: tuple[str, ...] | list[str] = (), progress: Progress = lambda pct, note: None, n: int = 3,
) -> Furnished:
    """Choose, add and arrange: the room's proposals are saved when this returns."""
    out = gather(person_id, house_user_id, property_id, room_id, look, level, use, keep_item_ids, avoid, progress)
    if not out.items:
        return out
    progress(74, f"Arranging {out.room_name}")
    result = arrange.arrange(house_user_id, property_id, room_id, out.items, [], out.look.name, n, goes_with=out.goes_with)
    out.layouts, out.lines, out.left = result.layouts, result.lines, result.left
    if not result.layouts:
        out.note = result.note or "no arrangement fits this room"
    return out


def brief(f: Furnished, words: str = "") -> str:
    """What the interior designer agent is told: the look, and every piece with what it is for."""
    floor = [i for i in f.items if dress.kind_of(i) == "solve"]
    rest = [i for i in f.items if dress.kind_of(i) != "solve"]
    by_id = {str(i.item_id): i for i in f.items}

    def line(i: InventoryItem) -> str:
        role = f.roles.get(str(i.item_id), i.item_class)
        other = by_id.get(f.goes_with.get(str(i.item_id), ""))
        return f"- #{i.tag} {i.label} ({i.item_class}) · {role}" + (f" · goes with #{other.tag} {other.label}" if other else "")

    parts = [
        f"Furnish this room in the look \"{f.look.name}\": {f.look.blurb}",
        "These pieces were chosen from the furniture library to go together, and are in the family's furniture now. Use every one of "
        "them in one propose_layouts call (all the numbers in item_ids).",
        "Floor furniture, yours to arrange with wishes:",
        *(line(i) for i in floor),
    ]
    if rest:
        parts += [
            "The rest is put in for you around the furniture (a rug under the group, lamps on the tables, pictures over the main "
            "pieces, plants in the corners). Give one a wish of kind on, above or under only where you want other than that:",
            *(line(i) for i in rest),
        ]
    if words.strip():
        parts.append(f"From the guide: {words.strip()}")
    return "\n".join(parts)
