"""What every piece of the library looks like, and which pieces belong together. Offline only.

    PYTHONPATH=. uv run --quiet --no-project --python 3.12 \
        --with-requirements services/api/requirements-b.txt --with-requirements services/api/twin/requirements.txt \
        --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match \
        --with torch --with open_clip_torch --with scikit-learn \
        python -m services.api.library.build.embed

Reads every artifacts/items.*.jsonl.gz there is (before the first one exists: the ABO products
with a 3D model, data/catalog/abo_meta/models.jsonl) and writes the look files that
library/__init__.py describes: looks.jsonl.gz, look_ids.json, look.f16.npy, compat.f16.npy,
vocab.json, vocab.f16.npy. The API reads those with numpy alone. No model runs at request time,
so everything a request needs is worked out here: a vector per piece, a vector per phrase of a
fixed vocabulary, and the tags a keyword search can use.

A run only does what is new. Thumbnails and the model's raw vectors are kept under data/library/
(not in git), a picture by its address and a title by its text, so adding a collection costs the
new pieces only. The steps:

    thumbs   every piece's picture, 256 px, fetched 16 at a time
    raw      an image vector and a title vector per piece, from one CLIP-family model (MODEL)
    colour   the piece's own colours from its pixels, with the studio background left out
    look     picture and title in one vector, cut down to LOOK_DIM numbers
    tags     styles, materials and how high-end it looks, from the vectors and the title's words
    vocab    the phrases a designer may ask for, as vectors in the same space
    compat   a small projection of the look vectors, trained on pieces that were designed
             together (one maker's design line, the catalog's room sets), shipped only if it
             beats the plain look vectors on lines it never saw

`python -m services.api.library.build.embed_check` measures and draws what this wrote.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import gzip
import hashlib
import io
import json
import logging
import re
from pathlib import Path

import numpy as np

from services.api.library import ARTIFACTS

log = logging.getLogger("library.embed")

REPO_ROOT = Path(__file__).resolve().parents[4]
DATA = REPO_ROOT / "data" / "library"  # caches and scratch pictures, never in git
ABO_META = REPO_ROOT / "data" / "catalog" / "abo_meta" / "models.jsonl"
ABO_IMAGES = "https://amazon-berkeley-objects.s3.amazonaws.com/images/small/"

# Requests name no project and no person.
HEADERS = {"User-Agent": "furniture-library-fetch/0.1"}
THUMB_PX = 256
FETCH_AT_ONCE = 16


# --------------------------------------------------------------------------------------------
# The pieces


def read_items() -> tuple[list[dict], list[str]]:
    """Every piece of every collection's item file, and the names of the files they came from.
    An id in two files is taken from the first."""
    pieces: dict[str, dict] = {}
    files = sorted(ARTIFACTS.glob("items.*.jsonl.gz"))
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as lines:
            for line in lines:
                if line.strip():
                    item = json.loads(line)
                    pieces.setdefault(item["id"], item)
    return list(pieces.values()), [p.name for p in files]


# ABO's product type, or a word of the title, to a furniture class. Only for the bootstrap below:
# the item files carry a checked class of their own.
_BOOT_TYPES = {"RUG": "rug", "WALL_ART": "wall_art", "HOME_MIRROR": "mirror", "PLANTER": "plant", "PILLOW": "decor",
               "VASE": "decor", "OTTOMAN": "ottoman", "DRESSER": "dresser", "HEADBOARD": "bed", "BED_FRAME": "bed",
               "LIGHT_FIXTURE": "ceiling_light", "CLOCK": "decor", "PICTURE_FRAME": "decor", "CANDLE_HOLDER": "decor",
               "ARTIFICIAL_PLANT": "plant", "BEAN_BAG_CHAIR": "armchair"}
_BOOT_WORDS: list[tuple[str, str]] = [
    (r"sectional|sofa-chaise|chaise sofa", "sectional"), (r"loveseat|love seat", "loveseat"),
    (r"ottoman|pouf|footstool|foot stool", "ottoman"), (r"sofa|couch|settee|futon|divano|canap", "sofa"),
    (r"nightstand|night stand|bedside", "nightstand"), (r"coffee table|cocktail table", "coffee_table"),
    (r"console|entryway table|sofa table", "console_table"), (r"dining table|kitchen table|dining room table", "dining_table"),
    (r"tv stand|media|entertainment|tv table|tv console", "tv_stand"), (r"desk", "desk"),
    (r"bookcase|bookshelf|shelving|shelf|shelves|étagère|etagere", "bookshelf"), (r"dresser|chest of|drawer chest", "dresser"),
    (r"wardrobe|armoire", "wardrobe"), (r"cabinet|sideboard|buffet|credenza|cupboard", "cabinet"),
    (r"side table|end table|accent table|nesting|garden stool", "side_table"),
    (r"floor lamp|torchiere|standing lamp|lámpara de pie|lampe sur pied", "floor_lamp"), (r"table lamp|desk lamp|bedside lamp", "table_lamp"),
    (r"pendant|chandelier|ceiling|flush mount", "ceiling_light"), (r"\brug\b|runner|doormat", "rug"),
    (r"mirror", "mirror"), (r"wall art|print\b|canvas|framed", "wall_art"), (r"planter|plant\b|flower pot", "plant"),
    (r"stool", "stool"), (r"bench", "bench"), (r"\bcrib\b", "crib"), (r"headboard|\bbed\b|bunk", "bed"),
    (r"accent chair|armchair|arm chair|recliner|club chair|wingback|living room chair|glider|rocker|lounge chair|chair-and-a-half", "armchair"),
    (r"chair|sedia|chaise", "chair"), (r"\btable\b", "side_table"), (r"lamp|sconce", "table_lamp"),
    (r"pillow|vase|basket|candle|sculpture|figurine|bowl|tray|clock|decor", "decor"),
]


def _boot_class(product_type: str, title: str) -> str:
    low = title.lower()
    if product_type in _BOOT_TYPES and product_type not in ("OTTOMAN",):
        return _BOOT_TYPES[product_type]
    if product_type in ("SOFA", "CHAIR", "TABLE", "LAMP", "STOOL_SEATING", "HOME_FURNITURE_AND_DECOR", "HOME", "DESK", "BED",
                        "CABINET", "SHELF", "BENCH", "OTTOMAN", "FURNITURE", "OUTDOOR_LIVING", "HOME_BED_AND_BATH",
                        "HOME_LIGHTING_AND_LAMPS", "STORAGE_BOX"):
        for pattern, cls in _BOOT_WORDS:
            if re.search(pattern, low):
                return cls
    return "other"


def bootstrap_items() -> list[dict]:
    """Stand-in pieces for a run before the first item file exists: every ABO product with a 3D
    model, with a rough class. Good enough to choose the model and train on; the shipped files
    come from the item files once they are there."""
    pieces = []
    for line in ABO_META.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if not row.get("photo"):
            continue
        pieces.append({"id": f"abo:{row['id']}", "src": "abo", "name": row["name"], "full": row["name"], "brand": row.get("brand"),
                       "cls": _boot_class(row.get("type") or "", row["name"]), "img": ABO_IMAGES + row["photo"],
                       "color": row.get("color"), "tags": []})
    return pieces


def load_pieces() -> tuple[list[dict], list[str]]:
    """The pieces to embed and where they came from: every item file, and the ABO stand-ins for
    as long as ABO's own item file is not there (it is the collection the design lines are in)."""
    pieces, files = read_items()
    if "items.abo.jsonl.gz" not in files and ABO_META.exists():
        known = {p["id"] for p in pieces}
        pieces += [p for p in bootstrap_items() if p["id"] not in known]
        files.append("(stand-in) data/catalog/abo_meta/models.jsonl")
    return [p for p in pieces if p.get("img")], files


