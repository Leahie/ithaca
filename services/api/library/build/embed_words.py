"""The fixed words of the look files: the styles and materials a piece may be tagged with, the
phrases of the vocabulary, and how a product title is read (its maker, its design line, and the
words left once those and the sizes are taken out).

Everything here is a list on purpose. The API matches free text against these phrases without a
model, so a phrase that is not listed cannot be asked for by its look, only by keyword.
"""

from __future__ import annotations

import collections
import re

# --------------------------------------------------------------------------------------------
# Styles a piece is tagged with (looks.jsonl.gz "styles"): (name, words that say it in a title,
# what it looks like, in words the model matches a picture against). The catalog's own STYLES
# names are all here, spelt the same, so a library piece and a catalog set can share a style.
STYLES: list[tuple[str, tuple[str, ...], str]] = [
    ("Mid-century modern", ("mid-century", "mid century", "midcentury"), "mid-century modern furniture with tapered wooden legs, 1950s and 1960s design"),
    ("Scandinavian", ("scandinavian", "nordic", "scandi"), "Scandinavian furniture, pale wood, white and light grey, simple and light"),
    ("Japandi", ("japandi",), "Japandi furniture, low and calm, natural wood, neutral linen, quiet and spare"),
    ("Minimalist", ("minimalist", "minimal"), "minimalist furniture, plain, bare and simple, nothing extra"),
    ("Industrial", ("industrial",), "industrial furniture, raw wood and black steel, factory loft"),
    ("Farmhouse", ("farmhouse",), "farmhouse furniture, homely planked and painted wood, country kitchen"),
    ("Rustic", ("rustic", "reclaimed", "lodge", "cabin"), "rustic furniture, rough reclaimed wood, log cabin"),
    ("Coastal", ("coastal", "nautical", "beach"), "coastal beach house furniture, whitewashed wood, rattan and pale blue"),
    ("Bohemian", ("bohemian", "boho", "moroccan", "global", "tribal", "mudcloth"), "bohemian furniture, eclectic patterns, macrame, tassels and rattan"),
    ("French country", ("french country", "french", "provincial", "cottage"), "French country furniture, curved cabriole legs, carved and painted pale wood"),
    ("Traditional", ("traditional", "classic", "charles of london"), "traditional classic furniture, rolled arms, turned legs, dark polished wood"),
    ("Hollywood regency", ("hollywood regency", "regency"), "Hollywood regency furniture, lacquer, tufted velvet and brass, bold and opulent"),
    ("Art Deco", ("art deco", "deco"), "Art Deco furniture, geometric and curved, brass, marble and velvet, 1920s"),
    ("Glam", ("glam", "glamorous", "luxe"), "glamorous furniture, velvet, mirror and gold metal"),
    ("Transitional", ("transitional",), "transitional furniture, between classic and modern, neutral upholstery"),
    ("Vintage", ("vintage", "antique", "retro", "distressed"), "vintage furniture that looks old and worn, antique patina"),
    ("Southwestern", ("southwestern", "southwest", "aztec", "navajo", "kilim"), "southwestern furniture, desert colours, woven Navajo and kilim patterns"),
    ("Kids", ("kids", "kid", "kiddie", "children", "children's", "child", "toddler", "youth", "nursery", "bunk"), "children's furniture, small, bright and playful"),
    ("Outdoor", ("outdoor", "patio", "garden", "all-weather", "all weather"), "outdoor patio and garden furniture, weatherproof"),
    ("Contemporary", ("contemporary",), "contemporary furniture, current, understated, soft shapes"),
    ("Modern", ("modern",), "modern furniture with clean straight lines"),
]
STYLE_NAMES = [name for name, _, _ in STYLES]
# Words every other title has, so they say little: a piece called "Modern Farmhouse" is Farmhouse.
LOOSE_STYLES = ("Modern", "Contemporary", "Vintage", "Traditional")

