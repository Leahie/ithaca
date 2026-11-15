"""Which pieces furnish a room, in a look. Owner: B.

A furnished room is more than a sofa and a table. A designer works through a room in layers, and
this does the same, from the library:

1. the piece the room is about (the sofa, the bed, the dining table, the desk), sized to the room
   and to the wall it will stand on, chosen for the look;
2. what makes that piece usable (the coffee table, the bedside tables, the chairs), sized to it;
3. the ground (a rug large enough to hold the group together);
4. light at three heights (a ceiling light, floor lamps, table lamps), because one lamp makes a
   room flat;
5. the walls (a picture over the main piece, a mirror over a console) and something alive
   (a tall plant by the window);
6. the small things that say someone lives here (a vase on the coffee table, a second chair).

Each piece after the first is chosen to go with the ones before it: the library's look vectors
say which pieces suit the look in words, and its compat vectors which pieces belong in one room
(library/index.py, `candidates`). Sizes follow the rules of thumb a designer uses: a coffee table
about two thirds of the sofa, a rug wider than the sofa, a picture narrower than what it hangs
over. How much goes in follows the size of the room and the level asked for: "essential" is
layers 1 to 3 and a lamp; "full" adds the rest once; "lavish" adds the second and third of each.

`plan` only chooses. furniture/furnish.py adds the pieces to someone's furniture and has them
arranged.
"""

from __future__ import annotations

import itertools
import math
import random
import re
from dataclasses import dataclass, field

from shapely.geometry import Polygon
from shapely.ops import unary_union

from packages.schema.inventory import InventoryItem
from packages.schema.twin import PropertyTwin, Room
from services.api.agent import views
from services.api.library.index import Library, Piece, library, words
from services.api.sim import layout_solver

LEVELS = {"essential": 0, "full": 1, "lavish": 2}
# Share of the floor the furniture standing on it may cover, by level. More, and no arrangement
# keeps the ways through the room open.
FLOOR_SHARE = (0.26, 0.32, 0.36)
SOLVER_MAX = 13  # floor pieces the solver arranges together in reasonable time
PICK_FROM = 3  # the choice falls among this many best candidates, so two rooms in one look differ


@dataclass(frozen=True)
class Look:
    """A designer's recipe: what to look for, in the words of the library's vocabulary."""

    key: str
    name: str
    blurb: str
    phrases: tuple[str, ...]  # the look in words: styles, materials, moods
    tones: tuple[str, ...]  # the colours of the large pieces
    accents: tuple[str, ...]  # the colours of the small ones: a chair, the pictures, a vase
    materials: tuple[str, ...]
    palette: tuple[str, ...]  # hex, for a card


