"""Write artifacts/items.polyhaven.jsonl.gz: the home pieces among Poly Haven's models (https://polyhaven.com, CC0).

    python -m services.api.library.build.polyhaven            from the cached listing (downloaded once)
    python -m services.api.library.build.polyhaven --refresh  ask Poly Haven for its listing again

Poly Haven publishes about 520 models, most of them props for outdoor and industrial scenes.
The pieces that can stand, lie or hang in a home are named here one by one, each with its
furniture class: the collection is small, and its own categories do not say which way a piece
faces, whether it is one thing or a kit, or whether it was drawn at real size. Every piece
listed was fetched (library/polyhaven.py), measured, and drawn (catalog_build.picture) to see
which way it faces.

What that showed, and what the tables below record:
  - Size. The listing gives `dimensions` in millimetres in Blender's axes [x, y, z]. A fetched
    model is Y up, with w = x, d = y, h = z (the same to within 1 cm for all but the pieces in
    SIZE, whose fetched model is posed or turned differently from the listing: those state the
    model's own size).
  - Scale. Models are in metres. One (steel_frame_shelves_01) is drawn ten times too large; its
    listing is right, and stating the listing's size is all it takes (objects/normalize.py).
    Some are drawn too large by a third or so (a chair with its seat 67 cm up, a dining table
    96 cm high) and nothing says what the right size is: BOUNDS and TOO_LARGE leave those out.
  - Facing. Seats, beds, cabinets, shelves and desks all face +Z as fetched. A few models are
    turned a quarter (TURN): `turn` is in degrees about +Y, anticlockwise seen from above.
    library/sources.py turns the fetched model first and sizes it after, so `w` and `d` are the
    placed piece's: a piece turned by 90 or 270 states the model's own two sides swapped.

The listing is downloaded data: it stays under data/catalog/polyhaven_meta/ (not in git).
"""

from __future__ import annotations

import gzip
import io
import json
import re
import sys
from collections import Counter

import httpx

from services.api.integrations.config import REPO_ROOT
from services.api.library import ARTIFACTS, CATEGORIES
from services.api.library import polyhaven as source
from services.api.objects.classes import FURNITURE_CLASSES

META = REPO_ROOT / "data" / "catalog" / "polyhaven_meta"
LISTING = META / "assets.json"
OUT = ARTIFACTS / "items.polyhaven.jsonl.gz"