# Materials and finishes a piece is tagged with (looks.jsonl.gz "materials"): (name, words that
# say it in a title, what it looks like). A word in the title beats what the picture suggests: a
# photo cannot tell velvet from microfibre, the maker can.
MATERIALS: list[tuple[str, tuple[str, ...], str]] = [
    ("velvet", ("velvet", "velour"), "velvet upholstery with a soft sheen"),
    ("leather", ("leather", "leathersoft", "leatherette"), "leather upholstery"),
    ("linen", ("linen",), "plain woven linen fabric upholstery"),
    ("boucle", ("boucle", "bouclé", "sherpa", "teddy"), "nubby boucle fabric upholstery"),
    ("fabric", ("fabric", "upholstered", "polyester", "microfiber", "microfibre", "chenille", "tweed", "performance"), "woven fabric upholstery"),
    ("cotton", ("cotton",), "cotton cloth"),
    ("wool", ("wool",), "a thick wool pile"),
    ("jute", ("jute", "sisal", "seagrass", "hemp"), "coarse woven jute and sisal fibre"),
    ("shag", ("shag", "faux fur", "sheepskin", "plush"), "long shaggy pile, fur"),
    ("rattan", ("rattan", "wicker", "cane", "bamboo"), "woven rattan, wicker and cane"),
    ("light wood", ("oak", "ash", "birch", "beech", "pine", "maple", "natural wood", "light wood", "blond", "whitewash", "driftwood"), "pale natural wood such as oak, ash or pine"),
    ("walnut", ("walnut", "teak", "acacia", "sheesham"), "mid-brown walnut and teak wood"),
    ("dark wood", ("espresso", "mahogany", "cherry", "dark oak", "dark wood", "merlot", "ebony", "wenge"), "dark brown polished wood such as espresso, mahogany or cherry"),
    ("painted wood", ("painted", "lacquer", "lacquered", "high gloss", "high-gloss"), "painted or lacquered wood with a smooth coloured finish"),
    ("marble", ("marble", "travertine", "terrazzo"), "veined marble stone"),
    ("stone", ("stone", "concrete", "cement", "granite", "slate"), "stone or concrete"),
    ("glass", ("glass", "crystal"), "clear glass"),
    ("mirror", ("mirror", "mirrored"), "mirrored glass"),
    ("brass", ("brass", "gold finish", "gold-finish", "gold metal", "brushed gold", "gold"), "polished brass and gold metal"),
    ("bronze", ("bronze", "copper"), "dark bronze or copper metal"),
    ("chrome", ("chrome", "nickel", "stainless", "silver"), "shiny chrome, nickel and steel"),
    ("black metal", ("black metal", "iron", "steel", "wrought"), "matt black metal, iron"),
    ("ceramic", ("ceramic", "porcelain", "stoneware", "earthenware", "terracotta", "terra cotta"), "glazed ceramic and pottery"),
    ("acrylic", ("acrylic", "lucite", "plexiglass"), "clear see-through acrylic"),
    ("plastic", ("plastic", "resin", "polypropylene", "polycarbonate", "pvc"), "moulded coloured plastic"),
    ("canvas", ("canvas", "print", "poster"), "a printed canvas or paper"),
]
MATERIAL_NAMES = [name for name, _, _ in MATERIALS]
# "Metal" alone names no finish: the picture decides which of these it is.
METALS = ("brass", "bronze", "chrome", "black metal")
# "Wood" alone names no tone either.
WOODS = ("light wood", "walnut", "dark wood", "painted wood")