LOOKS: list[Look] = [
    Look("quiet_luxury", "Quiet luxury", "Cream and taupe, soft textures and warm wood, with brass in small doses.",
         ("quiet luxury", "warm neutral", "boucle", "linen", "light wood", "brass"), ("cream", "beige", "white", "tan"), ("gold", "brown", "tan"),
         ("boucle", "linen", "light wood", "marble", "brass"), ("#efe7da", "#cdbfa9", "#8a6f52", "#b8975a")),
    Look("emerald_deco", "Emerald deco", "Deep green velvet against brass, marble and black: a 1930s cocktail bar at home.",
         ("art deco", "emerald velvet", "brass", "marble", "glam"), ("emerald", "green", "black", "charcoal"), ("gold", "emerald", "cream"),
         ("velvet", "brass", "marble", "glass"), ("#1f5c4a", "#b8975a", "#1b1b1b", "#efe7da")),
    Look("midcentury_walnut", "Mid-century walnut", "Walnut on tapered legs, tufted grey seating, a mustard or teal accent.",
         ("mid-century modern", "walnut", "tapered legs", "tufted"), ("grey", "brown", "charcoal"), ("mustard", "teal", "terracotta"),
         ("walnut", "dark wood", "leather", "brass"), ("#6b4a32", "#8a8d8f", "#c9972b", "#2f6f73")),
    Look("warm_japandi", "Warm Japandi", "Low, calm and pale: oak, linen and paper lamps, with one black line.",
         ("japandi", "scandinavian", "minimalist", "light wood", "linen"), ("beige", "cream", "white", "tan"), ("black", "olive", "charcoal"),
         ("light wood", "linen", "rattan", "ceramic"), ("#e9e1d3", "#c8ad86", "#2b2b2b", "#7c8363")),
    Look("parisian", "Parisian apartment", "Curved seats, a gilded mirror, marble and pale walls: a little worn, very elegant.",
         ("parisian apartment", "french country", "traditional", "curved", "gilded"), ("cream", "white", "grey", "beige"), ("gold", "blush", "black"),
         ("velvet", "marble", "brass", "dark wood"), ("#f1ece3", "#b9b4ab", "#c3a063", "#d9b7b0")),
    Look("modern_organic", "Modern organic", "Rounded shapes, pale stone and raw wood, in earth colours.",
         ("modern organic", "organic", "curved", "natural wood", "earthy"), ("cream", "beige", "tan", "white"), ("terracotta", "olive", "brown"),
         ("light wood", "ceramic", "linen", "jute"), ("#ece3d4", "#b98b62", "#a45a3c", "#7f8a5b")),
    Look("hollywood_glam", "Hollywood glam", "Jewel velvet, gold and glass, a crystal chandelier: made for the evening.",
         ("hollywood regency", "glam", "velvet", "gold", "crystal"), ("navy", "blush", "blue", "white"), ("gold", "blush", "white"),
         ("velvet", "brass", "glass", "marble"), ("#27365c", "#d9b7b0", "#c9a657", "#f4f1ec")),
    Look("industrial_loft", "Industrial loft", "Dark leather, black steel and thick dark wood, softened by one worn rug.",
         ("industrial", "leather", "black metal", "dark wood", "loft"), ("brown", "black", "charcoal", "grey"), ("tan", "terracotta", "olive"),
         ("leather", "black metal", "dark wood"), ("#5b3d2a", "#232323", "#7a7a78", "#a8713f")),
    Look("coastal_linen", "Coastal linen", "White slipcovers, rattan and washed wood, with the blues of the sea.",
         ("coastal", "linen", "rattan", "white", "light blue"), ("white", "cream", "beige"), ("blue", "navy", "teal"),
         ("linen", "rattan", "light wood", "jute"), ("#f6f3ec", "#d9cdb8", "#7fa3c0", "#2d4a6b")),
    Look("moody_library", "Moody library", "Dark wood, oxblood leather and green, books to the ceiling and low lamps.",
         ("dark academia", "traditional", "leather", "dark wood", "moody"), ("brown", "burgundy", "green", "charcoal"), ("gold", "burgundy", "navy"),
         ("leather", "dark wood", "brass", "velvet"), ("#3b2a22", "#6d1f24", "#2f4a3a", "#b8975a")),
    Look("modern_farmhouse", "Modern farmhouse", "Honest oak, cream linen and black iron: plain, generous and easy to live in.",
         ("farmhouse", "rustic", "oak", "linen"), ("cream", "beige", "white", "brown"), ("black", "olive", "blue"),
         ("light wood", "linen", "black metal", "jute"), ("#f0eadf", "#b08b5e", "#2a2a2a", "#8a9a7b")),
    Look("bright_scandinavian", "Bright Scandinavian", "White and pale oak, grey wool, clean lines and a lot of daylight.",
         ("scandinavian", "minimalist", "light wood", "modern"), ("white", "grey", "cream", "beige"), ("mustard", "blue", "black"),
         ("light wood", "linen", "ceramic"), ("#f7f6f2", "#d6d3cc", "#c9a877", "#e0b03a")),
]
LOOK_BY_KEY = {look.key: look for look in LOOKS}
DEFAULT_LOOK = "quiet_luxury"


def look_for(text: str | None) -> Look:
    """A look from its key or name, or made from the user's own words ("emerald velvet, brass")."""
    if not text or not text.strip():
        return LOOK_BY_KEY[DEFAULT_LOOK]
    wanted = text.strip().lower()
    for look in LOOKS:
        if wanted in (look.key, look.name.lower(), look.key.replace("_", " ")):
            return look
    said = set(words(wanted))
    near = max(LOOKS, key=lambda look: len(said & set(words(" ".join(look.phrases) + " " + look.name))))
    phrases = tuple(part.strip() for part in re.split(r"[,;]| and | with ", wanted) if part.strip())[:8] or (wanted,)
    tones = tuple("grey" if t == "gray" else t for t in dict.fromkeys(words(wanted)) if t in _TONES)  # the index spells it "grey"
    # Their words lead; the nearest recipe fills in what they did not say (materials, the accent).
    return Look("custom", text.strip()[:60], f"In your words: {text.strip()[:120]}", phrases, tones or near.tones, near.accents, near.materials, near.palette)


_TONES = {"white", "cream", "beige", "tan", "brown", "black", "charcoal", "gray", "gold", "blush", "pink", "red", "burgundy", "terracotta",
          "orange", "mustard", "yellow", "olive", "green", "emerald", "teal", "blue", "navy", "purple"}

# What a room is used as, from its name on the plan.
_USES = (
    ("kids", ("nursery", "kid", "child", "play")),
    ("bedroom", ("bed", "guest")),
    ("dining", ("dining",)),
    ("office", ("office", "study", "library", "den")),
    ("living", ("living", "family", "great", "lounge", "media", "sitting", "sun")),
    ("entry", ("hall", "entry", "foyer", "landing", "corridor", "mud", "vestibule")),
    ("outdoor", ("patio", "deck", "balcony", "porch", "terrace")),
    ("fitted", ("kitchen", "bath", "closet", "pantry", "laundry", "garage", "utility", "storage", "stair", "wc", "toilet", "powder")),
)
USES = ("living", "bedroom", "dining", "office", "entry", "kids", "outdoor")


