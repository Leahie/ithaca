"""Objaverse 1.0 as a collection of the furniture library: writes artifacts/items.objaverse.jsonl.gz.

    PYTHONPATH=. uv run --quiet --no-project --with-requirements services/api/requirements-b.txt \
        --with-requirements services/api/twin/requirements.txt python -m services.api.library.build.objaverse

That is enough while every thumbnail has been looked at before (the answers are in the cache). A
first run, or one that finds new pieces, also needs the picture model that build/embed.py uses:

    PYTHONPATH=. uv run --quiet --no-project --python 3.12 \
        --with-requirements services/api/requirements-b.txt --with-requirements services/api/twin/requirements.txt \
        --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
        --with torch --with open_clip_torch --with transformers --with sentencepiece \
        python -m services.api.library.build.objaverse

Objaverse (https://huggingface.co/datasets/allenai/objaverse) is about 800,000 models that people
published on Sketchfab under Creative Commons licences, each with Sketchfab's own record: title,
tags, licence, likes, face count, pictures. Very little of it is furniture, and much of what
Sketchfab files under "furniture-home" is whole rooms, packs of props, kitchen tools and game
scenery. So a model gets into the index only when all of this holds:

    its licence lets anyone use it, commercially too (CC0, CC BY, CC BY-SA)
    it is not animated, not age restricted, and has between 1,000 and 300,000 faces
    its title names ONE home piece ("Oak coffee table"); a title that names a room, a set, two
        things, a part of a thing ("chair leg"), a scan, a toy or a game prop is left out
    nothing contradicts the title: not Sketchfab's section, not the tags, not the LVIS annotation
        that Objaverse ships for some models
    it has enough likes for its class (MIN_LIKES): the proxy for "someone found this good"
    its thumbnail still loads, and shows one thing, not several (see "pictures" below)

A piece's size is not known without its model, so every line carries the typical size of its
class and "approx": true (the app scales the model by its own proportions when it is added), and
"auto": false: which way a model faces is not known either, so the designer never picks these
by itself. People add them by hand from the menu and turn them.

The metadata (160 files, about 580 MB) and the thumbnails of the pieces with enough likes (256 px,
about 20 MB) are downloaded once into data/library/objaverse/ and only ever parsed. No 3D model is
downloaded here: library/objaverse.py fetches one when a piece is added.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import lru_cache

import httpx

from services.api.integrations.config import REPO_ROOT
from services.api.library import ARTIFACTS, CATEGORIES
from services.api.objects.classes import TYPICAL_SIZE_M

REPO = "https://huggingface.co/datasets/allenai/objaverse/resolve/main"
HEADERS = {"User-Agent": "furniture-library-fetch/0.1"}
CACHE = REPO_ROOT / "data" / "library" / "objaverse"
OUT = ARTIFACTS / "items.objaverse.jsonl.gz"
SHARDS = 160  # metadata/000-000.json.gz .. 000-159.json.gz, 5,000 models each

LICENSES = {"by": "CC BY 4.0", "by-sa": "CC BY-SA 4.0", "cc0": "CC0"}  # Sketchfab's key -> what a card shows
MIN_FACES, MAX_FACES = 1_000, 300_000  # fewer is a box with a picture on it; more is too heavy for a whole house
MAX_GLB_MB = 60  # library/objaverse.py refuses a heavier file, so it is not listed either
PER_MAKER = 10  # at most this many pieces of one class from one Sketchfab account: one maker's line does not fill a section
NAME_MAX = 48
THUMB_PX = 256

# The fewest likes a piece of a class needs. On contact sheets, about one piece in four with a
# single like is an exercise, a blank white model or something odd; with two likes, one in ten.
# Chairs are so many that they can ask for more. Floor and ceiling lamps are few, and theirs with
# one like are mostly lamp makers' own models of real lamps (16 good of 19 looked at), so they stay.
MIN_LIKES: dict[str, int] = {"chair": 3, "floor_lamp": 1, "ceiling_light": 1}
DEFAULT_MIN_LIKES = 2

# Menu section of each class, as build/abo.py and build/polyhaven.py have it (a crib is a
# children's piece whatever its title says). Seating and tables that the title or tags put
# outdoors go to "outdoor", children's pieces to "kids" (section()).
SECTION = {
    "sofa": "sofas", "loveseat": "sofas", "sectional": "sofas", "armchair": "armchairs", "ottoman": "armchairs",
    "chair": "chairs", "stool": "chairs", "bench": "chairs",
    "coffee_table": "tables", "side_table": "tables", "dining_table": "tables", "console_table": "tables",
    "desk": "desks", "bed": "beds", "nightstand": "beds", "crib": "kids",
    "bookshelf": "storage", "cabinet": "storage", "dresser": "storage", "wardrobe": "storage", "tv_stand": "storage",
    "floor_lamp": "lighting", "table_lamp": "lighting", "ceiling_light": "lighting",
    "rug": "rugs", "wall_art": "wall", "mirror": "wall", "plant": "plants", "decor": "decor",
}
_SEATS_AND_TABLES = {"sofa", "loveseat", "sectional", "armchair", "chair", "stool", "bench", "ottoman",
                     "coffee_table", "side_table", "dining_table"}

# --- what a title says ---------------------------------------------------------------------------
#
# A title is read the way a person reads a label: the piece is the last thing it names ("table
# lamp" is a lamp, "lamp table" a table), and the words before it only describe ("Vasco oak coffee
# table"). Phrases are matched longest first. A kind that begins with "?" is settled by the tags
# or the annotation (_SETTLE), or the piece is left out: a plain "Table" may be 50 cm or 2 m wide.

_KINDS: dict[str, tuple[str, ...]] = {
    "sofa": ("sofa", "couch", "settee", "divan", "chesterfield", "daybed", "sofa bed", "sofabed", "futon",
             "three seater sofa", "3 seater sofa", "chesterfield sofa"),
    "loveseat": ("loveseat", "love seat", "two seater sofa", "2 seater sofa", "chaise longue", "chaise lounge"),
    "sectional": ("sectional", "sectional sofa", "sectional couch", "corner sofa", "corner couch", "l shaped sofa", "l shape sofa",
                  "modular sofa"),
    "armchair": ("armchair", "arm chair", "recliner", "recliner chair", "lounge chair", "club chair", "wingback chair", "wingback",
                 "wing chair", "easy chair", "rocking chair", "accent chair", "egg chair", "tub chair", "barrel chair", "bergere",
                 "bean bag", "beanbag", "bean bag chair", "papasan chair", "deck chair", "deckchair", "adirondack chair",
                 "butterfly chair", "relax chair"),
    "chair": ("chair", "dining chair", "office chair", "desk chair", "gaming chair", "swivel chair", "folding chair", "kitchen chair",
              "side chair", "high chair", "highchair", "windsor chair", "bistro chair", "cafe chair", "bar chair"),
    "stool": ("stool", "bar stool", "barstool", "tabouret", "taburet", "piano stool", "counter stool"),
    "ottoman": ("ottoman", "pouf", "pouffe", "footstool", "foot stool", "hassock"),
    "bench": ("bench", "garden bench", "park bench", "piano bench", "bench seat"),
    "coffee_table": ("coffee table", "coffeetable", "cocktail table", "tea table"),
    "side_table": ("side table", "sidetable", "end table", "accent table", "occasional table", "lamp table"),
    "nightstand": ("nightstand", "night stand", "bedside table", "bed side table", "bedside cabinet", "night table", "bedside"),
    "dining_table": ("dining table", "diningtable", "dinner table", "dinning table", "kitchen table", "dining room table", "picnic table",
                     "garden table", "patio table", "outdoor table"),
    "console_table": ("console table", "sofa table", "hall table", "hallway table", "entry table"),
    "?table": ("table",),
    "desk": ("desk", "writing desk", "computer desk", "office desk", "study desk", "work desk", "gaming desk", "standing desk",
             "secretary desk", "writing table", "computer table", "office table", "study table"),
    "tv_stand": ("tv stand", "tvstand", "tv table", "tv cabinet", "tv unit", "tv console", "tv bench", "tv board", "tv shelf",
                 "television stand", "media console", "media unit", "entertainment center"),
    "bookshelf": ("bookshelf", "book shelf", "bookcase", "book case", "bookshelves", "shelf", "shelves", "shelving", "shelving unit",
                  "shelf unit", "etagere"),
    "cabinet": ("cabinet", "cupboard", "sideboard", "credenza", "buffet", "hutch", "display cabinet", "china cabinet", "vitrine",
                "filing cabinet", "file cabinet", "storage cabinet", "bar cabinet"),
    "dresser": ("dresser", "chest of drawers", "drawer chest", "drawers", "tallboy", "chiffonier"),
    "wardrobe": ("wardrobe", "armoire"),
    "bed": ("bed", "bunk bed", "bunkbed", "double bed", "single bed", "bed frame", "bedframe", "canopy bed", "four poster bed",
            "king bed", "queen bed", "king size bed", "queen size bed"),
    "crib": ("crib", "baby crib", "baby cot", "baby bed", "baby cradle", "bassinet"),
    "floor_lamp": ("floor lamp", "floorlamp", "standing lamp", "torchere", "torchiere", "tripod lamp", "arc lamp"),
    "table_lamp": ("table lamp", "tablelamp", "desk lamp", "desklamp", "bedside lamp", "bed lamp", "night lamp", "nightstand lamp",
                   "reading lamp", "study lamp", "banker lamp", "bankers lamp", "tiffany lamp", "mushroom lamp", "lava lamp",
                   "table light", "desk light"),
    "ceiling_light": ("chandelier", "ceiling lamp", "ceiling light", "pendant lamp", "pendant light", "hanging lamp", "hanging light",
                      "suspension lamp"),
    "?lamp": ("lamp",),
    "rug": ("rug", "carpet", "area rug", "kilim"),
    "wall_art": ("wall art", "picture frame", "photo frame", "framed picture", "framed painting", "framed print", "framed art",
                 "wall picture", "wall clock", "wallclock", "tapestry", "wall decor", "wall decoration", "wall hanging"),
    "?painting": ("painting", "oil painting", "canvas painting"),
    "mirror": ("mirror", "wall mirror", "floor mirror", "standing mirror", "vanity mirror", "dressing mirror", "full length mirror"),
    "plant": ("potted plant", "plant pot", "pot plant", "house plant", "houseplant", "indoor plant", "office plant", "decorative plant",
              "interior plant", "flower pot", "flowerpot", "planter", "plant in pot", "plant in a pot", "flower in pot",
              "flowers in pot", "flower in a pot", "cactus in pot", "cactus in a pot", "tree in pot", "palm in pot", "potted palm",
              "potted cactus", "potted flower", "potted flowers", "potted tree", "potted fern", "potted succulent", "bonsai",
              "bonsai tree", "monstera", "ficus", "snake plant"),
    "?plant": ("plant", "cactus", "fern", "palm", "aloe", "aloe vera", "orchid", "succulent"),
    "decor": ("vase", "flower vase", "vase with flowers", "flowers in vase", "flowers in a vase", "candle", "candlestick",
              "candle stick", "candle holder", "candleholder", "candelabra", "candelabrum", "table clock", "mantel clock",
              "desk clock", "alarm clock", "hourglass", "hour glass", "cushion", "pillow", "throw pillow", "decorative pillow",
              "basket", "wicker basket", "laundry basket", "storage basket", "desk globe", "terrarium", "bookend"),
    "?clock": ("clock",),
}
# Names that hold a furniture word but are not home pieces: fixtures, street furniture, places of
# work, parts, games. Matching one settles the title: the model is left out.
_NOT_A_PIECE = (
    "electric chair", "dentist chair", "dental chair", "barber chair", "salon chair", "tattoo chair", "torture chair", "wheel chair",
    "school chair", "pilot chair", "cockpit chair", "captain chair", "car seat", "chair lift", "chair leg", "chair mat", "throne chair",
    "chair conformation", "step stool", "stool sample", "workbench", "work bench", "weight bench", "bench press", "bench vise",
    "bench grinder", "bus bench", "pool table", "billiard table", "billiards table", "billard table", "snooker table",
    "operating table", "surgery table", "surgical table", "autopsy table", "massage table", "ping pong table", "table tennis",
    "tennis table", "poker table", "gambling table", "card table", "game table", "gaming table", "chess table", "crafting table",
    "enchanting table", "periodic table", "work table", "worktable", "workshop table", "changing table", "dressing table",
    "serving table", "lab table", "drafting table", "drawing table", "table saw", "table cloth", "tablecloth", "table runner",
    "table football", "table hockey", "table leg", "table top", "tabletop", "table mat", "table fan", "table setting",
    "table number", "table bell", "table trolley", "turn table", "school desk", "reception desk", "front desk", "help desk",
    "information desk", "desk fan", "desk organizer", "desk organiser", "desk mat", "desk pad", "desk calendar", "desk bell",
    "desk toy", "desk decor", "desk name", "wall shelf", "wall shelves", "floating shelf", "shelf bracket", "shelf plate",
    "kitchen cabinet", "kitchen cabinets", "kitchen cupboard", "kitchen drawers", "kitchen drawer", "bathroom cabinet", "medicine cabinet", "arcade cabinet",
    "server cabinet", "electrical cabinet", "electric cabinet", "tool cabinet", "gun cabinet", "weapon cabinet", "fire cabinet",
    "cabinet door", "cabinet handle", "cabinet knob", "cabinet veterinar", "hospital bed", "flower bed", "flowerbed", "dog bed",
    "cat bed", "pet bed", "truck bed", "tanning bed", "bed sheet", "bedsheet", "bed cover", "bed linen", "bed rock", "bed form",
    "prison bed", "camp bed", "street lamp", "streetlamp", "lamp post", "lamppost", "street light", "streetlight", "traffic light",
    "oil lamp", "gas lamp", "kerosene lamp", "paraffin lamp", "magic lamp", "genie lamp", "aladdin lamp", "wall lamp", "wall light",
    "sconce", "lampshade", "lamp shade", "head lamp", "headlamp", "lamp holder", "lamp socket", "lamp base", "garden lamp",
    "ceiling fan", "magic carpet", "flying carpet", "red carpet", "carpet tile", "carpet tiles", "carpet plank", "rearview mirror",
    "rear view mirror", "side mirror", "car mirror", "hand mirror", "wing mirror", "pocket mirror", "mirror ball", "mirror edge",
    "mirror s edge", "mirrors edge", "funhouse mirror", "power plant", "nuclear plant", "water plant", "plant cell", "plant stand",
    "piranha plant", "carnivorous plant", "grandfather clock", "clock tower", "cuckoo clock", "pillow fight", "shopping basket",
    "picnic basket", "basket ball", "candle light", "candle flame", "wall candle", "wall candlestick", "wall candelabra",
    "painting easel", "painting tripod", "paint brush", "couch potato",
)
PHRASES: dict[tuple[str, ...], str | None] = {tuple(p.split()): kind for kind, phrases in _KINDS.items() for p in phrases}
PHRASES.update({tuple(p.split()): None for p in _NOT_A_PIECE})
_PHRASE_MAX = max(len(p) for p in PHRASES)
# A plural says "several": "Chairs", "Vases". ("shelves" and "drawers" are one piece, and are in PHRASES as they are.)
_PLURALS = {w + "s" for (w,) in (p for p in PHRASES if len(p) == 1)} | {"benches", "couches", "shelfs", "pots", "flowerpots"}
_PLURALS -= {"shelves", "drawers", "bookshelves"}

# Words anywhere in a title that say it is more than one thing, or a place.
_MANY = frozenset((
    "pack set sets kit kits collection collections bundle assets props scene scenes diorama environment interior interiors exterior "
    "modular pieces pcs pair duo trio group various assorted multiple several kitbash furnitures objects items stuff "
    "room rooms house home apartment flat office kitchen bathroom bedroom livingroom studio cafe restaurant bar shop store "
    "classroom school hospital library hotel garage building street city village town castle church tavern level map world corner "
    "workspace workstation setup"
).split())
# A place is fine as the word right before the piece ("office chair", "living room sofa", "kitchen table").
_PLACES = frozenset("room house home apartment flat office kitchen bedroom livingroom studio cafe restaurant bar hotel corner".split())
# Words that put a piece outside a home, outside our time, or outside good repair: game scenery,
# toys, exercises, public and work places.
_UNWELCOME = frozenset((
    "broken destroyed damaged ruined burnt burned bloody bloodied blood rusty rusted rust rotting dirty abandoned wrecked torn trash "
    "trashed garbage decaying melted cracked horror horrible creepy haunted scary spooky halloween zombie apocalypse apocalyptic "
    "postapocalyptic scifi sci fi cyberpunk steampunk futuristic spaceship alien fantasy medieval dungeon viking pirate magic magical "
    "wizard witch alchemist voxel minecraft lego roblox pixel cartoon toon stylized stylised handpainted anime chibi isometric "
    "toy toys doll dolls dollhouse barbie miniature mini tiny giant inflatable cardboard printable "
    "fnaf sims sims4 simpsons spongebob mario zelda pokemon potter hogwarts dumbledore batman disney fortnite veggietales matrix "
    "portal skyrim overwatch fallout stalker neighbor clone wars holo "
    "electric hospital medical prison jail military army tactical throne pharaoh coffin grave weapon gun character creature monster "
    "skull cinema theater theatre airport lab laboratory train station subway bus stadium university classroom salon dentist dj "
    "fire hose sink vending arcade warehouse terminal virtual tiltbrush mine mining miner "
    "scan scanned 3dscan photoscan photogrammetry lidar polycam scaniverse realitycapture metashape "
    "hw xyz homework draft drafts lesson exercise tutorial tuto practice test tests wip sketch sketchbook week wk "
    "challenge inktober proptober 3december template experience installation vs versus hpvslp"
).split())
# The same, as it shows in tags: one regular expression over the tags joined by spaces. ("scandinavian"
# is not a scan, and "rustic" is not rusty.)
_UNWELCOME_TAGS = re.compile(
    r"scan(s|ned|ner|ning|iverse)?\b|photogram|realitycapture|reality-capture|metashape|agisoft|polycam|lidar|objectcapture|object-capture"
    r"|zephyr|meshroom|trnio|qlone|\bkiri|\bluma|\bnerf\b|recap|3dcreator|displayland|capture|3d-?print|printable|tilt-?brush|gravity-?sketch"
    r"|voxel|minecraft|\blego\b|roblox|pixel|cartoon|\btoon|styliz|stylis|handpaint|hand-paint|\banime\b|chibi|isometric|diorama"
    r"|fnaf|\bsims|fantasy|medieval|dungeon|sci-fi|scifi|cyberpunk|steampunk|futuristic|horror|creepy|haunted|halloween|zombie"
    r"|apocalyp|abandoned|destroyed|broken|damaged|\bruin|bloody|\brust(y|ed)?\b|\bdirty\b|\btrash\b|garbage|wreck|hospital|medical"
    r"|weapon|\bgun\b|character|creature|monster|vehicle|spaceship|\balien|dollhouse|\bdoll\b|\btoys?\b|miniature"
    r"|harry-?potter|star-?wars|hello-?neighbor|veggietales|fallout|skyrim|stalker"
    r"|(?:^| )(?:sets?|packs?|collection|kit|bundle|scene|workspace)(?: |$)"
)
# Right after the piece's name: words that make it a part of the piece or another thing
# altogether ("chair leg", "sofa cushion", "vase texture", "shelf plate").
_PART = frozenset((
    "leg legs shade cloth cushion cushions sheet sheets organizer organiser mat mats ball tennis press post door doors handle handles "
    "knob knobs saw fan lift top texture textures material materials cover covers part parts piece base seat seats back arm arms bulb "
    "holder stand rack hook pad runner fountain game games football hockey mount switch cord socket shop store factory frame frames "
    "generator maker tool tools machine pattern patterns icon icons logo plate tile tiles plank box name plaque sign bell panel "
    "hinge pole supports support bracket divider power supply robot drums microphone sculpture lantern concept project display "
    "assembly type lights study studies depth bottom"
).split())
# Sketchfab's own sections that a home piece is never filed under (history is museum scans).
_WRONG_SECTION = frozenset((
    "characters-creatures people animals-pets cars-vehicles weapons-military food-drink news-politics sports-fitness places-travel "
    "music cultural-heritage-history"
).split())
# Sections that go with a plain title when "furniture-home" is not one of them. People file a
# sofa under architecture or a lamp under gadgets, or under nothing; under science or abstract
# art a "chair" is a molecule or a sculpture.
_HOME_SECTIONS = frozenset(("architecture", "electronics-gadgets", "fashion-style"))
# "with ..." is fine when what follows is part of the piece.
_WITH_OK = frozenset((
    "drawer drawers shelf shelves door doors cushion cushions pillow pillows armrest armrests arms wheels casters castors storage "
    "mattress headboard canopy shade lampshade glass top legs stand frame backrest back footrest ottoman texture textures pbr lod lods "
    "materials uv uvs colors colours plant plants flower flowers tree soil candle candles bulb light lights cloth blanket fabric cover "
    "quilt bedding books pattern tassels fringe handle handles lid hutch mirror a an the and two three four five six"
).split())
# Words that describe, never name: colours and materials. "black and white rug" is one rug.
_DESCRIBES = frozenset((
    "black white red green blue yellow orange pink purple brown grey gray beige cream ivory gold golden silver bronze brass copper "
    "chrome wood wooden oak walnut pine teak bamboo rattan wicker metal metallic steel iron glass marble stone concrete leather "
    "fabric velvet linen cotton wool plastic ceramic dark light"
).split())
# Title words that say nothing about the piece. They are dropped from what a card shows.
_NOISE_PHRASES = (
    "free download", "free model", "free 3d model", "3d model", "3d asset", "low poly", "low-poly", "lowpoly", "high poly", "high-poly",
    "highpoly", "mid poly", "mid-poly", "midpoly", "game ready", "game-ready", "gameready", "game asset", "game-asset", "gameasset",
    "game prop", "real time", "real-time", "realtime", "public domain", "creative commons", "vr ready", "ar ready", "for games",
    "for game", "for free", "for download", "substance painter", "3ds max", "photorealistic", "pbr textures", "pbr textured",
)
_NOISE = re.compile("|".join(rf"(?<![A-Za-z]){re.escape(p)}(?![A-Za-z])" for p in _NOISE_PHRASES), re.I)
_NOISE_WORDS = frozenset((
    "free freebie pbr 3d model asset prop download downloadable textured untextured rigged optimized optimised 4k 2k 8k 1k hd uhd vr "
    "ar ue4 ue5 unity unreal blender maya c4d cc0 final version ver obj fbx glb gltf stl dae zip hp lp lod lods sketchfab new "
    "updated update furniture realistic render design gamedev uv uvs uvw unwrapped retopo"
).split())
_NOISE_TAGS = re.compile(
    r"^(3d|3d-?models?|3d-?art|3d-?design|3d-?assets?|models?|modell?ing|free|free.*|download.*|blender.*|b3d|maya.*|3ds-?max.*|max|c4d"
    r"|cinema4d|zbrush|substance.*|painter|marmoset.*|photoshop|sketchup|unity.*|unreal.*|ue4|ue5|godot|game.*|low-?poly.*|low|poly"
    r"|high-?poly|mid-?poly|pbr.*|assets?|props?.*|textur.*|uv.*|render.*|obj|fbx|glb|gltf|stl|vr|ar|4k|2k|8k|hd|cc0|cc-by"
    r"|realtime|real-time|optimized|ready|archviz|cgi|cg|art|design|object|stuff|noai|sketchfab.*|.*challenge.*|xyz.*|draft.*|homework)$"
)
# Makers whose name in a title is not part of what the piece is called: it goes to `brand`.
_BRANDS = {"ikea": "IKEA", "vitra": "Vitra", "jysk": "JYSK", "nitori": "Nitori", "flexlux": "Flexlux", "kartell": "Kartell",
           "muuto": "Muuto", "minotti": "Minotti", "poliform": "Poliform", "thonet": "Thonet"}


_NOT_LATIN = re.compile(r"[^\x20-\x7e\u00c0-\u024f]+")  # anything but Latin letters, digits and plain punctuation
_PARTS = r"\s[-–—]\s|[|:;/()\[\]•·~]"  # what cuts a title into parts: "Sofa - Low Poly", "Prop: Candleholder", "Pillow | Black"


def tokens(text: str) -> list[str]:
    """A title or a tag as lowercase words: "WoodenChair_01" -> wooden chair 01."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text or "")
    return re.findall(r"[a-z]+|\d+[a-z]*", text.lower().replace("hand painted", "handpainted").replace("living room", "livingroom")
                      .replace("post-apocalyptic", "postapocalyptic").replace("'s", ""))