# --------------------------------------------------------------------------------------------
# Thumbnails


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def thumb_path(url: str) -> Path:
    """Where a picture is kept, by its address. PNG, so a see-through background survives."""
    key = _digest(url)
    return DATA / "thumbs" / key[:2] / f"{key}.png"


def _fetch_thumb(client, url: str) -> bool:
    from PIL import Image

    path = thumb_path(url)
    if path.exists():
        return True
    try:
        reply = client.get(url)
        reply.raise_for_status()
        picture = Image.open(io.BytesIO(reply.content))
        picture = picture.convert("RGBA" if picture.mode in ("RGBA", "LA", "P") else "RGB")
        picture.thumbnail((THUMB_PX, THUMB_PX))
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        picture.save(part, "PNG")
        part.rename(path)  # a run that is stopped half-way leaves no broken picture behind
        return True
    except Exception as err:  # noqa: BLE001 - one missing picture must not stop ten thousand
        log.warning("no picture for %s: %s", url, err)
        return False


def fetch_thumbs(urls: list[str]) -> None:
    """Downloads the pictures that are not cached yet, FETCH_AT_ONCE at a time."""
    import httpx

    urls = sorted({url for url in urls if not thumb_path(url).exists()})
    if not urls:
        return
    log.info("fetching %d pictures", len(urls))
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True,
                      limits=httpx.Limits(max_connections=FETCH_AT_ONCE)) as client:
        with concurrent.futures.ThreadPoolExecutor(FETCH_AT_ONCE) as pool:
            done = list(pool.map(lambda url: _fetch_thumb(client, url), urls))
    log.info("fetched %d of %d", sum(done), len(urls))


# --------------------------------------------------------------------------------------------
# Raw vectors from the model

# The models this can run (open_clip name, weights). MODEL is the one the shipped files use; the
# others are here so the choice can be measured again (embed_check.py models).
MODELS = {
    "siglip2-b16": ("ViT-B-16-SigLIP2", "webli"),
    "clip-b16": ("ViT-B-16", "datacomp_xl_s13b_b90k"),
    "clip-b32": ("ViT-B-32", "laion2b_s34b_b79k"),
}
MODEL = "siglip2-b16"


class Store:
    """Vectors kept on disk by key (a picture's address, a text), so a model only ever sees what
    it has not seen before. One matrix and one list of keys per model and kind."""

    def __init__(self, model: str, kind: str):
        self.folder = DATA / "raw" / model
        self.matrix_path = self.folder / f"{kind}.f32.npy"
        self.keys_path = self.folder / f"{kind}.keys.json"
        self.keys: list[str] = json.loads(self.keys_path.read_text()) if self.keys_path.exists() else []
        self.rows = np.load(self.matrix_path) if self.matrix_path.exists() else None
        self.index = {key: i for i, key in enumerate(self.keys)}

    def missing(self, keys: list[str]) -> list[str]:
        return sorted({key for key in keys if key not in self.index})

    def add(self, keys: list[str], rows: np.ndarray) -> None:
        self.rows = rows if self.rows is None else np.concatenate([self.rows, rows])
        self.keys += keys
        self.index = {key: i for i, key in enumerate(self.keys)}
        self.folder.mkdir(parents=True, exist_ok=True)
        np.save(self.matrix_path, self.rows)
        self.keys_path.write_text(json.dumps(self.keys))

    def get(self, keys: list[str]) -> np.ndarray:
        return self.rows[[self.index[key] for key in keys]]