def use_of(label: str) -> str | None:
    """What a room is furnished as, from its label: one of USES, "fitted" (a kitchen, a bathroom,
    a closet: built in, not furnished), or None when the label says nothing."""
    name = label.lower()
    return next((use for use, hints in _USES if any(h in name for h in hints)), None)


@dataclass
class Chosen:
    """One line of a plan: a piece (or a pair, or six chairs) and what it is for."""

    role: str  # "sofa", "picture over the sofa"
    piece: Piece | None  # from the library; None when the family's own piece fills the role
    count: int = 1
    own: InventoryItem | None = None  # the family's own piece that fills the role
    with_role: str | None = None  # the role of the piece it goes on, over or under

    @property
    def w(self) -> float:
        return self.piece.w if self.piece else self.own.dims_m.w

    @property
    def d(self) -> float:
        return self.piece.d if self.piece else self.own.dims_m.d

    @property
    def h(self) -> float:
        return self.piece.h if self.piece else self.own.dims_m.h


@dataclass
class Plan:
    use: str
    look: Look
    level: str
    chosen: list[Chosen] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # roles the library had nothing for, in this size
    note: str | None = None  # why nothing was planned

    @property
    def pieces(self) -> int:
        return sum(c.count for c in self.chosen)


# Classes the solver stands on the floor: they take room, so they count against the floor share.
_FLOOR = {"sofa", "loveseat", "sectional", "armchair", "chair", "stool", "ottoman", "bench", "coffee_table", "side_table", "dining_table",
          "desk", "console_table", "tv_stand", "bookshelf", "cabinet", "dresser", "wardrobe", "nightstand", "bed", "crib"}
_NOT_A_DINING_CHAIR = ("office", "desk", "task", "swivel", "gaming", "folding", "rocking", "bar", "counter", "kids", "toddler", "outdoor", "patio")
_NOT_A_GROWN_UPS_BED = ("bunk", "loft", "trundle", "toddler", "kids", "crib", "daybed")
_NOT_FOR_A_TABLE = ("pillow", "cushion", "throw", "blanket", "basket", "hamper", "bin", "pouf", "doormat")