# --------------------------------------------------------------------------------------------
# The vocabulary: what a designer or a person may ask for, by kind. Each phrase gets a vector in
# the look space (vocab.f16.npy). Design directions ("quiet luxury") and patterns are styles;
# finishes and details ("tufted") are materials.
VOCAB: dict[str, list[str]] = {
    "style": [
        "mid-century modern", "modern", "contemporary", "minimalist", "scandinavian", "japandi", "industrial", "farmhouse",
        "modern farmhouse", "rustic", "coastal", "bohemian", "traditional", "french country", "glam", "hollywood regency",
        "art deco", "transitional", "vintage", "southwestern", "kids", "outdoor",
        "quiet luxury", "old money", "parisian apartment", "wabi-sabi", "dark academia", "modern organic", "english country house",
        "70s retro", "boutique hotel", "california casual", "cottagecore", "grandmillennial", "maximalist", "eclectic",
        "mediterranean", "tuscan villa", "spanish revival", "moroccan", "tropical", "british colonial", "chinoiserie", "shaker",
        "craftsman", "victorian", "georgian", "neoclassical", "baroque", "bauhaus", "brutalist", "postmodern", "memphis design",
        "space age", "danish modern", "italian modern", "loft", "new york loft", "ski chalet", "log cabin", "lake house", "beach house",
        "hamptons", "palm springs", "desert modern", "zen", "japanese", "biophilic", "nautical", "shabby chic", "country cottage",
        "gothic", "regency", "gentleman's club", "library", "hotel lobby", "penthouse", "warm minimalism", "soft minimalism",
        "luxe", "preppy", "retro", "antique", "heritage", "classic", "casual", "formal", "urban", "artisan", "handcrafted",
        "striped", "geometric pattern", "floral", "plaid", "abstract pattern", "persian pattern", "moroccan trellis",
        "animal print", "botanical", "distressed", "curved", "angular", "round", "low profile", "oversized", "slim",
    ],
    "material": [
        "velvet", "leather", "cognac leather", "tan leather", "black leather", "brown leather", "white leather", "faux leather",
        "suede", "linen", "boucle", "cotton", "wool", "chenille", "tweed", "sheepskin", "faux fur", "shag", "jute", "sisal",
        "seagrass", "rattan", "wicker", "cane", "bamboo", "light wood", "pale oak", "oak", "ash wood", "pine", "walnut", "teak",
        "acacia", "mahogany", "cherry wood", "dark wood", "espresso wood", "ebony", "reclaimed wood", "driftwood",
        "whitewashed wood", "painted wood", "burl wood", "live edge wood", "marble", "white marble", "black marble", "travertine",
        "stone", "concrete", "terrazzo", "glass", "smoked glass", "mirrored", "acrylic", "brass", "antique brass", "gold metal",
        "bronze", "copper", "chrome", "brushed nickel", "stainless steel", "silver metal", "black metal", "wrought iron",
        "ceramic", "porcelain", "terracotta pottery", "lacquer", "high gloss", "matte", "upholstered", "tufted", "channel tufted",
        "button tufted", "nailhead trim", "quilted", "woven", "macrame", "fringe", "tassels", "crystal", "slipcovered",
        "skirted", "tapered legs", "hairpin legs", "turned legs", "metal legs", "wood frame", "canvas print", "framed print",
        "natural wood", "gilded", "emerald velvet", "navy velvet", "blush velvet",
    ],
    "colour": [
        "white", "cream", "ivory", "beige", "tan", "camel", "taupe", "brown", "chocolate brown", "black", "charcoal", "grey",
        "light grey", "silver", "gold", "blush", "pink", "red", "burgundy", "terracotta", "rust", "orange", "mustard", "yellow",
        "olive", "sage green", "green", "forest green", "emerald", "teal", "turquoise", "aqua", "blue", "light blue",
        "cobalt blue", "navy", "indigo", "purple", "plum", "lavender", "mauve", "multicolour",
        "black and white", "black and gold", "white and gold", "navy and gold", "navy and white", "blue and white",
        "grey and white", "pink and gold", "blush and brass", "emerald and gold", "emerald and brass", "green and gold",
        "cream and brown", "cream and black", "beige and white", "black and brass", "white and wood", "black and wood",
        "grey and wood", "walnut and brass", "marble and gold", "marble and brass", "black and walnut", "navy and walnut",
        "teal and gold", "terracotta and cream", "rust and navy", "mustard and grey", "olive and tan", "sage and cream",
        "charcoal and brass", "blue and grey", "pink and grey", "green and white", "red and black", "tan and black",
        "monochrome", "neutral", "warm neutral", "cool neutrals", "earth tones", "jewel tones", "pastel", "muted colours",
        "bold colours", "bright colours", "dark colours", "light colours", "tonal", "all white", "all black", "gold accents",
        "brass accents", "black accents", "natural tones", "sandy tones", "ocean blues", "sunset colours", "autumn colours",
        "two-tone", "colourful",
    ],
    "mood": [
        "warm", "cool", "moody", "airy", "serene", "dramatic", "opulent", "cozy", "sleek", "organic", "calm", "bright", "dark",
        "light", "fresh", "earthy", "elegant", "sophisticated", "refined", "luxurious", "glamorous", "playful", "cheerful",
        "bold", "understated", "relaxed", "inviting", "intimate", "romantic", "feminine", "masculine", "edgy", "quirky",
        "whimsical", "timeless", "natural", "rugged", "raw", "polished", "soft", "plush", "textured", "clean", "crisp",
        "simple", "rich", "lush", "vibrant", "tranquil", "peaceful", "restful", "energetic", "fun", "grown-up", "youthful",
        "homely", "lived-in", "curated", "collected", "tailored", "structured", "sculptural", "chunky", "delicate",
        "lightweight", "substantial", "statement", "grand", "stately", "humble", "nostalgic", "sunny", "breezy", "beachy",
        "expensive-looking", "high-end", "budget", "comfortable", "welcoming", "sumptuous", "decadent", "spare",
    ],
}
# The room composer (library/compose.py, LOOKS) asks for these by name: they must stay, spelt so.
NEEDED = ("quiet luxury", "warm neutral", "boucle", "linen", "light wood", "brass", "art deco", "emerald velvet", "marble", "glam",
          "mid-century modern", "walnut", "tapered legs", "tufted", "japandi", "scandinavian", "minimalist", "parisian apartment",
          "french country", "traditional", "curved", "gilded", "modern organic", "organic", "natural wood", "earthy",
          "hollywood regency", "velvet", "gold", "crystal", "industrial", "leather", "black metal", "dark wood", "loft", "coastal",
          "rattan", "white", "light blue", "dark academia", "moody", "farmhouse", "rustic", "oak", "modern")
