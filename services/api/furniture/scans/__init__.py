"""Rooms scanned by hand: a photo of someone's own room, with every thing in it listed and
modelled in 3D (my_room.py). They work like a room set up from a photo (furniture/own_room.py),
without the GPU pipeline: pick the photo, and a room of the house is set up with everything in
it, or with the things picked on the photo; or put one thing in a room, or in storage.

Each thing becomes the person's own piece the first time it is used (`item_for`), and the same
piece is used again while it is not standing in the house.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from functools import lru_cache
from types import ModuleType

from shapely.geometry import LineString, Point, Polygon

from packages.schema.inventory import InventoryItem, ItemAttributes
from services.api.furniture import house
from services.api.furniture.house import FurnitureError
from services.api.furniture.own_room import Outcome, _arrange, _on_top, _wall_spot
from services.api.furniture.scans import my_room
from services.api.furniture.scans.types import Thing
from services.api.furniture.store import PHOTO, furniture_store
from services.api.objects import files
from services.api.objects.pipeline import SceneThing
from services.api.twin.service import get_twin

SCANS: dict[str, ModuleType] = {"my-room": my_room}


@dataclass(frozen=True)
class Scan:
    scan_id: str
    module: ModuleType

    @property
    def things(self) -> list[Thing]:
        return self.module.THINGS

    def thing(self, key: str) -> Thing | None:
        return next((t for t in self.things if t.key == key), None)


def get(scan_id: str) -> Scan | None:
    module = SCANS.get(scan_id)
    return Scan(scan_id, module) if module else None


def all_scans() -> list[Scan]:
    return [Scan(k, m) for k, m in SCANS.items()]


@lru_cache(maxsize=128)
def model(scan_id: str, key: str) -> tuple[bytes, tuple[float, float, float]]:
    """A thing's model as GLB, and its (w, d, h) in metres. Built once per process."""
    scan = get(scan_id)
    thing = scan.thing(key) if scan else None
    if thing is None:
        raise KeyError(f"{scan_id}/{key}")
    return thing.build().glb()


def product_url(scan_id: str, key: str) -> str:
    """What a piece made from a scanned thing is known by."""
    return f"scan://{scan_id}/{key}"


def _cutout_url(scan: Scan, thing: Thing) -> str:
    """The thing as it is in the photo, a little room around it: its picture in the inventory."""
    rel = f"items/scan-{scan.scan_id}-{thing.key}.jpg"
    if files.find(rel) is None:
        from services.api.furniture.scans.textures import photo

        image = photo(str(scan.module.PHOTO))
        x0, y0, x1, y1 = thing.box_px
        pad = max(x1 - x0, y1 - y0) * 0.12
        crop = image.crop((max(0, x0 - pad), max(0, y0 - pad), min(image.width, x1 + pad), min(image.height, y1 + pad)))
        crop.thumbnail((640, 640))
        out = io.BytesIO()
        crop.save(out, "JPEG", quality=86)
        return files.save(rel, out.getvalue())
    return f"/api/files/{rel}"


def item_for(scan: Scan, thing: Thing, person: str, house_user_id: str | None = None, fresh: bool = False,
             skip: set[str] = frozenset(), here: set[str] = frozenset()) -> InventoryItem:
    """The person's piece for this thing: one they have that is not standing in the house (or
    stands among `here`, the room being set up), else a new one. `skip`: pieces already used.
    `fresh`: always a new one."""
    from services.api.objects import pipeline

    store = furniture_store()
    url = product_url(scan.scan_id, thing.key)
    if not fresh:
        for item in store.list_items(person):
            key = str(item.item_id)
            if item.product_url != url or key in skip:
                continue
            if house_user_id is None or key in here or not store.stands_in_house(house_user_id, key):
                return item
    glb, dims = model(scan.scan_id, thing.key)
    item = pipeline.import_glb(glb, thing.label, thing.item_class, dims, person, store, source_url=_cutout_url(scan, thing), product_url=url)
    look = ItemAttributes(color=thing.color, material=thing.material)
    return store.put_item(item.model_copy(update={"attributes": look}))


# --- a room of the house, set up with the scan ----------------------------------------------------


def _window_spot(room, twin, item: InventoryItem, how: str, used: list[int]) -> tuple[float, float, float] | None:
    """Where a curtain hangs: (x, z, yaw) facing into the room, over a window of the room
    (`cover`) or just beside one (`beside`). The widest windows first; a window already dressed is
    passed over while there is another."""
    outline = Polygon(room.polygon)
    windows = []
    for k, o in enumerate(twin.openings):
        if o.kind != "window" or room.room_id not in (o.room_id, o.other_room_id) or len(o.polygon) < 2:
            continue
        pts = [(p.x, p.z) for p in o.polygon]
        a, b = max(((p, q) for p in pts for q in pts), key=lambda pq: math.dist(*pq))
        if math.dist(a, b) > 0.3:
            windows.append((k, a, b))
    if not windows:
        return None
    windows.sort(key=lambda w: (w[0] in used, -math.dist(w[1], w[2])))
    k, a, b = windows[0]
    used.append(k)
    width = math.dist(a, b)
    ux, uz = (b[0] - a[0]) / width, (b[1] - a[1]) / width
    nx, nz = -uz, ux
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    if not outline.buffer(0.05).contains(Point(mid[0] + nx * 0.25, mid[1] + nz * 0.25)):
        nx, nz = -nx, -nz
    along = 0.0
    if how == "beside":
        along = width / 2 + item.dims_m.w / 2 + 0.05
        # The side with more wall.
        edge = LineString(list(outline.exterior.coords))
        right = Point(mid[0] + ux * (along + item.dims_m.w / 2), mid[1] + uz * (along + item.dims_m.w / 2))
        left = Point(mid[0] - ux * (along + item.dims_m.w / 2), mid[1] - uz * (along + item.dims_m.w / 2))
        if edge.distance(left) < edge.distance(right):
            along = -along
    out = item.dims_m.d / 2 + 0.06
    x, z = mid[0] + ux * along + nx * out, mid[1] + uz * along + nz * out
    if not outline.contains(Point(x, z)):
        return None
    return x, z, math.degrees(math.atan2(nx, nz))