class _Picker:
    """Works through a room's recipe, choosing each piece to go with the ones before it."""

    def __init__(self, lib: Library, twin: PropertyTwin, room: Room, use: str, look: Look, level: str, keep: list[InventoryItem],
                 avoid: tuple[str, ...], seed: int) -> None:
        self.lib, self.look, self.use = lib, look, use
        self.level = LEVELS.get(level, 1)
        self.plan = Plan(use, look, level)
        self.rng = random.Random(seed)
        self.avoid = avoid
        self.anchors: list[tuple[str, float]] = []
        self.families: set[str] = set()
        outline = Polygon(room.polygon)
        self.area = room.area_m2 or outline.area
        self.height = room.height_m
        on_its_floor = room.model_copy(update={"floor_y": views.floor_height(twin, room.room_id)})  # window sills are measured from the real floor
        scene = layout_solver._scene(on_its_floor, twin.openings, twin.walls, twin.sweeps)
        # The floor furniture can stand on: between the walls, clear of the doors and of the open
        # sides of the room, where people walk through. Its length and width bound what fits.
        free = scene.room.difference(unary_union(scene.door_zones)) if scene.door_zones else scene.room
        free = max(getattr(free, "geoms", [free]), key=lambda g: g.area) if not free.is_empty else scene.room
        box = free.minimum_rotated_rectangle.exterior.coords
        sides = sorted((math.dist(box[0], box[1]), math.dist(box[1], box[2])))
        self.short, self.long = max(sides[0], 1.0), max(sides[1], 1.0)
        walls = sorted((part.length for _wall, part in scene.solid), reverse=True)
        self.wall = walls[0] if walls else 0.0  # the longest stretch of solid wall: where the main piece goes
        self.wall2 = walls[1] if len(walls) > 1 else self.wall * 0.6
        # What is left of each stretch of solid wall as pieces are chosen to stand on it. A room
        # has only so much wall: a sofa, a bookcase and a console do not all fit on one.
        self.free_wall = list(walls)
        self.wall_of: dict[str, int] = {}  # role -> the stretch it was given
        self.floor_left = FLOOR_SHARE[self.level] * self.area
        self.solver_left = min(SOLVER_MAX, max(3, int(self.area / 2.0)))
        self.own: dict[str, list[InventoryItem]] = {}
        for item in keep:
            self.own.setdefault(item.item_class, []).append(item)

    def got(self, role: str) -> Chosen | None:
        return next((c for c in self.plan.chosen if c.role == role), None)

    def _wall_room(self, width: float, count: int, main: bool, beside: str | None) -> list[int] | None:
        """The stretches of wall `count` pieces this wide would stand on, or None when the room has
        not that much wall left. The main piece takes the longest wall; a piece that goes beside
        another (a bedside table) must fit on that one's wall; the rest take the shortest that fits,
        which keeps the long walls for long pieces."""
        left, given = list(self.free_wall), []
        for _ in range(count):
            fits = [k for k, length in enumerate(left) if length >= width + 0.05]
            if beside in self.wall_of:
                fits = [k for k in fits if k == self.wall_of[beside]]
            if not fits and main and left:
                fits = [max(range(len(left)), key=lambda k: left[k])]  # the room is about this piece: it goes in, on the longest wall there is
            if not fits:
                return [] if main else None
            k = max(fits, key=lambda k: left[k]) if main else min(fits, key=lambda k: left[k])
            left[k] -= width + 0.1
            given.append(k)
        return given

    def pick(
        self, role: str, classes: tuple[str, ...], *, count: int = 1, level: int = 0, size: dict[str, tuple[float, float]] | None = None,
        accent: bool = False, want: tuple[str, ...] = (), avoid: tuple[str, ...] = (), weight: float = 1.0, with_role: str | None = None,
        same_family_ok: bool = False, fewer_ok: bool = False,
    ) -> Chosen | None:
        """Choose the piece for a role, or None: the level does not call for it, the room has no
        floor left for it, or the library has nothing of that kind in that size."""
        if self.level < level or count < 1:
            return None
        on_floor = classes[0] in _FLOOR
        on_wall = classes[0] in layout_solver.WALL_PIECES  # its back goes on solid wall (the solver's rule)
        beside = with_role if classes[0] == "nightstand" else None
        if on_floor and self.solver_left < count:
            return None
        # The family's own piece of that kind fills the role first: a room is planned round what they keep.
        mine = next((self.own[c].pop(0) for c in classes if self.own.get(c)), None)
        if mine is not None:
            chosen = Chosen(role, None, 1, mine, with_role)
            return self._take(chosen, on_floor, weight, self._wall_room(mine.dims_m.w, 1, weight >= 2, beside) if on_wall else None)
        size = {axis: (lo, max(hi, lo)) for axis, (lo, hi) in (size or {}).items()}
        if classes[0] == "decor":  # these go on tables: a cushion or a floor basket is not a centrepiece
            avoid = (*avoid, *_NOT_FOR_A_TABLE)
        windows = [size, {axis: (lo * 0.8, hi * 1.2) for axis, (lo, hi) in size.items()}] if size else [{}]
        # A patio is furnished from the outdoor section only. A child's room from the kids' section
        # first, then from the rest: there is no kids' bedside table, and an ordinary one will do.
        sections: list[tuple[str | None, tuple[str, ...]]] = [(None, ("outdoor", "kids"))]
        if self.use == "outdoor":
            sections = [("outdoor", ())]
        elif self.use == "kids":
            sections = [("kids", ()), (None, ("outdoor",))]
        for (only, never), window in itertools.product(sections, windows):
            found = self.lib.candidates(
                classes, phrases=self.look.phrases, anchors=self.anchors, avoid=(*self.avoid, *avoid), size=window,
                tones=self.look.accents if accent else self.look.tones, materials=self.look.materials, want=want,
                exclude_families=None if same_family_ok else self.families, only_cat=only, not_cats=never,
                k=40 if on_floor else PICK_FROM * 2,  # more to choose from where the floor and the walls then rule some out
            )
            for many in ([count, *range(count - 1, 0, -1)] if fewer_ok else [count]):
                fit = found
                if on_floor:  # only what the floor still has room for
                    fit = [(p, s) for p, s in fit if p.w * p.d * many <= self.floor_left]
                if on_wall:  # and the walls
                    fit = [(p, s) for p, s in fit if self._wall_room(p.w, many, weight >= 2, beside) is not None]
                if fit:
                    best = fit[:PICK_FROM]
                    piece = self.rng.choices([p for p, _ in best], weights=[3, 2, 1][:len(best)])[0]
                    return self._take(Chosen(role, piece, many, None, with_role), on_floor, weight, self._wall_room(piece.w, many, weight >= 2, beside) if on_wall else None)
        self.plan.skipped.append(role)
        return None

    def _take(self, chosen: Chosen, on_floor: bool, weight: float, walls: list[int] | None = None) -> Chosen:
        self.plan.chosen.append(chosen)
        for k in walls or []:
            self.free_wall[k] -= chosen.w + 0.1
            self.wall_of.setdefault(chosen.role, k)
        if on_floor:
            self.floor_left -= chosen.w * chosen.d * chosen.count
            self.solver_left -= chosen.count
        piece = chosen.piece
        if piece is None and chosen.own is not None:  # their own piece sets the tone too, if the library knows it
            piece = self.lib.by_img.get(chosen.own.source_image_url)
        if piece is not None:
            self.anchors.append((piece.id, weight))
            self.families.add(piece.fam)
        return chosen