KIND_OF: dict[str, str] = {phrase: kind for kind, phrases in VOCAB.items() for phrase in phrases}
assert not [phrase for phrase in NEEDED if phrase not in KIND_OF], "the composer's phrases must all be in the vocabulary"
PHRASES: list[str] = [phrase for phrases in VOCAB.values() for phrase in phrases]

# How a phrase is put to the text model. Measured against the makers' own listing fields
# (embed_check.py mix): the bare phrase finds colours and styles best, and every "… furniture"
# or "a photo of …" around it made them worse; materials gain a little from one such wording.
# Moods have nothing to be measured against. A bare "light" or "cool" is a lamp or a fan to the
# model, so they are said of a room.
TEMPLATES: dict[str, tuple[str, ...]] = {
    "style": ("{}",),
    "material": ("{}", "{} furniture"),
    "colour": ("{}",),
    "mood": ("a {} room", "{} interior design", "{} furniture"),
}

# How high-end a piece looks: the difference between these two sets of descriptions.
LUX_HIGH = ("luxury high-end designer furniture", "an expensive, elegant, finely crafted piece of furniture",
            "premium materials and fine detail, from a luxury showroom", "bespoke furniture in a five-star hotel suite")
LUX_LOW = ("cheap budget furniture", "a plain, basic, utilitarian piece of furniture",
           "low-cost flat-pack furniture of laminate and plastic", "a flimsy bargain piece from a discount store")