def furnish(scan: Scan, house_user_id: str, property_id: str, room_id: str, by: str, keys: list[str] | None = None,
            progress=lambda pct, note: None) -> Outcome:
    """Set up a room of the house with the scan: everything in it, or the things `keys` names.
    What stood in the room before goes back to storage."""
    twin = get_twin(property_id)
    room = house.room_by_id(twin, room_id)
    if room is None:
        raise FurnitureError(404, "no such room")
    from services.api.agent import prompts

    chosen = [t for t in scan.things if keys is None or t.key in keys]
    outcome = Outcome(room_id=room_id, room_name=prompts.room_short_names(twin).get(room_id, room.label))
    if not chosen:
        return outcome

    items: dict[str, InventoryItem] = {}
    taken: set[str] = set()
    here = {str(p.item_id) for p in furniture_store().house_pieces(house_user_id, property_id, room_id)}
    for n, t in enumerate(chosen):
        progress(5 + round(70 * n / len(chosen)), f"Building the {t.label.lower()}")
        item = item_for(scan, t, by, house_user_id, skip=taken, here=here)
        taken.add(str(item.item_id))
        items[t.key] = item
    ident = lambda key: str(items[key].item_id) if key in items else None  # noqa: E731

    progress(80, "Arranging the room")
    floor = [
        SceneThing(item=items[t.key], beside=ident(t.beside) if t.beside else None)
        for t in chosen if not t.on_wall and not t.rests_on
    ]
    poses = _arrange(twin, room, floor)
    rows = []
    for st in floor:
        key = str(st.item.item_id)
        if key in poses:
            p = poses[key]
            rows.append(house._piece(twin, property_id, key, room, p.x, p.z, p.yaw, by))
            outcome.placed.append(st.item)
        else:
            outcome.left.append(f"{st.item.label}: no place for it could be found in this room")

    # What stood on a piece goes on that piece.
    tops: dict[str, list[tuple[float, float, float]]] = {}
    for t in chosen:
        if not t.rests_on:
            continue
        under_id = ident(t.rests_on)
        if under_id is None or under_id not in poses:
            outcome.left.append(f"{t.label}: {scan.thing(t.rests_on).label.lower() if scan.thing(t.rests_on) else 'what it stood on'} is not in the room")
            continue
        st = SceneThing(item=items[t.key], rests_on=under_id, offset_m=t.offset, turn_deg=t.turn)
        under = items[t.rests_on]
        x, z, yaw = _on_top(st, under, poses[under_id], tops.setdefault(under_id, []))
        rows.append(house._piece(twin, property_id, str(st.item.item_id), room, x, z, yaw, by, under_id, under.dims_m.h))
        outcome.placed.append(st.item)

    # What hung on the wall: over the piece it hung over, at the height it hung. Curtains at the windows.
    progress(90, "Hanging the pictures and curtains")
    dressed: list[int] = []
    for t in chosen:
        if not t.on_wall:
            continue
        item = items[t.key]
        spot = _window_spot(room, twin, item, t.window, dressed) if t.window else None
        near_id = ident(t.anchor) if t.anchor else None
        near = poses.get(near_id) if near_id else None
        if spot is None:
            st = SceneThing(item=item, on_wall=True, anchor=near_id, offset_m=(0.0, t.along), hang_m=t.hang)
            spot = _wall_spot(room, twin, st, near, items[t.anchor] if near is not None else None, [])
        if spot is None:
            outcome.left.append(f"{t.label}: no free stretch of wall for it")
            continue
        if t.window:
            lift = max(room.height_m - item.dims_m.h - 0.06, 0.0)
        else:
            lift = t.hang if t.hang is not None else 1.4
            lift = min(lift, max(room.height_m - item.dims_m.h - 0.05, 0.3))
        rows.append(house._piece(twin, property_id, str(item.item_id), room, spot[0], spot[1], spot[2], by, None, lift))
        outcome.placed.append(item)

    if rows:
        furniture_store().set_room(house_user_id, property_id, room_id, rows, by, PHOTO)
    return outcome


def put_one(scan: Scan, thing: Thing, house_user_id: str, property_id: str, room_id: str, by: str):
    """Put one thing in a room, beside what is there: on the piece it stood on when that piece is
    in the room, else where its kind goes (a picture over the main piece, a rug under the bed)."""
    from packages.schema.layout import PlacePiece

    twin = get_twin(property_id)
    room = house.room_by_id(twin, room_id)
    if room is None:
        raise FurnitureError(404, "no such room")
    store = furniture_store()
    item = item_for(scan, thing, by, house_user_id)
    near = None
    for other_key in (thing.rests_on, thing.anchor, thing.beside):
        if not other_key:
            continue
        url = product_url(scan.scan_id, other_key)
        for p in store.house_pieces(house_user_id, property_id, room_id):
            other = store.get_item(str(p.item_id))
            if other is not None and other.product_url == url:
                near = str(p.item_id)
                break
        if near:
            break
    cx, cz = Polygon(room.polygon).representative_point().coords[0]
    body = PlacePiece(x=cx, z=cz, room_id=room_id, floor_id=room.floor_id, snap=True, auto=True, near_item_id=near)
    return item, house.place(house_user_id, property_id, str(item.item_id), body, by)