def _over(p: _Picker, under: Chosen | None, tallest: float) -> float:
    """How tall a picture or a mirror may be to hang over a piece: what is left between the piece
    and the ceiling, less a hand's width above and below it."""
    return max(min(tallest, p.height - (under.h if under else 0.9) - 0.4), 0.25)


def _rug(p: _Picker, over_w: float, over_d: float, with_role: str, level: int = 0, around: float = 0.3) -> None:
    """A rug for a group `over_w` wide: wider than the group, and no larger than the floor."""
    long_most = max(min(over_w + 1.9, p.long - 0.4), 1.2)
    short_most = max(min(over_d + 2.2, p.short - 0.4), 0.9)
    p.pick("rug", ("rug",), level=level, with_role=with_role, accent=False,
           size={"long": (min(over_w + around, long_most), long_most), "short": (min(1.3, short_most), short_most)})


def _living(p: _Picker) -> None:
    area, big = p.area, p.area >= 30
    kinds = ("sectional", "sofa") if area >= 26 and p.wall >= 3.4 else ("sofa", "loveseat") if area < 14 else ("sofa",)
    most = max(min(p.wall - 0.3, 3.3 if big else 2.6 if area >= 18 else 2.25), 1.5)
    sofa = p.pick("sofa", kinds, size={"w": (1.35 if area < 14 else min(1.8, most), most)}, weight=2.5)
    sofa = sofa or p.pick("sofa", ("sofa", "loveseat", "sectional"), size={"w": (1.2, max(p.wall - 0.2, 1.4))}, weight=2.5)
    wide = sofa.w if sofa else 2.0
    p.pick("coffee table", ("coffee_table",), size={"w": (max(0.45 * wide, 0.55), max(0.8 * wide, 0.9)), "h": (0.2, 0.56)}, with_role="sofa")
    _rug(p, wide, 1.6, "sofa")
    chairs = 0 if area < 11 else 2 if area >= 18 else 1
    p.pick("armchair", ("armchair",), count=chairs, size={"w": (0.55, 1.1)}, accent=True, weight=1.5)
    tables = 2 if area >= 22 else 1
    p.pick("side table", ("side_table",), count=tables, size={"w": (0.3, 0.75), "h": (0.38, 0.72)}, with_role="sofa")
    p.pick("floor lamp", ("floor_lamp",), size={"h": (1.25, 2.0)}, with_role="armchair" if chairs else "sofa")
    # full: light at table height, the wall, something alive, something small
    p.pick("table lamp", ("table_lamp",), count=tables, level=1, size={"h": (0.3, 0.8)}, with_role="side table")
    p.pick("picture over the sofa", ("wall_art",), level=1, size={"w": (0.45 * wide, 0.95 * wide), "h": (0.25, _over(p, sofa, 1.25))}, accent=True, with_role="sofa")
    p.pick("plant", ("plant",), level=1, size={"h": (0.9, 2.3)})
    p.pick("coffee table decoration", ("decor",), level=1, size={"long": (0.08, 0.45), "h": (0.05, 0.5)}, accent=True, with_role="coffee table")
    if 15 <= area < 45:  # across a very large room nobody watches a screen
        p.pick("media console", ("tv_stand",), level=1, size={"w": (1.1, max(min(2.3, p.wall2 - 0.3), 1.2))})
    if big:
        p.pick("second sofa", ("loveseat", "sofa"), level=1, size={"w": (1.3, max(wide, 1.5))}, same_family_ok=True, weight=1.5)
    if area >= 20:
        p.pick("ottoman", ("ottoman",), level=1, size={"long": (0.4, 1.0)}, accent=True)
    # lavish: a second place to stand a lamp and hang a mirror, books, more green, a light overhead
    if area >= 18:
        console = p.pick("console", ("console_table",), level=2, size={"w": (0.9, max(min(1.8, p.wall2 - 0.3), 1.0)), "d": (0.2, 0.5)})
        if console:
            p.pick("mirror over the console", ("mirror",), level=2, size={"w": (0.4, console.w), "h": (0.3, _over(p, console, 1.3))}, with_role="console")
            p.pick("console lamp", ("table_lamp",), level=2, size={"h": (0.35, 0.85)}, with_role="console")
            p.pick("console decoration", ("decor",), level=2, size={"long": (0.08, 0.4), "h": (0.05, 0.5)}, accent=True, with_role="console")
    if area >= 22:
        p.pick("bookcase", ("bookshelf",), count=2 if area >= 40 else 1, level=2, size={"w": (0.6, 1.4), "h": (1.2, min(2.3, p.height - 0.1))})
    p.pick("ceiling light", ("ceiling_light",), level=2, size={"h": (0.12, max(p.height - 1.75, 0.2))}, with_role="coffee table")
    p.pick("second picture", ("wall_art",), level=2, size={"w": (0.5, 1.4), "h": (0.3, 1.3)}, accent=True)
    if area >= 24:
        p.pick("second plant", ("plant",), level=2, size={"h": (0.9, 2.3)})
    if big:
        p.pick("second floor lamp", ("floor_lamp",), level=2, size={"h": (1.25, 2.0)})


