"""The furniture library: thousands of real 3D models from open collections, behind the furniture
menu and the designer. Owner: B.

The catalog (objects/providers/catalog.py) is 160 hand-checked pieces in sets. The library is
everything else those collections publish that can stand in a home: every Amazon Berkeley Objects
product with a 3D model, Poly Haven's furniture, lamps, plants and decorations, and more
collections as they are added. Nothing is downloaded until a piece is used: the library is an
index (a few megabytes, in git), and a model is fetched and cached the first time someone adds it.

    index.py      the library at run time: load, search, facets, "goes with", "more like this"
    sources.py    a piece's model as a GLB, from whichever collection it comes from
    compose.py    which pieces furnish a room, in a look
    dress.py      where rugs, lamps, pictures, plants and decorations go once the floor is arranged
    build/        offline tools that write artifacts/ (never run by the API)
    artifacts/    the index files described below

Nothing here needs more than numpy at run time. The embedding models that made the look vectors
run offline only (build/embed.py).

## artifacts/items.<source>.jsonl.gz

One file per collection, one JSON object per line, written by build/<source>.py:

    id        "abo:B07B4MG9HX" | "ph:ArmChair_01" | "ov:<uid>"   (<source prefix>:<the collection's own id>)
    src       "abo" | "polyhaven" | "objaverse"
    name      what a card shows, 48 characters at most, no brand: "Parson sofa, beige"
    full      the collection's full title, for search
    brand     str | null
    line      the design line within the brand ("Sloane", "Parson"), if the title names one | null
    cls       a class from objects/classes.py (FURNITURE_CLASSES)
    cat       the menu section: one of CATEGORIES below
    w, d, h   real size in metres of the piece as it stands once turned: w across its front, d front to back, h up
    turn      degrees about +Y to turn the source model so its front faces +Z; left out when 0.
              The model is turned first and sized after, so w and d are those of the turned piece.
    approx    true when the size is the typical one for its class and not the piece's own: the
              model then keeps its proportions when it is added. Left out otherwise
    img       thumbnail URL (https), about 256 px
    faces     triangle count | null
    fam       family key: the same design in other colours or sizes shares it
    color     the maker's name for its colour | null
    tags      lowercase words that describe it (materials, shapes, features), from the title and the collection's own tags
    license   "CC BY 4.0" | "CC0" | ...
    credit    who to credit: "Amazon.com", "Kirill Sannikov / Poly Haven"
    url       the piece's page at the collection | null
    auto      true when the designer may pick it by itself: its size and the way it faces are known to be right

`w, d, h` are the truth about size: when a model is added it is scaled to exactly these
(objects/normalize.py), so a collection whose models are drawn at the wrong scale only has to
state the right size here.

## artifacts/looks.jsonl.gz, look.f16.npy, look_ids.json, vocab.json, vocab.f16.npy, compat.f16.npy

Written by build/embed.py from the pieces' pictures and titles:

    looks.jsonl.gz    per piece: {"id", "styles": [..], "materials": [..], "palette": ["#hex", ..], "tone": "emerald", "lux": 0..1}
    look_ids.json     the ids, in row order, of the two matrices below
    look.f16.npy      (N, D) float16, rows of length 1: what a piece looks like (image and title in one space)
    compat.f16.npy    (N, K) float16, rows of length 1: pieces that belong in one room are close, whatever their kind
    vocab.json        {"phrases": [..], "kind": {phrase: "style" | "material" | "colour" | "mood"}}
    vocab.f16.npy     (M, D) float16, rows of length 1: each phrase in the space of look.f16.npy

Every file is optional. Without the look files the library searches by words only.
"""

from __future__ import annotations

from pathlib import Path

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"

# The sections of the furniture menu, in the order they are listed: (key, label).
CATEGORIES: list[tuple[str, str]] = [
    ("sofas", "Sofas"),
    ("armchairs", "Armchairs"),
    ("chairs", "Chairs & stools"),
    ("tables", "Tables"),
    ("desks", "Desks"),
    ("beds", "Beds"),
    ("storage", "Storage"),
    ("lighting", "Lighting"),
    ("rugs", "Rugs"),
    ("wall", "Wall art & mirrors"),
    ("plants", "Plants"),
    ("decor", "Decor"),
    ("outdoor", "Outdoor"),
    ("kids", "Kids"),
]

# The collections, in the order they are listed: key -> (label, the credit line shown with a piece).
SOURCES: dict[str, tuple[str, str]] = {
    "abo": ("Amazon Berkeley Objects", "Amazon Berkeley Objects · CC BY 4.0"),
    "polyhaven": ("Poly Haven", "Poly Haven · CC0"),
    "objaverse": ("Objaverse", "Objaverse · Sketchfab creators"),
}