class Encoder:
    """The picture-and-text model, loaded the first time something is missing from the stores."""

    def __init__(self, model: str):
        self.model = model
        self.images = Store(model, "image")
        self.texts = Store(model, "text")
        self._loaded = None

    def _load(self):
        if self._loaded is None:
            import open_clip
            import torch

            name, weights = MODELS[self.model]
            log.info("loading %s (%s)", name, weights)
            net, _, preprocess = open_clip.create_model_and_transforms(name, pretrained=weights)
            # A graphics card when the machine has one; ten thousand pictures are also fine without.
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            net.eval().to(self.device)
            self._loaded = (net, preprocess, open_clip.get_tokenizer(name), torch)
        return self._loaded

    def image_vectors(self, paths: dict[str, Path]) -> None:
        """Adds the vectors of the pictures in `paths` (key -> file) that the store lacks."""
        todo = [key for key in self.images.missing(list(paths)) if paths[key].exists()]
        if not todo:
            return
        from PIL import Image

        net, preprocess, _, torch = self._load()

        def tensor(key: str):
            picture = Image.open(paths[key])
            if picture.mode == "RGBA":  # a cut-out stands on white, as a studio photo does
                white = Image.new("RGBA", picture.size, "white")
                picture = Image.alpha_composite(white, picture)
            return preprocess(picture.convert("RGB"))

        log.info("%s: %d pictures to embed", self.model, len(todo))
        for start in range(0, len(todo), 512):
            chunk = todo[start:start + 512]
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                batch = torch.stack(list(pool.map(tensor, chunk)))
            with torch.inference_mode():
                rows = torch.cat([net.encode_image(batch[i:i + 64].to(self.device)) for i in range(0, len(batch), 64)])
            rows = torch.nn.functional.normalize(rows.float(), dim=-1).cpu().numpy().astype(np.float32)
            self.images.add(chunk, rows)  # saved as it goes: a stopped run picks up where it was
            log.info("  %d / %d", start + len(chunk), len(todo))

    def text_vectors(self, texts: list[str]) -> np.ndarray:
        """The vectors of `texts`, in order, each of length 1."""
        todo = self.texts.missing(texts)
        if todo:
            net, _, tokenizer, torch = self._load()
            log.info("%s: %d texts to embed", self.model, len(todo))
            with torch.inference_mode():
                rows = torch.cat([net.encode_text(tokenizer(todo[i:i + 256]).to(self.device)) for i in range(0, len(todo), 256)])
            self.texts.add(todo, torch.nn.functional.normalize(rows.float(), dim=-1).cpu().numpy().astype(np.float32))
        return self.texts.get(texts)


def piece_texts(pieces: list[dict], lines: dict[str, tuple[str, str]]) -> list[str]:
    """What the text model reads for each piece: its title without maker, line name and sizes.
    A title of two or three words ("Chinese Sofa", as Poly Haven names things) says too little:
    the collection's own tags are put after it."""
    from services.api.library.build.embed_words import clean_title

    texts = []
    for piece in pieces:
        text = clean_title(piece.get("full") or piece.get("name") or "", piece.get("brand"), lines.get(piece["id"], (None, None))[1]) or piece.get("name") or "furniture"
        if len(text.split()) < 4 and piece.get("tags"):
            text += ": " + ", ".join(piece["tags"][:10])
        texts.append(text)
    return texts


# --------------------------------------------------------------------------------------------
# Which picture shows the piece, and which of its pixels are the piece

PLAIN_BORDER = 0.7  # this much of a picture's border in one colour: a studio photo
EVEN_BORDER = 0.9  # or this much of it even backdrop, plain or shaded: a render


class Notes:
    """A dictionary kept on disk under data/library/, for what is slow to work out twice."""

    def __init__(self, name: str):
        self.path = DATA / f"{name}.json"
        self.data: dict = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        part = self.path.with_suffix(".part")
        part.write_text(json.dumps(self.data))
        part.replace(self.path)


def _in_processes(job, items: list) -> list:
    """Pixel work is plain Python and numpy: spread it over the machine's cores."""
    if len(items) < 32:
        return [job(item) for item in items]
    with concurrent.futures.ProcessPoolExecutor(max_workers=12) as pool:
        return list(pool.map(job, items, chunksize=32))


def _pixel_job(path: str) -> dict:
    from PIL import Image

    from services.api.library.build.embed_colour import border_even, border_plain, piece_mask

    picture = np.asarray(Image.open(path))
    plain, colour = border_plain(picture[..., :3])
    return {"plain": round(plain, 3), "even": round(border_even(picture[..., :3]), 3), "white": bool(colour.min() >= 235),
            "cutout": bool(picture.shape[2] == 4 and float((picture[..., 3] < 128).mean()) > 0.02),
            "fill": round(float(piece_mask(picture, "studio").mean()), 3)}


def pixel_facts(urls: list[str]) -> dict[str, dict]:
    """picture address -> what its pixels say: "plain" (how much of its border is one colour),
    "even" (how much of it is even backdrop, plain or shaded), "white" (whether that colour is
    white), "cutout" (a see-through background), "fill" (how much of the picture the piece
    takes up, if the border is the background)."""
    notes = Notes("pixels")
    todo = sorted({url for url in urls if url not in notes.data and thumb_path(url).exists()})
    if todo:
        for url, facts in zip(todo, _in_processes(_pixel_job, [str(thumb_path(url)) for url in todo])):
            notes.data[url] = facts
        notes.save()
    return notes.data


def is_studio(facts: dict) -> bool:
    """A cut-out, a photo on a plain ground or a render on a shaded one: the piece alone."""
    return bool(facts["cutout"] or facts["plain"] >= PLAIN_BORDER or facts["even"] >= EVEN_BORDER)