# furniture class -> the asset ids of that class. A piece is in the library only if it is here.
PIECES: dict[str, str] = {
    "sofa": "chinese_sofa painted_wooden_sofa sofa_02 sofa_03 vintage_day_bed",
    "loveseat": "Sofa_01",
    "armchair": "ArmChair_01 GreenChair_01 Rockingchair_01 chinese_armchair mid_century_lounge_chair modern_arm_chair_01",
    "chair": "SchoolChair_01 WoodenChair_01 dining_chair_02 gallinera_chair painted_wooden_chair_01 painted_wooden_chair_02 plastic_monobloc_chair_01",
    "stool": "bar_chair_round_01 chinese_stool folding_wooden_stool metal_stool_01 metal_stool_02 metal_stool_03 painted_wooden_stool wooden_stool_01 wooden_stool_02",
    "ottoman": "Ottoman_01",
    "bench": "painted_wooden_bench",
    "bed": "GothicBed_01 old_bed_frame",
    "coffee_table": "CoffeeTable_01 WoodenTable_01 chinese_tea_table coffee_table_round_01 gallinera_table gothic_coffee_table industrial_coffee_table "
                    "modern_coffee_table_01 modern_coffee_table_02 small_wooden_table_01",
    "side_table": "WoodenTable_02 side_table_01 side_table_tall_01",
    "nightstand": "ClassicNightstand_01 painted_wooden_nightstand",
    "console_table": "ClassicConsole_01 chinese_console_table",
    "desk": "SchoolDesk_01 metal_office_desk",
    "dining_table": "dining_table painted_wooden_table round_wooden_table_01 round_wooden_table_02 wooden_picnic_table wooden_table_02",
    "cabinet": "GothicCabinet_01 WoodenTable_03 chinese_cabinet chinese_commode modern_wooden_cabinet painted_wooden_cabinet painted_wooden_cabinet_02 "
               "vintage_cabinet_01 vintage_wooden_drawer_01",
    "dresser": "GothicCommode_01",
    "bookshelf": "Shelf_01 drawer_cabinet painted_wooden_shelves steel_frame_shelves_01 steel_frame_shelves_02 steel_frame_shelves_03 wooden_bookshelf_worn "
                 "wooden_display_shelves_01 worn_metal_rack",
    "ceiling_light": "Chandelier_01 Chandelier_02 Chandelier_03 brass_diya_lantern chinese_chandelier hanging_industrial_lamp lantern_chandelier_01 modern_ceiling_lamp_01",
    "table_lamp": "desk_lamp_arm_01 industrial_pipe_lamp vintage_oil_lamp",
    "plant": "planter_box_01 planter_box_02 planter_box_03 planter_pot_clay potted_plant_01 potted_plant_02 potted_plant_04",
    "mirror": "ornate_mirror_01",
    "wall_art": "dartboard fancy_picture_frame_01 fancy_picture_frame_02 hanging_picture_frame_01 hanging_picture_frame_02 hanging_picture_frame_03 "
                "vintage_telephone_wall_clock wall_clock",
    # Things that stand on a shelf or a table: vases, figures, clocks, baskets, books, tableware on show.
    "decor": "Lantern_01 Ukulele_01 alarm_clock_01 antique_ceramic_vase_01 book_encyclopedia_set_01 brass_candleholders brass_goblets brass_vase_01 brass_vase_02 "
             "brass_vase_03 brass_vase_04 bronze_ray_statue bronze_shark_statue bronze_whale_statue bull_head carved_wooden_elephant carved_wooden_plate "
             "ceramic_vase_01 ceramic_vase_02 ceramic_vase_03 ceramic_vase_04 chess_set concrete_cat_statue garden_gnome gothic_statue horse_head horse_statue_01 "
             "jug_01 lambis_shell lion_head mantel_clock_01 marble_bust_01 metal_jug standing_picture_frame_01 standing_picture_frame_02 sungka_board "
             "sungka_board_02 tea_set_01 throw_pillows_01 wicker_basket_01 wicker_basket_02 wooden_bowl_01 wooden_bowl_02 wooden_candlestick wooden_lantern_01",
}
CLASS_OF: dict[str, str] = {asset: cls for cls, assets in PIECES.items() for asset in assets.split()}

# Looked at and left out, with the reason. Everything else that is not in PIECES is a prop for
# another kind of scene (rocks, trees, tools, food, crates, machines, weapons, building parts).
LEFT_OUT: dict[str, str] = {
    "outdoor_table_chair_set_01": "a set of several pieces in one model",
    "modular_street_seating": "a set of several pieces in one model",
    "BarberShopChair_01": "a shop, workshop or street piece",
    "CoffeeCart_01": "a shop, workshop or street piece",
    "hand_truck": "a shop, workshop or street piece",
    "industrial_storage_cart": "a shop, workshop or street piece",
    "tool_cart": "a shop, workshop or street piece",
    "caged_hanging_light": "a shop, workshop or street piece",
    "mounted_fluorescent_lights": "a shop, workshop or street piece",
    "standing_chalkboard_01": "a shop, workshop or street piece",
    "chinese_screen_panels": "no furniture class fits it",  # a folding screen
    "vintage_grandfather_clock_01": "no furniture class fits it",  # a floor clock; its glTF also differs from its listing
    "treasure_chest": "no furniture class fits it",
    "wooden_crate_01": "no furniture class fits it",  # a trunk
    "katana_stand_01": "no furniture class fits it",
    "spinning_wheel_01": "no furniture class fits it",
    "decorative_book_set_01": "no glTF is published for it",
    "industrial_caged_sconce": "a wall light (the app has no class for one)",
    "industrial_wall_lamp": "a wall light (the app has no class for one)",
    "industrial_wall_sconce": "a wall light (the app has no class for one)",
}
# Where a new model would most likely be a home piece: one that is in neither table is reported.
HOME_CATEGORIES = ("Furniture/", "Lighting/Ceiling", "Lighting/Floor & Desk", "Decor & Art/", "Nature/Plants/Potted Plants")