def _bedroom(p: _Picker) -> None:
    area = p.area
    most = 1.25 if area < 9.5 else 1.75 if area < 13 else 2.2
    bed = p.pick("bed", ("bed",), size={"w": (0.85 if area < 9.5 else 1.3, max(min(most, p.wall - 0.2), 0.9)), "d": (1.8, 2.4)},
                 avoid=_NOT_A_GROWN_UPS_BED, weight=2.5)
    wide = bed.w if bed else 1.6
    bedside = p.pick("bedside table", ("nightstand",), count=1 if area < 9.5 else 2, size={"w": (0.3, 0.75), "h": (0.35, 0.8)}, with_role="bed", fewer_ok=True)
    # No room for a bedside cabinet on the bed's wall (a window either side of it): a small table, which may stand before a window.
    bedside = bedside or p.pick("bedside table", ("side_table",), count=1 if area < 9.5 else 2, size={"w": (0.3, 0.6), "h": (0.4, 0.7)}, with_role="bed", fewer_ok=True)
    if bedside:
        p.pick("bedside lamp", ("table_lamp",), count=bedside.count, size={"h": (0.28, 0.8)}, with_role="bedside table")
    _rug(p, wide, 1.2, "bed", around=0.5)
    dresser = p.pick("dresser", ("dresser",), size={"w": (0.7, max(min(1.9, p.wall2 - 0.2), 0.8))}) if area >= 10 else None
    # full
    p.pick("picture over the bed", ("wall_art",), level=1, size={"w": (0.4 * wide, 0.9 * wide), "h": (0.25, _over(p, bed, 1.1))}, accent=True, with_role="bed")
    if area >= 13:
        p.pick("bench", ("bench",), level=1, size={"w": (0.8, max(wide, 0.9)), "h": (0.3, 0.6)}, with_role="bed")
    if dresser:
        p.pick("mirror over the dresser", ("mirror",), level=1, size={"w": (0.4, dresser.w), "h": (0.3, _over(p, dresser, 1.2))}, with_role="dresser")
        if dresser.h <= 1.25:  # a tall chest's top is above eye height: nothing is set on it
            p.pick("dresser decoration", ("decor",), level=1, size={"long": (0.08, 0.4), "h": (0.05, 0.5)}, accent=True, with_role="dresser")
    if area >= 11:
        p.pick("plant", ("plant",), level=1, size={"h": (0.8, 2.2)})
    if area >= 15:
        if p.pick("armchair", ("armchair",), level=1, size={"w": (0.55, 1.0)}, accent=True):
            p.pick("floor lamp", ("floor_lamp",), level=1, size={"h": (1.25, 2.0)}, with_role="armchair")
    # lavish
    if area >= 16:
        p.pick("wardrobe", ("wardrobe",), level=2, size={"w": (0.8, max(min(2.0, p.wall2 - 0.2), 0.9)), "h": (1.5, p.height - 0.1)})
    p.pick("ceiling light", ("ceiling_light",), level=2, size={"h": (0.1, max(p.height - 1.97, 0.12))})
    if not dresser:
        p.pick("standing mirror", ("mirror",), level=2, size={"h": (1.35, 2.0)})
    p.pick("second picture", ("wall_art",), level=2, size={"w": (0.4, 1.2), "h": (0.3, 1.2)}, accent=True)
    if area >= 15 and p.got("armchair"):
        p.pick("side table", ("side_table",), level=2, size={"w": (0.3, 0.6), "h": (0.38, 0.7)}, with_role="armchair")