def _matches(words: list[str]) -> list[tuple[int, int, str | None]]:
    """The phrases of PHRASES in a run of words, longest first, left to right: (start, end, kind)."""
    found, k = [], 0
    while k < len(words):
        for size in range(min(_PHRASE_MAX, len(words) - k), 0, -1):
            phrase = tuple(words[k:k + size])
            if phrase in PHRASES:
                found.append((k, k + size, PHRASES[phrase]))
                k += size - 1
                break
        k += 1
    return found


def _joins_two(part: list[str], k: int) -> bool:
    """Is the "and" at k between two things ("desk and chair"), not two colours ("black and white rug")?"""
    return not (0 < k < len(part) - 1 and part[k - 1] in _DESCRIBES and part[k + 1] in _DESCRIBES)


def read_title(title: str) -> tuple[str | None, bool]:
    """What one piece a title names: (kind, readable). kind is a class, a "?kind" for the tags to
    settle, or None when it names none. readable is False when the title says the model is not
    one welcome home piece (a set, a room, a part, a toy, an exercise): then it is left out
    whatever else is known about it."""
    title = _NOISE.sub(" ", title or "")
    parts = [tokens(part) for part in re.split(_PARTS + "|,", title)]
    if re.search(r"[&+]", title) or any(w in _UNWELCOME or w in _PLURALS for part in parts for w in part):
        return None, False
    kinds: list[str] = []
    for part in parts:
        # What comes after these says where, whose or what for, not what it is.
        for stop in ("for", "by", "from", "of"):
            if stop in part and not any(m[0] <= part.index(stop) < m[1] for m in _matches(part)):
                part = part[:part.index(stop)]
        found = _matches(part)
        if any(kind is None for _, _, kind in found):
            return None, False
        inside = {k for start, end, _ in found for k in range(start, end)}
        loose = [(k, w) for k, w in enumerate(part) if k not in inside]
        if any(w in ("and", "n", "plus") and _joins_two(part, k) for k, w in loose):
            return None, False
        if not found:
            if any(w in _MANY for _, w in loose):
                return None, False
            continue
        if len(found) > 1:  # "Desk lamp table": two things, unless they are one phrase (PHRASES has those)
            return None, False
        start, end, kind = found[0]
        for k, w in loose:
            if w in ("in", "on", "inside", "under", "near", "at", "next", "beside"):
                return None, False
            if w in ("with", "w"):
                if k < start:
                    return None, False  # "Wine barrels with shelf": the piece is only what comes with something else
                if any(t not in _WITH_OK and t not in _DESCRIBES and t not in _NOISE_WORDS and not t[0].isdigit() for t in part[k + 1:]):
                    return None, False
                break
            # A place may only stand before the piece, as what it is for: "old kitchen stool".
            if w in _MANY and not (k < start and w in _PLACES):
                return None, False
            if k >= end and w in _PART:
                return None, False
        if sum(1 for k, w in loose if k >= end and w not in _NOISE_WORDS and w not in _DESCRIBES and not w[0].isdigit()) > 2:
            return None, False  # a long tail after the name: the name was not the point of the title
        kinds.append(kind)
    if len(set(kinds)) > 1:
        return None, False
    return (kinds[0] if kinds else None), True