# What kind of picture it is, to know which pixels are the piece (embed_colour.py).
PICTURE_KINDS = {
    "studio": ("a product photo of one piece of furniture on a plain white background", "an object isolated on white"),
    "room": ("a photo of a furnished room", "an interior with several pieces of furniture, a floor and walls"),
    "flat": ("a flat pattern that fills the whole picture", "a close-up of a rug's surface", "a painting or print that fills the whole frame"),
}

# --------------------------------------------------------------------------------------------
# Reading a title

_LEAD = re.compile(r"^\s*(?:an?\s+)?(?:amazon\s*brand|amazon[\s-]*marke|marque\s+amazon|marca\s+amazon|marchio\s+amazon)\s*[-–—:]*\s*", re.I)
_UNIT = r"(?:cm|mm|inch(?:es)?|in|foot|feet|ft|''|['\"”″′])"
_SIZES = [
    # 5 x 8 Foot, 80 x 80 x 36cm (L x W x H), 10' 6 x 8'
    re.compile(rf"\(?\b\d+(?:[.,]\d+)?\s*{_UNIT}?(?:\s*\d+\s*{_UNIT}?)?\s*(?:[x×]\s*\d+(?:[.,]\d+)?\s*-?{_UNIT}?(?:\s*\d+\s*{_UNIT})?\s*)+\)?(?:\s*\((?:[a-z]+\s*x\s*)+[a-z]+\))?", re.I),
    # 79.9W, 82"W, 59H, 54 Inch, 36cm, 23.6-Inch
    re.compile(rf"\b\d+(?:[.,]\d+)?\s*{_UNIT}?\s*-?\s*(?:[WHLD]\b|inch(?:es)?\b|foot\b|feet\b|cm\b|mm\b)(?:\s*(?:wide|high|height|width|long|tall|deep)\b)?", re.I),
    re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:''|[\"”″])"),
]
# Words that follow a maker's name in a title and are not the name of a design line.
NOT_A_LINE = frozenset("""
modern contemporary mid mid-century midcentury classic traditional casual rustic vintage industrial farmhouse scandinavian
transitional coastal bohemian boho glam french southwestern american moroccan old new retro antique global tribal urban
wood wooden leather velvet metal steel ceramic glass marble polyester polypropylene wool cotton canvas jute beech oak cedar
terrazzo neoprene rubber plastic faux linen iron brass bronze rattan wicker bamboo pine walnut acacia concrete stone fabric
black blue pink red gold turquoise white grey gray green brown yellow orange purple navy silver ivory beige cream tan multi
round rectangular geometric abstract floral tufted solid curved textured patterned framed medallion trellis braided woven
handtufted large small medium single-light adjustable foldable folding portable outdoor indoor triangular elevated square
oval striped distressed tall short wide narrow low high high-back mid-back armless backless swivel reclining rolling
table piece drawer shelf globe pineapple watercolor spring motion schoolhouse multi-purpose multipurpose usb usb-a usb-c
heavy extra deep memory step floating gaming ergonomic steel shelf pull single double twin full queen king north south
east west los st the and for with set pack inch foot by sedia canapé lámpara lampe chaise divano mesa tavolo table lit
flow modular mudcloth-inspired premium deluxe basic basics home kids kid children decorative accent plush shag handwoven
hand-tufted hand-woven hand-knotted tripod arc task led edison vertical horizontal corner storage adjustable-height
sofa chair lamp rug bed desk mirror bench stool ottoman planter vase pillow headboard nightstand dresser bookcase cabinet
""".split())


def brand_key(brand: str | None) -> str:
    """One spelling per maker: "Amazon Basics", "AmazonBasics" and "Amazon Brand - Solimo" are
    each listed in several ways."""
    text = re.sub(r"^amazon brand\s*[-–]\s*", "", (brand or "").strip(), flags=re.I)
    text = re.sub(r"\(.*?\)|\bby amazon\b|[^\w&+ ]", " ", text, flags=re.I)
    key = re.sub(r"\s+", " ", text).strip().casefold()
    return "amazonbasics" if key.replace(" ", "") in ("amazonbasics", "amazonベーシック") or "amazonbasics" in (brand or "").casefold() else key