def _dining(p: _Picker) -> None:
    area = p.area
    lo, hi = (0.9, 1.45) if area < 10 else (1.35, 2.0) if area < 17 else (1.7, 2.7)
    pulled = 1.5  # a chair either side of the table, tucked in
    table = p.pick("dining table", ("dining_table",), size={"w": (min(lo, max(p.long - pulled, 0.8)), max(min(hi, p.long - pulled), 0.8)), "d": (0.6, max(p.short - pulled, 0.7))}, weight=2.5)
    wide, deep = (table.w, table.d) if table else (1.6, 0.9)
    seats = 4 if wide < 1.5 else 6 if wide < 2.1 else 8
    chairs = p.pick("dining chair", ("chair",), count=seats, size={"w": (0.38, 0.66), "h": (0.7, 1.15)}, avoid=_NOT_A_DINING_CHAIR, with_role="dining table")
    if chairs and chairs.piece:  # as many as sit at this table: along its two sides, and one at each end
        fit = 2 * max(1, int(wide // (chairs.w + 0.08))) + 2
        if fit < chairs.count:
            p.floor_left += (chairs.count - fit) * chairs.w * chairs.d
            p.solver_left += chairs.count - fit
            chairs.count = fit
        p.solver_left += chairs.count  # seated round the table afterwards, not arranged by the solver (furniture/arrange.py)
    p.pick("light over the table", ("ceiling_light",), size={"long": (0.2, max(0.85 * wide, 0.5)), "h": (0.12, max(p.height - 0.75 - 0.65, 0.2))},
           with_role="dining table")
    # full
    if p.long >= wide + 2.4 and p.short >= deep + 2.2:  # a rug the chairs stay on when pulled out, or none
        p.pick("rug", ("rug",), level=1, with_role="dining table",
               size={"long": (wide + 1.0, min(wide + 2.2, p.long - 0.4)), "short": (deep + 1.0, min(deep + 2.2, p.short - 0.4))})
    behind = max(p.short - deep - pulled, p.long - wide - pulled)  # what is left past the chairs, the better way round
    board = p.pick("sideboard", ("cabinet",), level=1, size={"w": (1.0, max(min(2.2, p.wall - 0.3), 1.1)), "d": (0.2, max(behind - 0.2, 0.25)), "h": (0.55, 1.1)}) if area >= 11 and behind >= 0.65 else None
    if board:
        p.pick("sideboard lamp", ("table_lamp",), count=2 if board.w >= 1.3 else 1, level=1, size={"h": (0.35, 0.85)}, with_role="sideboard")
        p.pick("picture over the sideboard", ("wall_art", "mirror"), level=1, size={"w": (0.45 * board.w, board.w), "h": (0.3, _over(p, board, 1.2))},
               accent=True, with_role="sideboard")
    p.pick("centrepiece", ("decor",), level=1, size={"long": (0.1, 0.5), "h": (0.08, 0.5)}, accent=True, with_role="dining table")
    p.pick("plant", ("plant",), level=1, size={"h": (0.9, 2.3)})
    # lavish
    if board:
        p.pick("sideboard decoration", ("decor",), level=2, size={"long": (0.08, 0.4), "h": (0.05, 0.5)}, accent=True, with_role="sideboard")
    if area >= 18 and behind >= 0.65:
        p.pick("display cabinet", ("bookshelf", "cabinet"), level=2, size={"w": (0.6, 1.4), "d": (0.2, max(behind - 0.2, 0.25)), "h": (1.3, min(2.3, p.height - 0.1))})
    p.pick("second plant", ("plant",), level=2, size={"h": (0.9, 2.3)})
    p.pick("second picture", ("wall_art",), level=2, size={"w": (0.5, 1.3), "h": (0.3, 1.2)}, accent=True)


def _office(p: _Picker) -> None:
    area = p.area
    desk = p.pick("desk", ("desk",), size={"w": (0.9, max(min(1.9, p.wall - 0.2), 1.0))}, weight=2.5)
    p.pick("desk chair", ("chair",), size={"w": (0.4, 0.78)}, avoid=("dining", "kids", "toddler", "folding", "outdoor", "bar", "counter"), with_role="desk")
    p.pick("desk lamp", ("table_lamp",), size={"h": (0.28, 0.75)}, with_role="desk")
    if area >= 8:
        p.pick("bookcase", ("bookshelf",), size={"w": (0.5, 1.4), "h": (1.0, min(2.3, p.height - 0.1))})
    _rug(p, desk.w if desk else 1.4, 1.4, "desk")
    # full
    if area >= 11:
        if p.pick("armchair", ("armchair",), level=1, size={"w": (0.55, 1.0)}, accent=True, weight=1.5):
            p.pick("side table", ("side_table",), level=1, size={"w": (0.3, 0.6), "h": (0.38, 0.7)}, with_role="armchair")
            p.pick("floor lamp", ("floor_lamp",), level=1, size={"h": (1.25, 2.0)}, with_role="armchair")
    p.pick("picture", ("wall_art",), level=1, size={"w": (0.5, 1.3), "h": (0.3, 1.2)}, accent=True)
    p.pick("plant", ("plant",), level=1, size={"h": (0.8, 2.2)})
    p.pick("desk decoration", ("decor",), level=1, size={"long": (0.06, 0.3), "h": (0.05, 0.4)}, accent=True, with_role="desk")
    # lavish
    if area >= 15:
        p.pick("sofa", ("loveseat", "sofa"), level=2, size={"w": (1.2, 1.95)}, weight=1.5)
    if area >= 13:
        p.pick("cabinet", ("cabinet",), level=2, size={"w": (0.7, 1.6), "h": (0.6, 1.2)})
    p.pick("second picture", ("wall_art",), level=2, size={"w": (0.4, 1.2), "h": (0.3, 1.2)}, accent=True)
    p.pick("ceiling light", ("ceiling_light",), level=2, size={"h": (0.1, max(p.height - 1.97, 0.12))})


def _entry(p: _Picker) -> None:
    console = p.pick("console", ("console_table",), size={"w": (0.7, max(min(1.6, p.wall - 0.4), 0.8)), "d": (0.2, 0.48)}, weight=2.5)
    if console:
        p.pick("mirror over the console", ("mirror",), size={"w": (0.35, console.w), "h": (0.3, _over(p, console, 1.25))}, with_role="console")
        p.pick("console lamp", ("table_lamp",), size={"h": (0.3, 0.8)}, with_role="console")
        p.pick("console decoration", ("decor",), level=1, size={"long": (0.08, 0.35), "h": (0.05, 0.5)}, accent=True, with_role="console")
    p.pick("runner", ("rug",), level=1, size={"short": (0.5, min(1.1, p.short - 0.4)), "long": (1.4, max(min(3.2, p.long - 0.4), 1.5))})
    p.pick("plant", ("plant",), level=1, size={"h": (0.8, 2.0)})
    if p.area >= 8:
        p.pick("bench", ("bench",), level=2, size={"w": (0.8, 1.4), "h": (0.3, 0.6)})
    p.pick("picture", ("wall_art",), level=2, size={"w": (0.4, 1.0), "h": (0.3, 1.1)}, accent=True)


def _kids(p: _Picker) -> None:
    area = p.area
    bed = p.pick("bed", ("bed", "crib"), size={"w": (0.6, 1.5), "d": (1.2, 2.3)}, weight=2.5)
    p.pick("bedside table", ("nightstand",), size={"w": (0.3, 0.6), "h": (0.3, 0.7)}, with_role="bed")
    p.pick("bedside lamp", ("table_lamp",), size={"h": (0.25, 0.7)}, with_role="bedside table")
    _rug(p, bed.w if bed else 1.0, 1.0, "bed", around=0.4)
    if area >= 9:
        if p.pick("desk", ("desk",), size={"w": (0.7, 1.4)}):
            p.pick("desk chair", ("chair",), size={"w": (0.3, 0.7)}, with_role="desk")
    p.pick("bookcase", ("bookshelf", "cabinet"), level=1, size={"w": (0.5, 1.3)})
    p.pick("picture over the bed", ("wall_art",), level=1, size={"w": (0.3, 1.2), "h": (0.25, _over(p, bed, 1.0))}, accent=True, with_role="bed")
    p.pick("toy chest", ("dresser", "cabinet"), level=2, size={"w": (0.6, 1.3)})
    p.pick("second picture", ("wall_art",), level=2, size={"w": (0.3, 1.0), "h": (0.25, 1.0)}, accent=True)
    p.pick("ottoman", ("ottoman",), level=2, size={"long": (0.35, 0.9)}, accent=True)


def _outdoor(p: _Picker) -> None:
    area = p.area
    sofa = p.pick("sofa", ("sofa", "loveseat", "sectional"), size={"w": (1.2, max(min(2.6, p.wall - 0.3), 1.3))}, weight=2.5)
    p.pick("coffee table", ("coffee_table", "side_table"), size={"long": (0.4, 1.4), "h": (0.2, 0.6)}, with_role="sofa")
    p.pick("lounge chair", ("armchair", "chair"), count=2 if area >= 12 else 1, size={"w": (0.5, 1.0)})
    p.pick("planter", ("plant",), level=1, count=2 if area >= 14 else 1, size={"h": (0.4, 2.0)})
    if area >= 16:
        if p.pick("dining table", ("dining_table",), level=2, size={"w": (0.8, 1.9)}):
            p.pick("dining chair", ("chair",), count=4, level=2, size={"w": (0.4, 0.7)})
    del sofa


_RECIPES = {"living": _living, "bedroom": _bedroom, "dining": _dining, "office": _office, "entry": _entry, "kids": _kids, "outdoor": _outdoor}


def plan(
    twin: PropertyTwin, room: Room, look: Look, level: str = "full", use: str | None = None, keep: list[InventoryItem] | None = None,
    avoid: tuple[str, ...] = (), seed: int = 0,
) -> Plan:
    """Choose the pieces for a room. `use`: what the room is furnished as (one of USES), when its
    name on the plan is wrong or says nothing. `keep`: the family's own pieces to plan the room
    round: each fills the role of its kind and nothing is chosen in its place."""
    use = use if use in USES else use_of(room.label)
    if use == "fitted":
        return Plan("fitted", look, level, note=f"{room.label} is a fitted room (a kitchen, a bathroom, storage): it is not furnished from the library")
    if use is None:  # an unnamed room: by its size
        use = "living" if (room.area_m2 or 0) >= 14 else "office"
    lib = library()
    if not lib.pieces:
        return Plan(use, look, level, note="the furniture library is empty on this server")
    if (room.area_m2 or 0) < 3.5:
        return Plan(use, look, level, note=f"{room.label} is too small to furnish")
    picker = _Picker(lib, twin, room, use, look, level, list(keep or []), avoid, seed)
    _RECIPES[use](picker)
    for mine in (i for items in picker.own.values() for i in items):  # what they keep that no role asked for still stays
        picker.plan.chosen.append(Chosen(mine.label, None, 1, mine))
    if not picker.plan.chosen:
        picker.plan.note = "the library has nothing that fits this room"
    return picker.plan