# The LVIS annotation Objaverse ships for about 47,000 models -> kind. The annotations are not
# clean enough to stand alone (a "desk" may be a whole workspace), so one only counts together
# with the title or the tags.
LVIS: dict[str, str] = {
    "armchair": "armchair", "recliner": "armchair", "rocking_chair": "armchair", "beanbag": "armchair", "deck_chair": "armchair",
    "chair": "chair", "folding_chair": "chair", "highchair": "chair", "stool": "stool", "music_stool": "stool",
    "footstool": "ottoman", "ottoman": "ottoman", "bench": "bench", "sofa": "sofa", "sofa_bed": "sofa", "futon": "sofa",
    "loveseat": "loveseat", "chaise_longue": "loveseat", "bed": "bed", "bunk_bed": "bed", "crib": "crib",
    "coffee_table": "coffee_table", "dining_table": "dining_table", "kitchen_table": "dining_table", "table": "?table", "desk": "desk",
    "bookcase": "bookshelf", "cabinet": "cabinet", "cupboard": "cabinet", "file_cabinet": "cabinet", "armoire": "wardrobe",
    "wardrobe": "wardrobe", "dresser": "dresser", "lamp": "?lamp", "table_lamp": "table_lamp", "chandelier": "ceiling_light",
    "painting": "wall_art", "tapestry": "wall_art", "wall_clock": "wall_art", "mirror": "mirror", "flowerpot": "plant",
    "vase": "decor", "candle": "decor", "candle_holder": "decor", "hourglass": "decor", "alarm_clock": "decor", "cushion": "decor",
    "pillow": "decor",
}
# Classes that are the same sort of thing. A title may be more exact than the annotation
# ("armchair" for LVIS "chair"); a title and an annotation of different sorts cancel each other.
# A nightstand is a small table to some and a small cabinet to others; a clock hangs or stands.
_SORTS = {
    "seat": "sofa loveseat sectional armchair chair stool ottoman bench",
    "table": "coffee_table side_table dining_table console_table nightstand desk ?table tv_stand",
    "store": "tv_stand bookshelf cabinet dresser wardrobe nightstand",
    "bed": "bed crib", "lamp": "floor_lamp table_lamp ceiling_light ?lamp", "wall": "wall_art ?painting ?clock", "mirror": "mirror",
    "decor": "decor ?clock", "plant": "plant ?plant", "rug": "rug",
}
_SORT: dict[str, set[str]] = {}
for _sort, _members in _SORTS.items():
    for _kind in _members.split():
        _SORT.setdefault(_kind, set()).add(_sort)