def picture_kinds(encoder: Encoder, pieces: list[dict], facts: dict[str, dict]) -> list[str]:
    """"studio", "room" or "flat" for each piece's picture (embed_colour.py says what they
    mean). The pixels tell a studio picture. For the rest the model says whether it shows a
    room or a surface that fills the frame: pixels cannot tell a print from a room.

    A rug that is not alone on white always counts as in a room. ABO shows nearly every rug
    lying under a sofa, close enough that the model calls the picture a pattern, and the sofa
    and the floorboards would be counted as the rug's colours."""
    from services.api.library.build.embed_words import PICTURE_KINDS

    urls = [p["img"] for p in pieces]
    unsure = sorted({url for url in urls if not is_studio(facts[url])})
    encoder.image_vectors({url: thumb_path(url) for url in unsure})
    room, flat = (unit(encoder.text_vectors(list(PICTURE_KINDS[kind])).mean(axis=0)) for kind in ("room", "flat"))
    seen = encoder.images.get(unsure) if unsure else np.zeros((0, len(room)), dtype=np.float32)
    in_room = dict(zip(unsure, seen @ room > seen @ flat))
    return ["studio" if is_studio(facts[url]) else "room" if in_room[url] or piece["cls"] == "rug" else "flat" for piece, url in zip(pieces, urls)]


ON_WHITE = "#on-white"  # after a picture's address: the same picture with its backdrop made white


def _white_job(job: tuple[str, str]) -> None:
    from PIL import Image

    from services.api.library.build.embed_colour import on_white

    source, target = job
    Image.fromarray(on_white(np.asarray(Image.open(source)))).save(target, "PNG")


def seen_as(pieces: list[dict], kinds: list[str], facts: dict[str, dict]) -> list[str]:
    """The key of the picture the model is shown for each piece: its picture, or, for a studio
    picture whose backdrop is not white (a Sketchfab render on a dark gradient), that picture
    with the backdrop made white. Makes the whitened pictures that are missing."""
    keys = []
    for piece, kind in zip(pieces, kinds):
        url = piece["picture"]
        dark = kind == "studio" and not facts[url]["cutout"] and not (facts[url]["white"] and facts[url]["plain"] >= PLAIN_BORDER)
        keys.append(url + ON_WHITE if dark else url)
    todo = sorted({key for key in keys if key.endswith(ON_WHITE) and not thumb_path(key).exists()})
    for key in todo:
        thumb_path(key).parent.mkdir(parents=True, exist_ok=True)
    _in_processes(_white_job, [(str(thumb_path(key[:-len(ON_WHITE)])), str(thumb_path(key))) for key in todo])
    return keys


def abo_other_pictures() -> dict[str, list[str]]:
    """ABO product id -> the addresses of its other photos, from the dataset's listing files when
    they are on this machine (data/catalog/abo_meta/). Empty without them."""
    import csv

    cache = DATA / "abo_pictures.json"
    if cache.exists():
        return json.loads(cache.read_text())
    listings, images = sorted(ABO_META.parent.glob("listings_*.json.gz")), ABO_META.parent / "images.csv.gz"
    if not listings or not images.exists():
        return {}
    with gzip.open(images, "rt") as rows:
        path_of = {row["image_id"]: row["path"] for row in csv.DictReader(rows)}
    others: dict[str, list[str]] = {}
    for path in listings:
        with gzip.open(path, "rt") as lines:
            for line in lines:
                if '"3dmodel_id"' not in line:
                    continue
                listing = json.loads(line)
                urls = others.setdefault(listing["3dmodel_id"], [])
                urls += [ABO_IMAGES + path_of[i] for i in listing.get("other_image_id") or [] if i in path_of and ABO_IMAGES + path_of[i] not in urls]
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(others))
    return others


def studio_pictures(pieces: list[dict], kinds: list[str]) -> int:
    """Where a piece's picture shows it in a room (nearly every ABO rug: the rug under a sofa
    and a plant), looks among the product's other photos for one of the piece alone on white and
    reads the look and the colours from that instead: sets piece["picture"], and the kind to
    "studio". The card still shows piece["img"]. Returns how many were changed.

    Of the photos with a plain border, the one the piece fills most is taken: that prefers a
    rug seen from above to the same rug seen at an angle, and leaves out drawings with measurements."""
    others = abo_other_pictures()
    wanted = {i: others.get(p["id"][4:], [])[:8] for i, p in enumerate(pieces) if kinds[i] == "room" and p["id"].startswith("abo:")}
    urls = [url for candidates in wanted.values() for url in candidates]
    fetch_thumbs(urls)
    facts = pixel_facts(urls)
    changed = 0
    for i, candidates in wanted.items():
        best, best_fill = None, 0.12
        for url in candidates:
            if url in facts and facts[url]["plain"] >= 0.9 and facts[url]["white"] and best_fill < facts[url]["fill"] <= 0.97:
                best, best_fill = url, facts[url]["fill"]
        if best:
            pieces[i]["picture"], kinds[i] = best, "studio"
            changed += 1
    return changed


COLOUR_RULES = 2  # raise when embed_colour.py changes what it answers: the kept answers are then worked out again


def named_tone(piece: dict, text: str) -> str | None:
    """The tone the maker's own words give, if they give one: the colour name first, then what
    follows the last comma of the title ("…, Honey Oak"), then a plain colour word anywhere."""
    from services.api.library.build.embed_colour import TONE_WORDS, TONES

    tail = text.rsplit(",", 1)[-1] if "," in text else ""
    for source, known in ((piece.get("color") or "", TONE_WORDS), (tail if len(tail.split()) <= 5 else "", TONE_WORDS),
                          (text, {**{tone: tone for tone in TONES}, "gray": "grey", "ivory": "cream"})):
        for word in re.findall(r"[a-z-]+", source.lower()):
            if word in known:
                return known[word]
    return None


# Pieces whose largest area is white whatever their colour: a lamp's shade, a bed's linen.
_PALE_LAST = ("floor_lamp", "table_lamp", "bed", "crib")


def _colour_job(job: tuple[str, str, str | None, bool]) -> tuple[list[str], str]:
    from PIL import Image

    from services.api.library.build.embed_colour import palette

    path, kind, named, pale_last = job
    return palette(np.asarray(Image.open(path)), kind, named, pale_last)