# (w, d, h) in metres of the fetched model, before any turn, where it is not the listing's x, y, z.
SIZE: dict[str, tuple[float, float, float]] = {
    # Turned a quarter against the listing: what the listing calls x lies along the model's Z.
    "modern_coffee_table_01": (0.600, 1.202, 0.390),
    "wooden_display_shelves_01": (0.372, 1.078, 1.556),
    "standing_picture_frame_01": (0.100, 0.201, 0.250),
    "standing_picture_frame_02": (0.102, 0.217, 0.266),
    # Posed differently in the glTF, or the listing counts parts the glTF does not have.
    "desk_lamp_arm_01": (0.202, 0.614, 0.893),
    "throw_pillows_01": (0.940, 0.452, 0.450),
    "alarm_clock_01": (0.132, 0.067, 0.174),
    "mantel_clock_01": (0.342, 0.126, 0.160),
    "wicker_basket_02": (0.347, 0.254, 0.200),
    "WoodenTable_03": (1.327, 0.575, 0.830),
}

# Degrees about +Y (anticlockwise seen from above) that bring the fetched model's front to +Z.
TURN: dict[str, int] = {
    "wooden_display_shelves_01": 270,  # its open sides are at +X and -X
    "standing_picture_frame_01": 270,  # the picture is on the +X side, the stand on -X
    "standing_picture_frame_02": 270,
    "modern_coffee_table_01": 90,  # no front, but its long side lies along Z: a coffee table is placed long side to the sofa
    "bronze_shark_statue": 270,  # shown from its side, not nose on
    "sungka_board": 90,  # lies along Z: turned, it fits on a shelf
}

# Sized and facing right, and still not for the designer to pick by itself.
NOT_AUTO: dict[str, str] = {
    "old_bed_frame": "a bare frame without a mattress",
    "desk_lamp_arm_01": "clamps to the edge of a desk: it has no foot to stand on",
    "Ukulele_01": "cannot stand by itself",
    "brass_diya_lantern": "a 12 cm lantern on a chain, not the light of a room",
}

# The menu section of a class. A piece for the garden goes to "outdoor" whatever its class.
SECTION: dict[str, str] = {
    "sofa": "sofas", "loveseat": "sofas", "sectional": "sofas",
    "armchair": "armchairs", "ottoman": "armchairs",
    "chair": "chairs", "stool": "chairs", "bench": "chairs",
    "coffee_table": "tables", "side_table": "tables", "dining_table": "tables", "console_table": "tables",
    "desk": "desks",
    "bed": "beds", "crib": "beds", "nightstand": "beds",
    "dresser": "storage", "wardrobe": "storage", "bookshelf": "storage", "cabinet": "storage", "tv_stand": "storage",
    "floor_lamp": "lighting", "table_lamp": "lighting", "ceiling_light": "lighting",
    "rug": "rugs", "wall_art": "wall", "mirror": "wall", "plant": "plants", "decor": "decor",
}
OUTDOOR_WORDS = re.compile(r"\b(outdoor|patio|garden|picnic)\b", re.I)
KIDS_WORDS = re.compile(r"\b(kids?|child(ren)?('s)?|toddler|nursery|baby)\b", re.I)
OUTDOOR = frozenset({"plastic_monobloc_chair_01", "planter_box_01", "planter_box_02", "planter_box_03"})  # garden pieces whose name does not say so