# What settles a "?kind": words of the title and the tags, each group its own class. Exactly one
# group must speak; the group "" speaks against (a street lamp, a clock tower, a tree).
_SETTLE: dict[str, dict[str, set[str]]] = {
    "?table": {
        "dining_table": {"dining", "dinner", "diningtable", "diningroom", "kitchen"},
        "coffee_table": {"coffee", "coffeetable", "cocktail"},
        "side_table": {"side", "sidetable", "endtable"},
        "nightstand": {"bedside", "nightstand", "bedroom"},
        "desk": {"desk", "office", "computer", "writing"},
        "console_table": {"console", "hallway", "entryway"},
    },
    "?lamp": {
        "floor_lamp": {"floor", "floorlamp", "standing", "torchere", "torchiere", "tripod"},
        "table_lamp": {"table", "tablelamp", "desk", "desklamp", "bedside", "nightstand", "nightlamp"},
        "ceiling_light": {"ceiling", "pendant", "chandelier", "hanging", "suspension"},
        "": {"street", "streetlamp", "wall", "sconce", "oil", "kerosene", "garden", "outdoor", "park", "lantern", "bulb"},
    },
    "?clock": {
        "wall_art": {"wall", "wallclock"},
        "decor": {"alarm", "alarmclock", "table", "desk", "mantel", "mantle"},
        "": {"tower", "grandfather", "pocket", "watch", "wrist"},
    },
    "?painting": {  # "painting" is also what people call a scene made after a painting, or a painted texture
        "wall_art": {"frame", "framed", "canvas", "wall", "artwork", "gallery", "portrait", "landscape", "picture", "walldecor"},
    },
    "?plant": {
        "plant": {"pot", "pots", "potted", "planter", "flowerpot", "houseplant", "indoor", "vase", "plantpot", "potplant", "indoorplant"},
        "": {"tree", "grass", "forest", "jungle", "garden", "wild", "nature", "weed", "bush", "field", "desert", "underwater"},
    },
}
_OUTDOOR = {"garden", "patio", "outdoor", "outdoors", "park", "terrace", "picnic", "beach", "camping", "lawn", "poolside", "backyard",
            "adirondack", "deck", "deckchair"}