def colours(pieces: list[dict], kinds: list[str], texts: list[str]) -> tuple[list[list[str]], list[str]]:
    """(palette, tone) of every piece, from its picture."""
    notes = Notes("colours")
    named = [named_tone(p, text) for p, text in zip(pieces, texts)]
    pale = [p["cls"] in _PALE_LAST for p in pieces]
    keys = [f"{COLOUR_RULES}|{p['picture']}|{kind}|{name}|{int(last)}" for p, kind, name, last in zip(pieces, kinds, named, pale)]
    todo = {key: (str(thumb_path(p["picture"])), kind, name, last) for key, p, kind, name, last in zip(keys, pieces, kinds, named, pale) if key not in notes.data}
    if todo:
        log.info("colours of %d pictures", len(todo))
        for key, answer in zip(todo, _in_processes(_colour_job, list(todo.values()))):
            notes.data[key] = answer
        notes.save()
    return [notes.data[key][0] for key in keys], [notes.data[key][1] for key in keys]


# --------------------------------------------------------------------------------------------
# Everything a build starts from


@dataclasses.dataclass
class Prepared:
    pieces: list[dict]  # each with "maker" (one spelling per brand) and "picture" (the address its look is read from)
    files: list[str]  # the item files they came from
    lines: dict[str, tuple[str, str]]  # piece id -> (maker, design line)
    texts: list[str]  # what the text model read for each piece
    image: np.ndarray  # (N, d) picture vectors, rows of length 1
    text: np.ndarray  # (N, d) title vectors, rows of length 1
    kinds: list[str]  # "studio" | "room" | "flat"
    palettes: list[list[str]]
    tones: list[str]
    encoder: Encoder


def prepare(model: str = MODEL) -> Prepared:
    """Pieces, pictures, raw vectors and colours: all from the caches when nothing is new."""
    from services.api.library.build.embed_words import brand_key, find_lines

    pieces, files = load_pieces()
    log.info("%d pieces from %s", len(pieces), ", ".join(files))
    fetch_thumbs([p["img"] for p in pieces])
    pieces = [p for p in pieces if thumb_path(p["img"]).exists()]
    lines = find_lines(pieces)
    for piece in pieces:
        piece["maker"], piece["picture"] = brand_key(piece.get("brand")), piece["img"]
    encoder = Encoder(model)
    shown = [p["img"] for p in pieces]
    kinds = picture_kinds(encoder, pieces, pixel_facts(shown))
    rooms = kinds.count("room")
    changed = studio_pictures(pieces, kinds)
    used = [p["picture"] for p in pieces]
    seen = seen_as(pieces, kinds, pixel_facts(used))
    log.info("pictures: %d studio (%d of them put on white for the model), %d flat, %d in a room, of which %d have a studio photo to use instead",
             kinds.count("studio") - changed, sum(key.endswith(ON_WHITE) for key in seen), kinds.count("flat"), rooms, changed)
    encoder.image_vectors({key: thumb_path(key) for key in seen})
    texts = piece_texts(pieces, lines)
    palettes, tones = colours(pieces, kinds, texts)
    return Prepared(pieces, files, lines, texts, encoder.images.get(seen), encoder.text_vectors(texts), kinds, palettes, tones, encoder)


# --------------------------------------------------------------------------------------------
# The look space

# How much of a look vector is the picture; the rest is the title. Measured, not guessed:
# embed_check.py mix prints the table these come from.
PICTURE_SHARE = 0.5
LOOK_DIM = 256
# The average piece stays in. Taking it out would also take it out of the phrases, which are
# text and lie far from every picture: what is left of them all points the same way, and
# phrase search falls to half (measured).
CENTRE = False
COMPAT_DIM = 64


def unit(rows: np.ndarray) -> np.ndarray:
    return rows / np.maximum(np.linalg.norm(rows, axis=-1, keepdims=True), 1e-9)


def title_as_picture(image: np.ndarray, text: np.ndarray, folds: int = 5, ridge: float = 1.0) -> np.ndarray:
    """Each piece's title, moved into the space its picture is in.

    The model was trained to match a text to a picture, never a text to a text: its title
    vectors lie close together by length and wording, not by meaning ("velvet" is nearest to
    every title that is just "Sofa"). Added to the picture vectors as they are, they make
    phrase search worse than the pictures alone. So a title is first turned into the picture it
    stands for, by a linear map fitted on the pieces themselves (title vector -> picture
    vector). Each piece is mapped by a fit that did not see it, or the map would hand every
    piece its own picture back."""
    fold = np.random.default_rng(0).integers(folds, size=len(image))
    out = np.zeros_like(image)
    for held in range(folds):
        fit = fold != held
        titles, pictures = text[fit].astype(np.float64), image[fit].astype(np.float64)
        title_mean, picture_mean = titles.mean(axis=0), pictures.mean(axis=0)
        centred = titles - title_mean
        weights = np.linalg.solve(centred.T @ centred + ridge * np.eye(centred.shape[1]), centred.T @ (pictures - picture_mean))
        out[~fit] = (text[~fit] - title_mean) @ weights + picture_mean
    return unit(out)


def mix(image: np.ndarray, text: np.ndarray, share: float = PICTURE_SHARE) -> np.ndarray:
    """Picture and title in one vector of the model's full size: `share` of the picture, the
    rest the title as a picture."""
    return unit(share * image + (1 - share) * title_as_picture(image, text)) if share < 1 else image


def fit_basis(rows: np.ndarray, dim: int = LOOK_DIM, centre: bool = CENTRE) -> tuple[np.ndarray, np.ndarray]:
    """(the average row, the `dim` directions the rows differ most along): a PCA."""
    mean = rows.mean(axis=0) if centre else np.zeros(rows.shape[1], dtype=rows.dtype)
    _, _, axes = np.linalg.svd((rows - mean).astype(np.float64), full_matrices=False)
    return mean.astype(np.float32), axes[:dim].astype(np.float32)