# What a piece of a class can believably measure, in metres: (w, d, h), each (least, most).
# Poly Haven draws at real size, with exceptions that only show against these: a chair 2.27 m
# tall, a cabinet 1.1 m deep, a table 96 cm high. Such a piece is left out, not resized by guess.
BOUNDS: dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = {
    "sofa": ((1.5, 3.2), (0.6, 1.3), (0.6, 1.2)),
    "loveseat": ((1.0, 1.9), (0.55, 1.2), (0.6, 1.2)),
    "armchair": ((0.5, 1.3), (0.5, 1.3), (0.6, 1.25)),
    "chair": ((0.35, 0.8), (0.35, 0.85), (0.7, 1.2)),
    "stool": ((0.25, 0.7), (0.25, 0.7), (0.35, 1.1)),
    "ottoman": ((0.3, 1.5), (0.3, 1.2), (0.25, 0.55)),
    "bench": ((0.7, 2.2), (0.25, 0.8), (0.35, 1.1)),
    "bed": ((0.8, 2.3), (1.8, 2.4), (0.3, 2.2)),
    "coffee_table": ((0.5, 1.9), (0.4, 1.5), (0.25, 0.65)),
    "side_table": ((0.25, 0.9), (0.25, 0.9), (0.35, 0.85)),
    "nightstand": ((0.3, 0.9), (0.25, 0.65), (0.35, 0.85)),
    "console_table": ((0.6, 2.0), (0.2, 0.65), (0.6, 1.0)),
    "desk": ((0.6, 2.2), (0.4, 1.1), (0.65, 0.82)),
    "dining_table": ((0.7, 3.0), (0.6, 1.5), (0.68, 0.82)),
    "cabinet": ((0.3, 2.6), (0.25, 0.8), (0.4, 2.4)),
    "dresser": ((0.5, 2.0), (0.3, 0.7), (0.5, 1.6)),
    "bookshelf": ((0.3, 2.6), (0.15, 0.75), (0.5, 2.4)),
    "ceiling_light": ((0.1, 1.6), (0.1, 1.6), (0.1, 1.6)),
    "table_lamp": ((0.08, 0.7), (0.08, 0.7), (0.15, 0.95)),
    "plant": ((0.1, 1.5), (0.1, 1.5), (0.1, 2.5)),
    "mirror": ((0.2, 2.0), (0.005, 0.2), (0.2, 2.3)),
    "wall_art": ((0.1, 2.5), (0.005, 0.25), (0.1, 2.5)),
    "decor": ((0.02, 1.2), (0.02, 1.2), (0.02, 1.2)),
}
# Drawn too large as well, where only the height of the seat shows it (measured on the fetched model).
TOO_LARGE: dict[str, str] = {
    "painted_wooden_bench": "its seat is 67 cm above the floor",
}
# A decoration the designer may put down by itself fits on a shelf or a table; a larger one
# (a row of candle holders a metre long, a tea service laid out) is for a person to place.
DECOR_AUTO_M = (0.6, 0.7)  # widest side on the floor, height
MIN_ROOM_LIGHT_M = 0.25  # a ceiling light narrower than this is an ornament

# The same design in other sizes: these share a family key.
FAMILIES: dict[str, str] = {
    "steel_frame_shelves_01": "steel_frame_shelves", "steel_frame_shelves_02": "steel_frame_shelves", "steel_frame_shelves_03": "steel_frame_shelves",
    "modern_coffee_table_01": "modern_coffee_table", "modern_coffee_table_02": "modern_coffee_table",
}

# Names a card shows, where the collection's own name says the wrong thing or reads badly.
NAMES: dict[str, str] = {
    "Rockingchair_01": "Rocking chair",
    "ArmChair_01": "Armchair",
    "modern_arm_chair_01": "Modern armchair",
    "mid_century_lounge_chair": "Mid-century lounge chair",
    "ClassicConsole_01": "Classic console table",
    "book_encyclopedia_set_01": "Encyclopedia, a row of volumes",
    "planter_pot_clay": "Clay planter pot",
    "wooden_bookshelf_worn": "Worn wooden bookshelf",
    "WoodenTable_01": "Long low wooden table",
    "WoodenTable_02": "Small round wooden table",
    "WoodenTable_03": "Wooden sideboard with drawers",
    "bar_chair_round_01": "Round bar stool",
    "coffee_table_round_01": "Round coffee table",
    "side_table_tall_01": "Tall side table",
    "desk_lamp_arm_01": "Desk lamp with an arm",
    "drawer_cabinet": "Steel frame shelves with drawers",
    "Shelf_01": "Tall shelf",
    "jug_01": "Ceramic jug",
    "vintage_day_bed": "Vintage day bed",
}
NAME_MAX = 48
TAGS_MAX = 12
CLASS_WORDS = {cls: cls.replace("_", " ") for cls in FURNITURE_CLASSES}