_KIDS = {"kids", "kid", "children", "childrens", "child", "baby", "toddler", "nursery", "infant", "highchair"}


def classify(title: str, tags: list[str], sections: list[str], lvis: str | None) -> str | None:
    """The furniture class of a model, or None to leave it out: from its title, its tags,
    Sketchfab's section and the LVIS annotation. Nothing is guessed: a plain "Table" with no word
    for which kind of table is left out, and so is a title that disagrees with its annotation."""
    if _WRONG_SECTION & set(sections) or _UNWELCOME_TAGS.search(" ".join(t.lower() for t in tags)):
        return None
    kind, readable = read_title(title)
    if not readable:
        return None
    noted = LVIS.get(lvis or "")
    tagged = [m[2] for t in tags for m in _matches(tokens(t)) if m[2]]  # the pieces the tags name
    if kind is None:
        # The title names no piece ("Karl", "PBC200"). The annotation alone is not enough, but
        # the annotation and the tags together are: the tags must name that sort of thing and no other.
        if not noted or not tagged or not all(_SORT[k] & _SORT[noted] for k in tagged):
            return None
        kind = noted
    elif _NOT_LATIN.search(title) and not any(_SORT[k] & _SORT[kind] for k in tagged):
        # Part of the title is in a script this file cannot read, and may say it is something
        # else ("OTTOMAN" is also a maker of coffee pots): then the tags must name the piece too.
        return None
    elif noted:
        if not _SORT[kind] & _SORT[noted]:
            return None
        if kind.startswith("?") and not noted.startswith("?"):
            kind = noted  # "Table", annotated as a dining table
    elif "furniture-home" not in sections:
        # Only the title speaks: then Sketchfab's section must not say otherwise.
        allowed = _HOME_SECTIONS | {"nature-plants"} if kind in ("plant", "?plant") else _HOME_SECTIONS
        if not set(sections) <= allowed:
            return None
    if not kind.startswith("?"):
        return kind
    said = set(tokens(title)) | {w for t in tags for w in tokens(t)} | {t.lower().replace("-", "").replace("_", "") for t in tags}
    spoke = [cls for cls, cue in _SETTLE[kind].items() if said & cue]
    return spoke[0] if len(spoke) == 1 and spoke[0] else None


