"""The furniture library at run time: load the index, search it, and find pieces that suit a look
and go with pieces already chosen. numpy only (services/api/library/__init__.py has the format).

Search is by words. Every word typed must match a piece: its name, its kind, its colour, its
material, a look it suits, its maker. A word or phrase of the look vocabulary ("art deco",
"emerald velvet", "quiet luxury") also matches the pieces that look most like it, by the look
vectors made offline from each piece's picture, even when no title says so.

`candidates` is what the designer uses: the pieces of some kind, within a size, ranked by how well
they suit a look in words and how well they go with the pieces already picked for the room.
"""

from __future__ import annotations

import bisect
import gzip
import json
import logging
import re
import zlib
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from services.api.library import ARTIFACTS, CATEGORIES, SOURCES

log = logging.getLogger(__name__)

SEMANTIC_SHARE = 0.06  # a phrase of the vocabulary matches the pieces that look most like it: this share of the library
PREFIX_MIN = 3  # a typed word this long also matches the start of a longer word ("velv" finds velvet)
PAGE_MAX = 120

# Words people type for what the index calls something else.
_SAME = {
    "couch": "sofa", "settee": "sofa", "grey": "gray", "carpet": "rug", "picture": "art", "painting": "art", "poster": "art",
    "shelve": "shelf", "bookcase": "bookshelf", "sideboard": "cabinet", "buffet": "cabinet", "credenza": "cabinet",
    "armoire": "wardrobe", "bedside": "nightstand", "planter": "plant", "pouf": "ottoman", "footstool": "ottoman",
    "television": "tv", "midcentury": "mid-century",
}
# What a class is also called, so "lamp" finds floor lamps and "table" finds coffee tables.
_CLASS_WORDS = {
    "ceiling_light": "ceiling light lamp chandelier pendant", "wall_art": "wall art", "tv_stand": "tv stand media console",
    "bookshelf": "bookshelf shelf", "nightstand": "nightstand bedside table", "plant": "plant", "decor": "decor decoration accessory",
    "floor_lamp": "floor lamp", "table_lamp": "table lamp", "armchair": "armchair accent chair", "loveseat": "loveseat sofa",
    "sectional": "sectional sofa", "crib": "crib bed", "dresser": "dresser drawer", "cabinet": "cabinet storage",
}


def words(text: str) -> list[str]:
    """Lowercase words of a text, plurals and spellings folded together, for both sides of a search."""
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9-]*", (text or "").lower()):
        if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
            w = w[:-1]
        out.append(_SAME.get(w, w))
    return out


@dataclass(slots=True)
class Piece:
    """One piece of the library (a line of artifacts/items.<source>.jsonl.gz, with its looks)."""

    id: str
    src: str
    name: str
    full: str
    cls: str
    cat: str
    w: float
    d: float
    h: float
    img: str
    brand: str | None = None
    line: str | None = None
    turn: float = 0.0
    faces: int | None = None
    fam: str | None = None
    color: str | None = None
    tags: tuple[str, ...] = ()
    license: str = ""
    credit: str = ""
    url: str | None = None
    auto: bool = False
    approx: bool = False
    styles: tuple[str, ...] = ()
    materials: tuple[str, ...] = ()
    palette: tuple[str, ...] = ()
    tone: str | None = None
    lux: float = 0.5
    row: int = -1  # its row in the look and compat matrices, -1 if it has none
    n: int = 0  # its place in Library.pieces
    weights: dict[str, float] = field(default_factory=dict, repr=False)  # search word -> how much a match counts

    @property
    def model(self) -> str:
        """The collection's own id for it: what the collection's fetcher takes."""
        return self.id.split(":", 1)[1]

    @property
    def long(self) -> float:
        """The longer side of its footprint: what matters for a rug, which may lie either way."""
        return max(self.w, self.d)

    @property
    def short(self) -> float:
        return min(self.w, self.d)

    def says(self, word_list: list[str]) -> float:
        """How many of these words describe the piece, 0 to 1 (its name, tags, looks, materials, colour)."""
        return sum(1 for w in word_list if w in self.weights) / len(word_list) if word_list else 0.0