def listing(refresh: bool = False) -> dict[str, dict]:
    """Poly Haven's list of models: {asset id: its record}. Downloaded once, then read from disk."""
    if refresh or not LISTING.exists():
        META.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=120, headers=source.HEADERS, follow_redirects=True) as http:
            LISTING.write_bytes(http.get(f"{source.API}/assets", params={"t": "models"}).raise_for_status().content)
    return json.loads(LISTING.read_text(encoding="utf-8"))


def _metres(value: float) -> float:
    """A side as the artifact states it: to the millimetre, finer under a centimetre, never zero
    (objects/normalize.py will not stretch a model to a side it does not have)."""
    return max(round(value, 4 if value < 0.01 else 3), 0.0001)


def size(asset_id: str, record: dict) -> tuple[float, float, float]:
    """(w, d, h) of the piece as it is placed: the fetched model's, with w and d swapped when it
    is turned a quarter (it is turned before it is sized, library/sources.py)."""
    x, y, z = (v / 1000 for v in record["dimensions"])  # millimetres, Blender's axes: z is up
    w, d, h = SIZE.get(asset_id, (x, y, z))
    if TURN.get(asset_id, 0) % 180:
        w, d = d, w
    return _metres(w), _metres(d), _metres(h)


def believable(asset_id: str, cls: str, dims: tuple[float, float, float]) -> bool:
    """Whether a piece of this class can be this size (BOUNDS), as it stands once turned."""
    return asset_id not in TOO_LARGE and all(lo <= v <= hi for v, (lo, hi) in zip(dims, BOUNDS[cls]))


def _sentence(text: str) -> str:
    text = re.sub(r"\s+", " ", text.replace("_", " ")).strip()
    return text[:1].upper() + text[1:].lower()


def names(records: dict[str, dict], kept: dict[str, str]) -> dict[str, str]:
    """What a card shows for each kept piece: the collection's name in sentence case, without
    the "01" of a design that has no "02", and never the same name for two pieces."""
    numbered = re.compile(r"^(.*?)[ _]0*(\d+)$")
    # How many models share a name once its number is taken off ("Sungka Board" and "Sungka Board 02" do).
    bases = Counter((m.group(1) if (m := numbered.match(r["name"])) else r["name"]).lower() for r in records.values())
    out: dict[str, str] = {}
    for asset_id in kept:
        name = records[asset_id]["name"]
        m = numbered.match(name)
        if asset_id in NAMES:
            name = NAMES[asset_id]
        elif m and bases[m.group(1).lower()] == 1:
            name = m.group(1)
        out[asset_id] = _sentence(name)[:NAME_MAX].rstrip(" ,")
    # Two models can carry one name ("Wooden Table 02" is a side table and a dining table).
    for name, count in Counter(out.values()).items():
        if count > 1:
            for asset_id in [a for a, n in out.items() if n == name]:
                out[asset_id] = f"{name}, {CLASS_WORDS[kept[asset_id]]}"[:NAME_MAX]
    clash = [n for n, c in Counter(out.values()).items() if c > 1]
    if clash:
        raise SystemExit(f"two pieces share a name: {clash}")
    return out


def tags(record: dict) -> list[str]:
    """Lowercase words for search: what it is made of first, then the collection's own tags."""
    attributes = record.get("attributes") or {}
    words = [*(attributes.get("material") or []), *(record.get("tags") or []), *(attributes.get("condition") or [])]
    seen: list[str] = []
    for word in words:
        word = re.sub(r"\s+", " ", str(word).replace("_", " ")).strip().lower()
        if word and word not in seen:
            seen.append(word)
    return seen[:TAGS_MAX]


def section(asset_id: str, cls: str, record: dict) -> str:
    title = record["name"]
    if asset_id in OUTDOOR or OUTDOOR_WORDS.search(title):
        return "outdoor"
    if KIDS_WORDS.search(title):
        return "kids"
    return SECTION[cls]