def section(cls: str, title: str, tags: list[str]) -> str:
    """The menu section of a piece: its class's, unless the title or tags put it in the garden or the children's room."""
    said = set(tokens(title)) | {w for t in tags for w in tokens(t)}
    if cls in _SEATS_AND_TABLES and said & _OUTDOOR:
        return "outdoor"
    if cls in _SEATS_AND_TABLES | {"bed", "desk", "bookshelf", "dresser", "wardrobe"} and said & _KIDS:
        return "kids"
    return SECTION[cls]


# --- what a card shows ---------------------------------------------------------------------------

_ONE_WORD_KINDS = {p[0] for p, kind in PHRASES.items() if len(p) == 1 and kind}


def brand_of(title: str) -> str | None:
    return next((_BRANDS[w] for w in tokens(title) if w in _BRANDS), None)


def clean_name(title: str, cls: str) -> str:
    """A title as a card shows it: the part that names the piece, without "FREE", "Low Poly",
    "Game Ready", file names, version numbers or brand, 48 characters at most. A title with no
    word left in it (a maker's code, another script) is named after its class."""
    text = _NOT_LATIN.sub(" ", title or "")
    text = re.sub(r"\.(obj|fbx|glb|gltf|blend|max|stl|3ds|dae|zip|rar|c4d|skp)\b", " ", text, flags=re.I)
    if " " not in text.strip():
        text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)  # WoodenChair
    text = _NOISE.sub(" ", text.replace("_", " "))
    # "Unseen: Fancy Mirror", "Bench - SCP", "Stool (PBR)": only the part with the piece in it.
    parts = [part for part in re.split(_PARTS, text) if part.strip()]
    naming = [part for part in parts if any(kind for _, _, kind in _matches(tokens(part)))]
    text = max(naming, key=lambda part: len(part.split())) if naming else text
    text = re.sub(r"\b[vV]\.?\d+(\.\d+)*\b|#\d+\b|\b\d+\s*(tris|triangles|polys|polygons|faces|verts|vertices)\b", " ", text, flags=re.I)
    drop = _NOISE_WORDS | set(_BRANDS)
    text = " ".join(w for w in text.split() if w.strip("-:,.!*'\"").lower() not in drop)
    text = re.sub(r"\s+(\d{1,2}|0\d+)$|(?<=[a-z]{3})\d{1,2}$", "", text.strip())  # "Chair 02", "Chair1": the maker's count
    text = re.sub(r"\s+(by|from|for|of|the|a|and)$", "", re.sub(r"\s+", " ", text).strip(" -–—:,.;/\\!*'\""), flags=re.I)
    text = re.sub(r"(?<![A-Za-z])'|'(?![A-Za-z])|\"", "", text).strip()  # quotation marks, not the apostrophe of "70's"
    # Something to read: a word of four letters with a vowel, or the piece's own name ("Bed", "Rug").
    if not any((len(w) >= 4 and re.search(r"[aeiouy]", w)) or w in _ONE_WORD_KINDS for w in re.findall(r"[a-z\u00e0-\u024f]+", text.lower())):
        return cls.replace("_", " ").capitalize()
    text = text.capitalize() if text.islower() or text.isupper() else text[0].upper() + text[1:]
    if len(text) > NAME_MAX:
        text = text[:NAME_MAX + 1].rsplit(" ", 1)[0].rstrip(" -–—:,.;/&(")[:NAME_MAX]
    return text