def _brand_pattern(key: str) -> re.Pattern | None:
    words = [re.escape(w) for w in re.findall(r"[\w]+|[&+]", "amazon basics" if key == "amazonbasics" else key)]
    return re.compile(r"[\s.\-]*".join(words), re.I) if words else None


def _after_brand(title: str, key: str) -> tuple[str, bool]:
    """The title from just after the maker's name on (and whether the name was found). A title
    that does not name its maker comes back whole, minus the "Amazon Brand –" in front."""
    text = _LEAD.sub("", title or "")
    pattern = _brand_pattern(key)
    if pattern:
        found = pattern.search(text)
        if found and found.start() < 24:
            return text[found.end():].lstrip(" -–—.,:"), True
    return text, False


def _line_word(rest: str) -> str | None:
    found = re.match(r"([A-ZÀ-Ý][a-zà-ÿ]{2,})\b(?![-'])", rest)
    return found.group(1) if found and found.group(1).casefold() not in NOT_A_LINE else None


def find_lines(pieces: list[dict]) -> dict[str, tuple[str, str]]:
    """piece id -> (maker, design line), read from the titles: "Rivet Sloane Mid-Century Sofa"
    is Rivet's Sloane line, "Stone & Beam Parson Coffee Table" Stone & Beam's Parson line.

    A maker's lines are the capitalised words that follow its name in at least two titles and
    hardly ever turn up anywhere else in a title: a line name is used for nothing else, a word
    of description ("Rustic", "Round") is used everywhere. Where that finds nothing and the
    item file names a line itself (it also reads two-word names, "North End"), that one is taken."""
    first: collections.Counter = collections.Counter()
    leading: collections.Counter = collections.Counter()
    anywhere: collections.Counter = collections.Counter()
    candidate: dict[str, tuple[str, str]] = {}
    for piece in pieces:
        title = piece.get("full") or piece.get("name") or ""
        for word in set(re.findall(r"[A-Za-zÀ-ÿ][\w'-]*", title)):
            anywhere[word.casefold()] += 1
        key = brand_key(piece.get("brand"))
        if not key:
            continue
        rest, named = _after_brand(title, key)
        word = _line_word(rest) if named else None
        if word:
            candidate[piece["id"]] = (key, word)
            first[(key, word)] += 1
            leading[word.casefold()] += 1  # two makers may each have an "Anne" line
    lines = {}
    for piece_id, (key, word) in candidate.items():
        if first[(key, word)] >= 2 and leading[word.casefold()] >= 0.7 * anywhere[word.casefold()]:
            lines[piece_id] = (key, word)
    for piece in pieces:
        if piece.get("line") and piece["id"] not in lines and brand_key(piece.get("brand")):
            lines[piece["id"]] = (brand_key(piece.get("brand")), piece["line"])
    return lines


def clean_title(title: str, brand: str | None = None, line: str | None = None) -> str:
    """A title as the text model reads it: no maker, no line name, no sizes. What is left says
    what the piece is and looks like. The line's name must go because two pieces of one line
    would otherwise look alike for sharing a made-up word, which is what the compatibility
    vectors are tested on."""
    text, _ = _after_brand(title, brand_key(brand))
    if line:
        text = re.sub(rf"\b{re.escape(line)}\b", " ", text, count=1)
    for pattern in _SIZES:
        text = pattern.sub(" ", text)
    text = re.sub(r"\(\s*\)", " ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    text = re.sub(r"([,;:\-–]\s*){2,}", ", ", text)
    return re.sub(r"\s+", " ", text).strip(" ,.;:-–")


def words_in(title: str) -> str:
    """A title in lower case with every word set off by spaces, for whole-word matching."""
    return " " + re.sub(r"[^a-z0-9à-ÿ'+-]+", " ", (title or "").lower()) + " "