def auto(asset_id: str, cls: str, dims: tuple[float, float, float]) -> bool:
    """Whether the designer may pick the piece by itself. Every piece in PIECES was measured and
    its facing looked at, so what is left is whether it suits being placed without a person."""
    if asset_id in NOT_AUTO:
        return False
    w, d, h = dims
    if cls == "decor":
        return max(w, d) <= DECOR_AUTO_M[0] and h <= DECOR_AUTO_M[1]
    if cls == "ceiling_light":
        return max(w, d) >= MIN_ROOM_LIGHT_M
    return True


def build(refresh: bool = False) -> tuple[list[dict], Counter]:
    """(the library's records, how many models were left out for each reason)."""
    records = listing(refresh)
    left_out: Counter = Counter()
    kept: dict[str, str] = {}
    sizes: dict[str, tuple[float, float, float]] = {}
    for asset_id, record in records.items():
        cls = CLASS_OF.get(asset_id)
        if cls is None:
            if asset_id in LEFT_OUT:
                left_out[LEFT_OUT[asset_id]] += 1
            elif record["category"].startswith(HOME_CATEGORIES):
                left_out["new since this list was written: not looked at yet"] += 1
                print(f"not looked at yet: {asset_id} ({record['category']})", file=sys.stderr)
            else:
                left_out[f"not a home piece: {record['category'].split('/')[0].lower()}"] += 1
            continue
        dims = size(asset_id, record)
        if not believable(asset_id, cls, dims):
            left_out["its size is not believable for its class"] += 1
            print(f"left out, size not believable for class {cls}: {asset_id} {dims[0]:.2f} x {dims[1]:.2f} x {dims[2]:.2f} m (w d h)", file=sys.stderr)
            continue
        kept[asset_id], sizes[asset_id] = cls, dims
    for asset_id in sorted(set(CLASS_OF) - set(records)):
        print(f"no longer in Poly Haven's listing: {asset_id}", file=sys.stderr)

    shown = names(records, kept)
    sections = [key for key, _label in CATEGORIES]
    items: list[dict] = []
    for asset_id, cls in kept.items():
        record = records[asset_id]
        w, d, h = sizes[asset_id]
        cat = section(asset_id, cls, record)
        assert cls in FURNITURE_CLASSES and cat in sections, (asset_id, cls, cat)
        item: dict = {
            "id": f"ph:{asset_id}", "src": "polyhaven", "name": shown[asset_id], "full": record["name"], "brand": None, "line": None,
            "cls": cls, "cat": cat, "w": w, "d": d, "h": h,
        }
        if TURN.get(asset_id):
            item["turn"] = TURN[asset_id]
        item |= {
            "img": record["thumbnail_url"], "faces": record.get("polycount"), "fam": f"ph:{FAMILIES.get(asset_id, asset_id)}", "color": None,
            "tags": tags(record), "license": "CC0", "credit": f"{', '.join(record.get('authors') or {}) or 'Poly Haven'} / Poly Haven",
            "url": source.page_url(asset_id), "auto": auto(asset_id, cls, (w, d, h)),
        }
        items.append(item)
    # In the order of the menu, then by name: the file reads like the menu and changes little between builds.
    items.sort(key=lambda item: (sections.index(item["cat"]), item["cls"], item["name"].lower(), item["id"]))
    return items, left_out


def write(items: list[dict]) -> int:
    """The artifact: one JSON object per line, gzipped with no timestamp so the same pieces give the same file."""
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as f:
        for item in items:
            f.write((json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(raw.getvalue())
    return len(raw.getvalue())


def main(argv: list[str]) -> None:
    items, left_out = build(refresh="--refresh" in argv)
    size_bytes = write(items)
    print(f"{len(items)} pieces -> {OUT} ({size_bytes / 1024:.1f} kB)")
    for title, key in (("per section", "cat"), ("per class", "cls")):
        counts = Counter(item[key] for item in items)
        print(f"{title}: " + ", ".join(f"{name} {count}" for name, count in counts.most_common()))
    print(f"the designer may pick {sum(item['auto'] for item in items)} by itself; {sum('turn' in item for item in items)} are turned")
    print(f"left out: {sum(left_out.values())}")
    for reason, count in left_out.most_common():
        print(f"  {count:4}  {reason}")


if __name__ == "__main__":
    main(sys.argv[1:])