def _read(path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class Library:
    def __init__(self, artifacts=ARTIFACTS) -> None:
        looks: dict[str, dict] = {}
        if (artifacts / "looks.jsonl.gz").exists():
            looks = {r["id"]: r for r in _read(artifacts / "looks.jsonl.gz")}
        rows: dict[str, int] = {}
        self.look: np.ndarray | None = None
        self.compat: np.ndarray | None = None
        if (artifacts / "look_ids.json").exists() and (artifacts / "look.f16.npy").exists():
            rows = {pid: k for k, pid in enumerate(json.loads((artifacts / "look_ids.json").read_text()))}
            self.look = np.load(artifacts / "look.f16.npy").astype(np.float32)
            if (artifacts / "compat.f16.npy").exists():
                self.compat = np.load(artifacts / "compat.f16.npy").astype(np.float32)
        self.vocab: dict[str, np.ndarray] = {}
        self.vocab_kind: dict[str, str] = {}
        if self.look is not None and (artifacts / "vocab.json").exists() and (artifacts / "vocab.f16.npy").exists():
            vocab = json.loads((artifacts / "vocab.json").read_text())
            vectors = np.load(artifacts / "vocab.f16.npy").astype(np.float32)
            for phrase, vector in zip(vocab["phrases"], vectors):
                key = " ".join(words(phrase))
                self.vocab[key] = vector
                self.vocab_kind[key] = vocab.get("kind", {}).get(phrase, "")
        self._phrase_len = max((len(p.split()) for p in self.vocab), default=1)

        known_cats = {key for key, _ in CATEGORIES}
        self.pieces: list[Piece] = []
        for key in SOURCES:
            path = artifacts / f"items.{key}.jsonl.gz"
            if not path.exists():
                continue
            for r in _read(path):
                try:
                    if r["cat"] not in known_cats or min(r["w"], r["d"], r["h"]) <= 0:
                        continue
                    extra = looks.get(r["id"], {})
                    self.pieces.append(Piece(
                        id=r["id"], src=r.get("src", key), name=r["name"], full=r.get("full") or r["name"], cls=r["cls"], cat=r["cat"],
                        w=float(r["w"]), d=float(r["d"]), h=float(r["h"]), img=r["img"], brand=r.get("brand"), line=r.get("line"),
                        turn=float(r.get("turn") or 0.0), faces=r.get("faces"), fam=r.get("fam") or r["id"], color=r.get("color"),
                        tags=tuple(r.get("tags") or ()), license=r.get("license", ""), credit=r.get("credit", ""), url=r.get("url"),
                        auto=bool(r.get("auto")), approx=bool(r.get("approx")), styles=tuple(extra.get("styles") or ()),
                        materials=tuple(extra.get("materials") or ()), palette=tuple(extra.get("palette") or ()), tone=extra.get("tone"),
                        lux=float(extra.get("lux", 0.5)), row=rows.get(r["id"], -1),
                    ))
                except (KeyError, TypeError, ValueError) as e:  # one bad line must not take the library down
                    log.warning("library: skipped a line of %s: %s", path.name, e)
        cat_label = dict(CATEGORIES)
        self.by_id: dict[str, Piece] = {}
        self.by_img: dict[str, Piece] = {}
        self.family_size: Counter[str] = Counter()
        self._index: dict[str, list[tuple[int, float]]] = {}
        for n, p in enumerate(self.pieces):
            p.n = n
            self.by_id[p.id] = p
            self.by_img.setdefault(p.img, p)
            self.family_size[p.fam] += 1
            fields = (
                (3.0, p.name),
                (2.5, _CLASS_WORDS.get(p.cls, p.cls.replace("_", " "))),
                (2.0, f"{cat_label[p.cat]} {' '.join(p.tags)} {' '.join(p.styles)} {' '.join(p.materials)} {p.tone or ''} {p.color or ''} {p.brand or ''} {p.line or ''}"),
                (1.0, f"{p.full} {SOURCES.get(p.src, (p.src, ''))[0]}"),
            )
            for weight, text in fields:
                for w in words(text):
                    if weight > p.weights.get(w, 0.0):
                        p.weights[w] = weight
            for w, weight in p.weights.items():
                self._index.setdefault(w, []).append((n, weight))
        self._words = sorted(self._index)
        self._rows = np.array([p.row for p in self.pieces], dtype=np.int64)
        # The order pieces are shown in when nothing is typed: the better looking first, with a
        # steady shuffle so one maker's line does not fill a page.
        jitter = np.array([(zlib.crc32(p.id.encode()) % 1000) / 1000 for p in self.pieces]) if self.pieces else np.zeros(0)
        self.prior = np.array([0.6 * p.lux + (0.25 if p.auto else 0.0) for p in self.pieces]) + 0.15 * jitter
        self._semantic: dict[str, np.ndarray] = {}

    # --- words ----------------------------------------------------------------------------------

    def _literal(self, word: str) -> np.ndarray:
        """How well each piece matches one typed word: 0 where it does not."""
        score = np.zeros(len(self.pieces), dtype=np.float32)
        for n, weight in self._index.get(word, ()):
            score[n] = weight
        if len(word) >= PREFIX_MIN:
            k = bisect.bisect_left(self._words, word)
            while k < len(self._words) and self._words[k].startswith(word):
                if self._words[k] != word:
                    for n, weight in self._index[self._words[k]]:
                        score[n] = max(score[n], 0.7 * weight)
                k += 1
        return score

    def looks_like(self, phrase: str) -> np.ndarray | None:
        """Each piece's likeness to a phrase of the vocabulary (cosine, 0 for a piece with no look
        vector), or None when the phrase is not in the vocabulary."""
        key = " ".join(words(phrase))
        if key not in self.vocab or self.look is None:
            return None
        if key not in self._semantic:
            sims = np.zeros(len(self.pieces), dtype=np.float32)
            have = self._rows >= 0
            sims[have] = self.look[self._rows[have]] @ self.vocab[key]
            self._semantic[key] = sims
        return self._semantic[key]

    def _units(self, query: str) -> list[tuple[str, np.ndarray]]:
        """The query cut into what must each match: phrases of the vocabulary (the longest first)
        and the single words left over. Each with its score per piece."""
        tokens = words(query)
        units: list[tuple[str, np.ndarray]] = []
        k = 0
        while k < len(tokens):
            for size in range(min(self._phrase_len, len(tokens) - k), 0, -1):
                phrase = " ".join(tokens[k:k + size])
                sims = self.looks_like(phrase)
                if sims is None and size > 1:
                    continue
                literal = np.minimum.reduce([self._literal(t) for t in tokens[k:k + size]])
                if sims is not None and (sims > 0).any():
                    floor = np.quantile(sims[sims > 0], 1 - SEMANTIC_SHARE)
                    top = float(sims.max())
                    alike = np.where(sims >= floor, 0.5 + 2.0 * (sims - floor) / max(top - floor, 1e-6), 0.0)
                    literal = np.maximum(literal, alike.astype(np.float32))
                units.append((phrase, literal))
                k += size
                break
        return units

    def match(self, query: str) -> tuple[np.ndarray, np.ndarray]:
        """(which pieces match, their score). Every word must match; when no piece has them all,
        the pieces with the most of them are given instead, so a search never comes back empty
        for one odd word."""
        n = len(self.pieces)
        units = self._units(query)
        if not units:
            return np.ones(n, dtype=bool), self.prior.copy()
        scores = np.stack([s for _, s in units])
        hit = scores > 0
        every = hit.all(axis=0)
        if not every.any():
            most = hit.sum(axis=0)
            every = (most == most.max()) & (most > 0)
        return every, scores.sum(axis=0) + 0.5 * self.prior

    # --- the menu -------------------------------------------------------------------------------

    def search(
        self, query: str = "", category: str | None = None, source: str | None = None, family: str | None = None,
        offset: int = 0, limit: int = 60,
    ) -> dict:
        """One page of the menu: the matching pieces (one per design, unless `family` asks for
        one design's colours and sizes), and how many fall under each section and collection."""
        matched, score = self.match(query)
        cats = np.array([p.cat for p in self.pieces])
        srcs = np.array([p.src for p in self.pieces])
        if family:
            matched = np.array([p.fam == family for p in self.pieces]) if self.pieces else matched
        in_cat = matched & (cats == category) if category else matched
        in_src = matched & (srcs == source) if source else matched
        chosen = np.flatnonzero(in_cat & in_src)
        order = chosen[np.argsort(-score[chosen], kind="stable")]

        def designs(indices) -> list[int]:
            seen, out = set(), []
            for n in indices:
                fam = self.pieces[n].fam
                if family or fam not in seen:
                    seen.add(fam)
                    out.append(int(n))
            return out

        shown = designs(order)
        if not query.strip() and not family:
            shown = self._showcase(shown, by="cls" if category else "cat")

        def count(mask, key_of) -> Counter:
            seen, counts = set(), Counter()
            for n in np.flatnonzero(mask):
                p = self.pieces[n]
                if (p.fam, key_of(p)) not in seen:
                    seen.add((p.fam, key_of(p)))
                    counts[key_of(p)] += 1
            return counts

        by_cat, by_src = count(in_src, lambda p: p.cat), count(in_cat, lambda p: p.src)
        present = {p.src for p in self.pieces}
        limit = max(1, min(limit, PAGE_MAX))
        return {
            "total": len(shown),
            "total_all": len(self.pieces),
            "items": [self.pieces[n] for n in shown[max(0, offset):max(0, offset) + limit]],
            "categories": [(key, label, by_cat.get(key, 0)) for key, label in CATEGORIES],
            "sources": [(key, label, by_src.get(key, 0)) for key, (label, _credit) in SOURCES.items() if key in present],
        }

    def _showcase(self, order: list[int], by: str) -> list[int]:
        """With nothing typed, the first pages show a bit of everything: the best of each section
        (or of each kind within a section) in turn, not four hundred chairs before the first lamp."""
        groups: dict[str, list[int]] = {}
        for n in order:
            groups.setdefault(getattr(self.pieces[n], by), []).append(n)
        out: list[int] = []
        queues = list(groups.values())
        while queues:
            queues = [q for q in queues if q]
            for q in queues:
                out.append(q.pop(0))
        return out

    # --- for the designer -----------------------------------------------------------------------

    def candidates(
        self, classes: tuple[str, ...] | list[str], *, phrases: tuple[str, ...] | list[str] = (), anchors: list[tuple[str, float]] | None = None,
        avoid: tuple[str, ...] | list[str] = (), size: dict[str, tuple[float, float]] | None = None, tones: tuple[str, ...] = (),
        materials: tuple[str, ...] = (), auto_only: bool = True, exclude_families: set[str] | None = None, want: tuple[str, ...] = (),
        only_cat: str | None = None, not_cats: tuple[str, ...] = (), k: int = 12,
    ) -> list[tuple[Piece, float]]:
        """The pieces of these kinds that suit a look and go with what is already chosen, best first.

        phrases: the look in words ("art deco", "emerald velvet"). Phrases of the vocabulary are
        compared by look vector; every word is also looked for in the piece's own description.
        anchors: (library id, weight) of pieces already chosen for the room: candidates that go
        with them rank higher (the compat vectors: pieces designed for one room are close).
        size: {"w" | "d" | "h" | "long" | "short": (least, most)} in metres; long and short are
        the sides of the footprint whichever way it lies. tones, materials: welcome colours and
        materials. want: words the piece must be described by ("round", "bunk"). avoid: words it
        must not be. only_cat, not_cats: sections of the menu to keep to or to leave out (a patio
        is furnished from "outdoor"; a living room never is)."""
        pool = [
            p for p in self.pieces
            if p.cls in classes and (p.auto or not auto_only) and (not exclude_families or p.fam not in exclude_families)
            and (only_cat is None or p.cat == only_cat) and p.cat not in not_cats
            and all(lo <= getattr(p, axis) <= hi for axis, (lo, hi) in (size or {}).items())
        ]
        banned, needed = words(" ".join(avoid)), words(" ".join(want))
        pool = [p for p in pool if not any(w in p.weights for w in banned) and all(w in p.weights for w in needed)]
        if not pool:
            return []
        idx = np.array([p.n for p in pool])

        def spread(values: np.ndarray) -> np.ndarray:
            """Scores made comparable: how many deviations a piece is above the pool's average."""
            sd = float(values.std())
            return (values - float(values.mean())) / sd if sd > 1e-6 else np.zeros_like(values)

        score = np.zeros(len(pool), dtype=np.float32)
        said = [w for phrase in phrases for w in words(phrase)]
        likeness = [s[idx] for phrase in phrases if (s := self.looks_like(phrase)) is not None]
        if likeness:
            score += 1.0 * spread(np.mean(likeness, axis=0))
        if said:
            score += 1.2 * np.array([p.says(said) for p in pool], dtype=np.float32)
        space = self.compat if self.compat is not None else self.look
        if anchors and space is not None:
            mine = [(self.by_id[a].row, weight) for a, weight in anchors if a in self.by_id and self.by_id[a].row >= 0]
            have = self._rows[idx] >= 0
            if mine and have.any():
                target = sum(space[row] * weight for row, weight in mine)
                target = target / (np.linalg.norm(target) or 1.0)
                goes = np.zeros(len(pool), dtype=np.float32)
                goes[have] = space[self._rows[idx][have]] @ target
                score += 1.3 * spread(goes) * have
        score += 0.8 * (np.array([p.lux for p in pool], dtype=np.float32) - 0.5)
        if tones:
            score += 0.5 * np.array([p.tone in tones for p in pool], dtype=np.float32)
        if materials:
            score += 0.3 * np.array([len(set(p.materials) & set(materials)) for p in pool], dtype=np.float32)
        order = np.argsort(-score, kind="stable")[:k]
        return [(pool[int(n)], float(score[int(n)])) for n in order]


@lru_cache(maxsize=1)
def library() -> Library:
    """The process's library, read once."""
    lib = Library()
    log.info("furniture library: %d pieces, look vectors: %s", len(lib.pieces), "yes" if lib.look is not None else "no")
    return lib