def clean_tags(tags: list[str]) -> list[str]:
    """Sketchfab's tags, lowercase, without the ones about software and file formats; 12 at most."""
    out: list[str] = []
    for tag in tags:
        tag = tag.strip().lower()
        if 2 <= len(tag) <= 24 and tag.isascii() and not _NOISE_TAGS.match(tag) and tag not in out:
            out.append(tag)
    return out[:12]


def thumbnail(images: list[dict]) -> str | None:
    """The picture nearest 256 px wide (Sketchfab keeps 64, 256, 720, 1024 and 1920)."""
    usable = [im for im in images if (im.get("url") or "").startswith("https://") and (im.get("width") or 0) >= 200]
    return min(usable, key=lambda im: abs(im["width"] - THUMB_PX))["url"] if usable else None


# --- the metadata --------------------------------------------------------------------------------


def download() -> None:
    """The annotation file and the 160 metadata files, into data/library/objaverse/ (once)."""
    (CACHE / "metadata").mkdir(parents=True, exist_ok=True)
    names = ["lvis-annotations.json.gz"] + [f"metadata/000-{n:03d}.json.gz" for n in range(SHARDS)]
    missing = [name for name in names if not (CACHE / name).exists()]
    if not missing:
        return
    print(f"downloading {len(missing)} metadata files (about 3.6 MB each)", file=sys.stderr)
    with httpx.Client(timeout=120, headers=HEADERS, follow_redirects=True) as http:
        def get(name: str) -> None:
            data = http.get(f"{REPO}/{name}").raise_for_status().content
            gzip.decompress(data)  # a download cut short must not be cached: this raises on one
            partial = CACHE / f"{name}.part"
            partial.write_bytes(data)
            partial.replace(CACHE / name)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(get, missing))


@lru_cache(maxsize=1)
def _lvis_by_uid() -> dict[str, str]:
    """uid -> LVIS category. Read once per process: each worker of the pool keeps its own."""
    with gzip.open(CACHE / "lvis-annotations.json.gz", "rt", encoding="utf-8") as f:
        return {uid: category for category, uids in json.load(f).items() for uid in uids}


def _scan(shard: int) -> tuple[list[dict], Counter]:
    """One metadata file: the models that pass every test but likes and the thumbnail."""
    lvis = _lvis_by_uid()
    with gzip.open(CACHE / "metadata" / f"000-{shard:03d}.json.gz", "rt", encoding="utf-8") as f:
        records = json.load(f)
    found: list[dict] = []
    why: Counter = Counter()
    for uid, r in records.items():
        why["models"] += 1
        if r.get("license") not in LICENSES:
            why["licence not open"] += 1
            continue
        if r.get("animationCount") or r.get("isAgeRestricted"):
            why["animated or age restricted"] += 1
            continue
        archive = (r.get("archives") or {}).get("glb") or {}
        faces = archive.get("faceCount") or r.get("faceCount") or 0
        if not MIN_FACES <= faces <= MAX_FACES or (archive.get("size") or 0) > MAX_GLB_MB * 1e6:
            why["too light or too heavy"] += 1
            continue
        title = (r.get("name") or "").strip()
        tags = [t.get("name") or "" for t in r.get("tags") or []]
        sections = [c.get("name") or "" for c in r.get("categories") or []]
        cls = classify(title, tags, sections, lvis.get(uid))
        if cls is None:
            why["not one home piece"] += 1
            continue
        img = thumbnail((r.get("thumbnails") or {}).get("images") or [])
        user = r.get("user") or {}
        maker = (user.get("displayName") or user.get("username") or "").strip()
        if not img or not maker or not r.get("viewerUrl"):
            why["no picture, maker or page"] += 1
            continue
        found.append({
            "uid": uid, "title": title, "cls": cls, "cat": section(cls, title, tags), "likes": r.get("likeCount") or 0,
            "views": r.get("viewCount") or 0, "faces": faces, "img": img, "tags": clean_tags(tags), "maker": maker,
            "account": user.get("username") or maker, "license": LICENSES[r["license"]], "url": r["viewerUrl"],
        })
    return found, why


def candidates() -> tuple[list[dict], Counter]:
    """Every single home piece in the metadata, and how many models each test turned away."""
    found: list[dict] = []
    why: Counter = Counter()
    with ProcessPoolExecutor() as pool:
        for some, counts in pool.map(_scan, range(SHARDS)):
            found += some
            why += counts
    return found, why


def liked(found: list[dict]) -> list[dict]:
    """The pieces with enough likes for their class."""
    return [c for c in found if c["likes"] >= MIN_LIKES.get(c["cls"], DEFAULT_MIN_LIKES)]


def spread(pieces: list[dict]) -> list[dict]:
    """No more than PER_MAKER pieces of a class from one account: its most liked."""
    kept: list[dict] = []
    per_maker: Counter = Counter()
    for c in sorted(pieces, key=lambda c: (-c["likes"], -c["views"], c["uid"])):
        if per_maker[c["account"], c["cls"]] < PER_MAKER:
            per_maker[c["account"], c["cls"]] += 1
            kept.append(c)
    return kept


# --- pictures ------------------------------------------------------------------------------------
#
# A title cannot say everything: "Sofa Pink" is two armchairs and a pouf, "Desk" a desk with a
# computer, a chair and forty small things on it, "Bookcase" the same bookcase four times. So each
# piece's thumbnail is looked at, by the picture-and-text model build/embed.py also uses (it runs
# offline, on a CPU, about ten pictures a second): does it look more like one thing, or like
# several? MANY_MARGIN was set by eye on contact sheets of the pictures in the order of this
# score. Of the 60 highest, 59 were sets, rooms or copies side by side; around -0.002 half were;
# around -0.008 one in seven; from -0.014 down, one in twelve, and those were mostly a bed with
# its two nightstands or a lounge chair with its footstool.