def project(rows: np.ndarray, basis: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    """Rows of the full space in the cut-down space, each of length 1 again."""
    mean, axes = basis
    return unit((rows - mean) @ axes.T)


def phrase_vectors(encoder: Encoder, phrases: list[str], kind_of: dict[str, str], ensemble: bool = True, apart: bool = True) -> np.ndarray:
    """Each phrase as a vector of the model's full size: its wordings averaged, then (`apart`)
    the average phrase of its kind taken out.

    What all moods have in common, or all materials, is not what a search for one of them is
    after, and it is not neutral: the pieces nearest to "a … room" in general are the 3D
    renders, so that with the average left in nine in ten of the best sofas for any mood were
    Objaverse's, which are one in five of the sofas. Without it the mix is that of the library,
    and the listings are matched better too (embed_check.py mix)."""
    from services.api.library.build.embed_words import TEMPLATES

    rows = _prototypes(encoder, [[template.format(phrase) for template in (TEMPLATES[kind_of[phrase]] if ensemble else ("{}",))] for phrase in phrases])
    if apart:
        kinds = np.array([kind_of[phrase] for phrase in phrases])
        for kind in set(kinds):
            rows[kinds == kind] -= rows[kinds == kind].mean(axis=0)
    return unit(rows)


# --------------------------------------------------------------------------------------------
# Tags

_SEATS = ("sofa", "loveseat", "sectional", "armchair", "ottoman", "bench", "bed", "crib")
_UPHOLSTERY = ("velvet", "leather", "linen", "boucle", "fabric")
_HARD = ("light wood", "walnut", "dark wood", "painted wood", "marble", "stone", "glass", "mirror", "brass", "bronze", "chrome", "black metal", "rattan", "acrylic", "plastic")
# What the picture may say a piece is made of, by class, when its title names no material. A rug
# that looks like marble is still a rug.
_SEEN_MATERIALS: dict[str, tuple[str, ...]] = {
    **{cls: _UPHOLSTERY for cls in _SEATS},
    **{cls: _UPHOLSTERY + ("light wood", "walnut", "dark wood", "painted wood", "black metal", "chrome", "brass", "rattan", "acrylic", "plastic") for cls in ("chair", "stool")},
    "rug": ("wool", "jute", "cotton", "shag"),
    "wall_art": ("canvas",),
    **{cls: ("brass", "bronze", "chrome", "black metal", "ceramic", "glass", "light wood", "walnut", "marble", "rattan") for cls in ("floor_lamp", "table_lamp", "ceiling_light")},
    "mirror": ("brass", "black metal", "chrome", "light wood", "walnut", "dark wood", "painted wood", "rattan"),
    "plant": ("ceramic", "rattan", "stone", "black metal", "brass", "light wood"),
}
# Words that are a material in "Marble Coffee Table" and only a colour in "Sofa, Stone".
_COLOUR_NAMES = frozenset("stone iron ash gold silver steel slate cherry linen oak walnut espresso pine copper bronze pearl cement mahogany ebony brass chrome teak".split())


def _prototypes(encoder: Encoder, wordings: list[list[str]]) -> np.ndarray:
    """One vector per list of wordings: the average of their text vectors, of length 1."""
    encoder.text_vectors([text for texts in wordings for text in texts])  # all at once: one pass through the model
    return unit(np.array([encoder.text_vectors(texts).mean(axis=0) for texts in wordings]))


def _style_wordings() -> list[list[str]]:
    from services.api.library.build.embed_words import STYLES

    return [[describes, f"{name} style furniture"] for name, _, describes in STYLES]


def _material_wordings() -> list[list[str]]:
    from services.api.library.build.embed_words import MATERIALS

    return [[describes, f"furniture made of {name}"] for name, _, describes in MATERIALS]


def fixed_texts() -> list[str]:
    """Every text the build puts to the model besides the titles: the vocabulary in its
    wordings, and what the tags are matched against. `--only raw` embeds them too, so a later
    build needs no model at all."""
    from services.api.library.build.embed_words import KIND_OF, LUX_HIGH, LUX_LOW, PHRASES, PICTURE_KINDS, TEMPLATES

    texts = [template.format(phrase) for phrase in PHRASES for template in TEMPLATES[KIND_OF[phrase]]]
    texts += [text for wordings in _style_wordings() + _material_wordings() + [list(LUX_HIGH), list(LUX_LOW)] + [list(v) for v in PICTURE_KINDS.values()] for text in wordings]
    return sorted(set(texts))


def _relative(scores: np.ndarray, classes: np.ndarray | None = None) -> np.ndarray:
    """Each column against its own average: a label's score only means something compared with
    how the rest of the library scores on it. With `classes`, half of it is against the piece's
    own class: a rug is "bohemian" when it is more so than other rugs, not than sofas."""
    everything = (scores - scores.mean(axis=0)) / (scores.std(axis=0) + 1e-9)
    if classes is None:
        return everything
    within = everything.copy()
    for cls in set(classes):
        members = classes == cls
        if members.sum() >= 20:
            within[members] = (scores[members] - scores[members].mean(axis=0)) / (scores[members].std(axis=0) + 1e-9)
    return 0.5 * everything + 0.5 * within


def _said(piece: dict) -> str:
    """The words a piece is described by, set off by spaces: its title and the collection's
    tags, without its maker's name ("Stone & Beam" is not made of stone)."""
    from services.api.library.build.embed_words import clean_title, words_in

    maker = set(re.findall(r"[a-z]+", (piece.get("brand") or "").lower()))
    title = clean_title(piece.get("full") or piece.get("name") or "", piece.get("brand"))
    return words_in(" ".join([title, " ".join(tag for tag in piece.get("tags") or [] if tag not in maker)]))


def style_tags(vectors: np.ndarray, pieces: list[dict], encoder: Encoder) -> list[list[str]]:
    """One or two styles per piece. A style named in the title comes first (the maker knows),
    the plain words every title has ("Modern") only when nothing better is said; then what the
    look itself is closest to."""
    from services.api.library.build.embed_words import LOOSE_STYLES, STYLES

    names = [name for name, _, _ in STYLES]
    seen = _relative(vectors @ _prototypes(encoder, _style_wordings()).T, np.array([p["cls"] for p in pieces]))
    out = []
    for row, piece in enumerate(pieces):
        words = _said(piece)
        said = [name for name, keys, _ in STYLES if any(f" {key} " in words for key in keys)]
        styles = [name for name in said if name not in LOOSE_STYLES][:2] or said[:1]
        order = [names[i] for i in np.argsort(-seen[row]) if names[i] not in styles]
        score = dict(zip(names, seen[row]))
        if not styles:
            styles = [order[0]]
            if score[order[1]] >= max(1.0, score[order[0]] - 0.4):
                styles.append(order[1])
        elif len(styles) == 1 and score[order[0]] >= 1.3:
            styles.append(order[0])
        out.append(styles)
    return out


def material_tags(vectors: np.ndarray, pieces: list[dict], encoder: Encoder) -> list[list[str]]:
    """Up to three materials per piece: the ones its title names, in the order it names them,
    and failing that the one its look is closest to among those its class can be made of."""
    from services.api.library.build.embed_words import MATERIALS, METALS, WOODS, clean_title

    names = [name for name, _, _ in MATERIALS]
    seen = _relative(vectors @ _prototypes(encoder, _material_wordings()).T)
    out = []
    for row, piece in enumerate(pieces):
        words = _said(piece)
        title = clean_title(piece.get("full") or piece.get("name") or "", piece.get("brand"))
        # What follows the last comma of a title is the colour's name: "Stone", "Iron", "Oak".
        tail = {w for w in re.findall(r"[a-z]+", title.rsplit(",", 1)[-1].lower())} if "," in title else set()
        soft = piece["cls"] in _SEATS or piece["cls"] in ("rug", "wall_art")
        found: list[tuple[int, str]] = []
        for name, keys, _ in MATERIALS:
            if piece["cls"] == "rug" and name in ("fabric", "plastic"):
                continue  # "polypropylene" and "polyester" say what a rug is spun from, not what it looks like
            at = [words.find(f" {key} ") for key in keys if f" {key} " in words and not (key in _COLOUR_NAMES and (soft or key in ("gold", "silver")) and key in tail and words.count(f" {key} ") == 1)]
            if at:
                found.append((min(at), name))
        score = dict(zip(names, seen[row]))
        said = [name for _, name in sorted(found)]
        # "Wood" and "metal" name no finish: the picture says which.
        for general, family in (("wood", WOODS), ("wooden", WOODS), ("metal", METALS)):
            if f" {general} " in words and not any(name in family for name in said):
                said.append(max(family, key=lambda name: score[name]))
        if not said:
            allowed = _SEEN_MATERIALS.get(piece["cls"], _HARD + ("ceramic",))
            ranked = sorted(allowed, key=lambda name: -score[name])
            said = ranked[:1] + [name for name in ranked[1:2] if score[name] >= 1.5]
        out.append(said[:3])
    return out


def lux_scores(vectors: np.ndarray, pieces: list[dict], encoder: Encoder) -> np.ndarray:
    """How high-end each piece looks, 0..1, as its rank among the pieces of its own class: 0.9 is
    "more so than nine in ten sofas". A marble side table and a marble dining table are not
    compared. Classes too small to rank (fewer than eight) are ranked against everything."""
    from services.api.library.build.embed_words import LUX_HIGH, LUX_LOW

    high, low = _prototypes(encoder, [list(LUX_HIGH), list(LUX_LOW)])
    raw = vectors @ high - vectors @ low
    classes = np.array([p["cls"] for p in pieces])
    out = np.zeros(len(pieces), dtype=np.float32)
    everything = (np.argsort(np.argsort(raw)) + 0.5) / len(raw)
    for cls in set(classes):
        members = np.flatnonzero(classes == cls)
        out[members] = everything[members] if len(members) < 8 else (np.argsort(np.argsort(raw[members])) + 0.5) / len(members)
    return out


# --------------------------------------------------------------------------------------------
# Which pieces go together

# How the projection is trained: chosen on the validation lines (embed_check.py compat).
COMPAT_TRAINING = {"hidden": 0, "steps": 1500, "temperature": 0.07, "dropout": 0.1, "decay": 0.05, "stay": 0.0}


def compat_vectors(look: np.ndarray, pieces: list[dict], lines: dict[str, tuple[str, str]], palettes: list[list[str]]) -> tuple[np.ndarray, dict]:
    """(a compatibility vector per piece, the test that decided what it is).

    Trains the projection on most design lines and room sets, tests it on the rest against the
    look vectors as they are and against colour alone, and ships it only if it wins on both
    measures, with every piece as a candidate and with only the anchor's own maker's. Then the
    same recipe is run once more on every line and set, for the vectors that are shipped: the
    numbers in the report are those of the tested projection, which saw less."""
    from services.api.library.build import embed_compat as C

    groups = C.design_groups(pieces, lines)
    split = C.split_groups(groups)
    source = C.class_centred(look, pieces)
    tested, record = C.train_projection(source, pieces, groups, split["train"], split["val"], dim=COMPAT_DIM, **COMPAT_TRAINING)
    colour = C.colour_vectors(palettes)
    report = {"split": {part: len(names) for part, names in split.items()}, "best_step": record["best_step"], "rows": {},
              "tested": tested, "groups": groups, "names": split}  # the last three for embed_check.py weights
    for name, vectors in (("look", look), ("look, class average out", source), ("colour only", colour), ("learned", tested)):
        report["rows"][name] = {"all": C.rank_metrics(vectors, pieces, groups, split["test"]),
                                "own maker": C.rank_metrics(vectors, pieces, groups, split["test"], own_maker=True)}
    learned, rows = report["rows"]["learned"], report["rows"]
    report["ship_learned"] = all(learned[pool][measure] > rows[base][pool][measure]
                                 for base in ("look", "colour only") for pool in ("all", "own maker") for measure in ("recall@10", "mrr"))
    if report["ship_learned"]:
        steps = {**COMPAT_TRAINING, "steps": record["best_step"]}
        final, _ = C.train_projection(source, pieces, groups, sorted(groups), None, dim=COMPAT_DIM, **steps)
    else:
        final = project(look, fit_basis(look, COMPAT_DIM, centre=True))
    return final, report


def compat_table(report: dict) -> str:
    lines = [f"compat test: {report['split']['test']} held-out groups (lines and sets), {report['split']['train']} trained on, {report['split']['val']} for stopping",
             f"{'':28s}{'every candidate':>22s}{'own maker only':>22s}", f"{'':28s}{'recall@10':>11s}{'mrr':>11s}{'recall@10':>11s}{'mrr':>11s}"]
    for name, row in report["rows"].items():
        lines.append(f"{name:28s}{row['all']['recall@10']:11.3f}{row['all']['mrr']:11.3f}{row['own maker']['recall@10']:11.3f}{row['own maker']['mrr']:11.3f}")
    first = next(iter(report["rows"].values()))
    lines.append(f"anchors {first['all']['anchors']}, anchor-match pairs {first['all']['pairs']}, groups {first['all']['groups']} "
                 f"(own maker: {first['own maker']['anchors']} anchors, {first['own maker']['pairs']} pairs)")
    lines.append("shipped: the learned projection" if report["ship_learned"] else "shipped: a PCA of the look vectors (the learned projection did not win)")
    return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# Writing the files


def _write(path: Path, save) -> None:
    """Writes beside the file and swaps it in, so the API never reads half a file."""
    part = path.with_name(path.name + ".part")
    with open(part, "wb") as out:
        save(out)
    part.replace(path)


def describe(ready: Prepared) -> dict:
    """The look vectors, the vocabulary in the same space, and the tags: everything but compat."""
    from services.api.library.build.embed_words import KIND_OF, PHRASES

    pieces, encoder = ready.pieces, ready.encoder
    full = mix(ready.image, ready.text)
    basis = fit_basis(full)
    return {"ready": ready, "full": full, "look": project(full, basis), "vocab": project(phrase_vectors(encoder, PHRASES, KIND_OF), basis),
            "styles": style_tags(full, pieces, encoder), "materials": material_tags(full, pieces, encoder), "lux": lux_scores(full, pieces, encoder)}


def build(model: str = MODEL, write: bool = True) -> dict:
    """Works everything out and writes the look files. Returns what a caller may want to print or test."""
    from services.api.library.build.embed_words import KIND_OF, PHRASES

    ready = prepare(model)
    pieces = ready.pieces
    out = describe(ready)
    look, vocab, styles, materials, lux = out["look"], out["vocab"], out["styles"], out["materials"], out["lux"]
    compat, report = compat_vectors(look, pieces, ready.lines, ready.palettes)
    log.info("%s", compat_table(report))
    out |= {"compat": compat, "report": report}
    if not write:
        return out
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    rows = [{"id": p["id"], "styles": styles[i], "materials": materials[i], "palette": ready.palettes[i], "tone": ready.tones[i], "lux": round(float(lux[i]), 3)}
            for i, p in enumerate(pieces)]
    _write(ARTIFACTS / "looks.jsonl.gz", lambda f: f.write(gzip.compress("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows).encode("utf-8"), mtime=0)))
    _write(ARTIFACTS / "look_ids.json", lambda f: f.write(json.dumps([p["id"] for p in pieces]).encode("utf-8")))
    _write(ARTIFACTS / "look.f16.npy", lambda f: np.save(f, look.astype(np.float16)))
    _write(ARTIFACTS / "compat.f16.npy", lambda f: np.save(f, compat.astype(np.float16)))
    _write(ARTIFACTS / "vocab.json", lambda f: f.write(json.dumps({"phrases": PHRASES, "kind": KIND_OF}, ensure_ascii=False, indent=0).encode("utf-8")))
    _write(ARTIFACTS / "vocab.f16.npy", lambda f: np.save(f, vocab.astype(np.float16)))
    total = 0
    for name in ("looks.jsonl.gz", "look_ids.json", "look.f16.npy", "compat.f16.npy", "vocab.json", "vocab.f16.npy"):
        size = (ARTIFACTS / name).stat().st_size
        total += size
        shape = np.load(ARTIFACTS / name, mmap_mode="r").shape if name.endswith(".npy") else ""
        log.info("  %-16s %8.1f kB  %s", name, size / 1e3, shape)
    log.info("  %-16s %8.2f MB for %d pieces from %s", "together", total / 1e6, len(pieces), ", ".join(ready.files))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", choices=["thumbs", "raw"], help="stop after this step")
    parser.add_argument("--model", choices=sorted(MODELS), default=MODEL, help="the picture-and-text model (the shipped files use the default)")
    parser.add_argument("--dry", action="store_true", help="work everything out, write nothing")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.only == "thumbs":
        fetch_thumbs([p["img"] for p in load_pieces()[0]])
    elif args.only == "raw":
        prepare(args.model).encoder.text_vectors(fixed_texts())
    else:
        build(args.model, write=not args.dry)


if __name__ == "__main__":
    main()