LOOK_MODEL = ("ViT-B-16-SigLIP2", "webli")  # open_clip's name for the model and for its weights
ONE = ("a 3D render of one single piece of furniture", "a 3D render of a single object on a plain background")
MANY = ("a 3D render of a set of several pieces of furniture", "a 3D render of a room interior", "a table with chairs around it",
        "a collection of many different objects", "several copies of the same object side by side",
        "a desk with a computer and many things on it")
MANY_MARGIN = -0.005  # a picture this much more like MANY than like ONE (cosine) is left out
LOOK_WITH = ("--python 3.12 --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match "
             "--with torch --with open_clip_torch --with transformers --with sentencepiece")


def _picture(c: dict):
    return CACHE / "thumbs" / f"{c['uid']}.jpg"


def thumbnails(pieces: list[dict]) -> list[dict]:
    """The pieces whose thumbnail still loads; the pictures are kept in the cache (thumbs/).
    The metadata is from 2022: a model taken down since, or given a new picture, answers 403 or
    404, and that is remembered (thumbnails.json) so it is asked only once."""
    (CACHE / "thumbs").mkdir(parents=True, exist_ok=True)
    known_path = CACHE / "thumbnails.json"
    known: dict[str, bool] = json.loads(known_path.read_text()) if known_path.exists() else {}
    todo = [c for c in pieces if not _picture(c).exists() and known.get(c["img"]) is not False]
    if todo:
        print(f"fetching {len(todo)} thumbnails", file=sys.stderr)
        with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as http:
            def get(c: dict) -> bool | None:
                for _ in range(2):
                    try:
                        r = http.get(c["img"])
                    except httpx.HTTPError:
                        continue
                    if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/"):
                        _picture(c).write_bytes(r.content)
                        return True
                    if r.status_code in (403, 404, 410):
                        return False
                return None  # no answer: left out of this run, asked again in the next

            with ThreadPoolExecutor(max_workers=16) as pool:
                for c, ok in zip(todo, pool.map(get, todo)):
                    if ok is not None:
                        known[c["img"]] = ok
        known_path.write_text(json.dumps(known))
    return [c for c in pieces if _picture(c).exists()]


def several(pieces: list[dict]) -> dict[str, float]:
    """How much more each piece's thumbnail looks like several things than like one: uid ->
    margin (above MANY_MARGIN it is left out). Each picture is looked at once: the answers are
    kept in the cache (pictures.json), by the picture's address."""
    known_path = CACHE / "pictures.json"
    known: dict[str, float] = json.loads(known_path.read_text()) if known_path.exists() else {}
    todo = [c for c in pieces if c["img"] not in known]
    if todo:
        try:
            import open_clip
            import torch
            from PIL import Image

            net, _, preprocess = open_clip.create_model_and_transforms(LOOK_MODEL[0], pretrained=LOOK_MODEL[1])
            tokenizer = open_clip.get_tokenizer(LOOK_MODEL[0])  # this one needs transformers and sentencepiece
        except ImportError as e:
            raise SystemExit(f"{len(todo)} thumbnails have not been looked at yet, and that needs a model ({e}). "
                             f"Run again with these added to uv run: {LOOK_WITH}") from e
        print(f"looking at {len(todo)} thumbnails", file=sys.stderr)
        net.eval()
        with torch.inference_mode():
            words = torch.nn.functional.normalize(net.encode_text(tokenizer(list(ONE + MANY))), dim=-1)
            for start in range(0, len(todo), 64):
                batch = todo[start:start + 64]
                pictures = torch.stack([preprocess(Image.open(_picture(c)).convert("RGB")) for c in batch])
                likeness = torch.nn.functional.normalize(net.encode_image(pictures), dim=-1) @ words.T
                margins = likeness[:, len(ONE):].max(dim=1).values - likeness[:, :len(ONE)].max(dim=1).values
                known.update({c["img"]: round(float(m), 4) for c, m in zip(batch, margins)})
                known_path.write_text(json.dumps(known))  # saved as it goes: a stopped run picks up where it was
    return {c["uid"]: known[c["img"]] for c in pieces}


# --- the artifact --------------------------------------------------------------------------------


def line(c: dict) -> dict:
    """A piece as a line of the artifact (services/api/library/__init__.py has the format)."""
    w, d, h = TYPICAL_SIZE_M[c["cls"]]
    return {
        "id": f"ov:{c['uid']}", "src": "objaverse", "name": clean_name(c["title"], c["cls"]), "full": c["title"],
        "brand": brand_of(c["title"]), "line": None, "cls": c["cls"], "cat": c["cat"], "w": w, "d": d, "h": h, "approx": True,
        "img": c["img"], "faces": c["faces"], "fam": c["uid"], "color": None, "tags": c["tags"],
        "license": c["license"], "credit": f"{c['maker']} on Sketchfab", "url": c["url"], "auto": False,
    }


def write(pieces: list[dict]) -> None:
    """One line per piece, sections in menu order, then by class, the most liked first. The file
    is the same for the same pieces (no time stamp inside), so git shows only real changes."""
    order = {key: n for n, (key, _) in enumerate(CATEGORIES)}
    pieces = sorted(pieces, key=lambda c: (order[c["cat"]], c["cls"], -c["likes"], c["uid"]))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as f:
        for c in pieces:
            f.write((json.dumps(line(c), ensure_ascii=False, separators=(",", ":")) + "\n").encode())


def main() -> None:
    download()
    found, why = candidates()
    for reason, count in why.most_common():
        print(f"{count:8}  {reason}")
    enough = liked(found)
    seen = thumbnails(enough)
    margin = several(seen)
    single = [c for c in seen if margin[c["uid"]] <= MANY_MARGIN]
    pieces = spread(single)
    print(f"{len(found)} single home pieces by their titles, {len(enough)} with enough likes; left out: {len(enough) - len(seen)} for a "
          f"thumbnail that is gone, {len(seen) - len(single)} that look like several things, {len(single) - len(pieces)} over {PER_MAKER} from one maker")
    write(pieces)
    for label, key in (("section", "cat"), ("class", "cls"), ("licence", "license")):
        print(f"\nby {label}:")
        for value, count in Counter(c[key] for c in pieces).most_common():
            print(f"{count:6}  {value}")
    print(f"\n{len(pieces)} pieces -> {OUT.relative_to(REPO_ROOT)} ({OUT.stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
