"""The measurements and pictures behind the choices in embed.py. Offline, never run by the API.

    python -m services.api.library.build.embed_check <what>      (same `uv run` line as embed.py)

    models    the candidate models side by side (each needs its raw vectors: embed.py --only raw --model ...)
    mix       how much picture and how much title, centring, the size of the look space, the phrase wordings
    tags      colours, styles and materials against what the makers and the catalog say
    compat    the ways of training the compatibility projection, on the validation lines; who made it, as a probe
    weights   how the run-time should weigh "suits these phrases" against "goes with these pieces"
    sheets    contact sheets of phrase and "goes with" queries, from the written files, to look at

What counts as right, where there is no hand-made answer: ABO's listings carry the maker's own
fields for material, style and colour, apart from the title. A phrase query is scored by how high
it ranks the pieces whose listing says the same (average precision within one class), also over
only the pieces whose title does NOT say it, where the picture has to do the work.
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import logging
import re

import numpy as np

from services.api.library import ARTIFACTS
from services.api.library.build import embed as E
from services.api.library.build import embed_compat as C
from services.api.library.build import embed_words as W

log = logging.getLogger("library.embed")
SHEETS = E.DATA / "sheets"


# --------------------------------------------------------------------------------------------
# What the makers say


def abo_labels() -> dict[str, dict]:
    """ABO product id -> the listing's own words: {"material", "style", "colour"} as lower-case
    text, and "code" (a hex colour, where the listing has one)."""
    cache = E.DATA / "abo_labels.json"
    if cache.exists():
        return json.loads(cache.read_text())

    def said(listing: dict, field: str) -> str:
        return " ; ".join(str(v.get("value", "")) for v in listing.get(field) or [] if isinstance(v, dict) and str(v.get("language_tag", "en")).startswith("en"))

    labels: dict[str, dict] = {}
    for path in sorted(E.ABO_META.parent.glob("listings_*.json.gz")):
        with gzip.open(path, "rt") as lines:
            for line in lines:
                if '"3dmodel_id"' not in line:
                    continue
                listing = json.loads(line)
                entry = labels.setdefault(listing["3dmodel_id"], {"material": "", "style": "", "colour": "", "code": None})
                entry["material"] += " ; " + " ; ".join(said(listing, field) for field in ("material", "fabric_type", "finish_type"))
                entry["style"] += " ; " + said(listing, "style")
                standard = " ".join(s for v in listing.get("color") or [] if str(v.get("language_tag", "")).startswith("en") for s in v.get("standardized_values") or [])
                entry["colour"] += " ; " + said(listing, "color") + " " + standard
                entry["code"] = entry["code"] or next(iter(listing.get("color_code") or []), None)
    labels = {key: {**entry, **{field: entry[field].lower() for field in ("material", "style", "colour")}} for key, entry in labels.items()}
    cache.write_text(json.dumps(labels))
    return labels


def _mentions(text: str, phrase: str) -> bool:
    phrase = phrase.replace("grey", "gr[ae]y").replace("colour", "colou?r")
    return re.search(rf"(?<![a-z]){phrase}(?![a-z])", text) is not None


def phrase_truth(pieces: list[dict]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """phrase -> (which pieces' listings say it, which pieces' titles say it), for the phrases of
    the vocabulary that enough listings say."""
    labels = abo_labels()
    field_of = {"material": "material", "colour": "colour", "style": "style"}
    listing = [labels.get(p["id"].split(":", 1)[1], {}) if p["id"].startswith("abo:") else {} for p in pieces]
    titles = [(p.get("full") or p.get("name") or "").lower() for p in pieces]
    truth = {}
    for phrase in W.PHRASES:
        kind = W.KIND_OF[phrase]
        if kind not in field_of or " and " in phrase:
            continue
        key = "mid-century" if phrase == "mid-century modern" else phrase
        says = np.array([_mentions(entry.get(field_of[kind], ""), key) for entry in listing])
        if says.sum() >= 12:
            truth[phrase] = (says, np.array([_mentions(title, key) for title in titles]))
    return truth


def average_precision(scores: np.ndarray, right: np.ndarray) -> float:
    order = np.argsort(-scores, kind="stable")
    hits = right[order]
    if not hits.any():
        return float("nan")
    found = np.cumsum(hits)
    return float((found[hits] / (np.flatnonzero(hits) + 1)).mean())


def phrase_scores(look: np.ndarray, vocab: np.ndarray, phrases: list[str], pieces: list[dict], truth: dict) -> dict[str, float]:
    """Mean average precision of phrase queries within a class, by kind of phrase: over every
    piece ("material"), and over only the pieces whose title does not say the phrase ("material, unsaid")."""
    classes = np.array([p["cls"] for p in pieces])
    at = {phrase: i for i, phrase in enumerate(phrases)}
    found: dict[str, list[float]] = collections.defaultdict(list)
    for phrase, (says, titled) in truth.items():
        scores = look @ vocab[at[phrase]]
        for cls in set(classes):
            members = classes == cls
            if members.sum() < 40 or cls == "other":
                continue
            if 3 <= (says & members).sum() <= 0.5 * members.sum():
                found[W.KIND_OF[phrase]].append(average_precision(scores[members], says[members]))
            quiet = members & ~titled
            if 3 <= (says & quiet).sum() <= 0.5 * quiet.sum():
                found[W.KIND_OF[phrase] + ", unsaid"].append(average_precision(scores[quiet], says[quiet]))
    return {kind: float(np.mean(values)) for kind, values in sorted(found.items())} | {"queries": sum(len(v) for k, v in found.items() if "unsaid" not in k)}


def chance(pieces: list[dict], truth: dict) -> dict[str, float]:
    """What a random order would score, for scale."""
    rng = np.random.default_rng(0)
    noise = rng.normal(size=(len(pieces), 8)).astype(np.float32)
    return phrase_scores(noise, rng.normal(size=(len(W.PHRASES), 8)).astype(np.float32), W.PHRASES, pieces, truth)


def _show(name: str, scores: dict) -> str:
    return f"{name:34s}" + "  ".join(f"{key} {value:.3f}" if isinstance(value, float) else f"{key} {value}" for key, value in scores.items())


def _line_recall(vectors: np.ndarray, ready: E.Prepared, part: str = "train") -> float:
    groups = C.design_groups(ready.pieces, ready.lines)
    return C.rank_metrics(vectors, ready.pieces, groups, C.split_groups(groups)[part])["recall@10"]


# --------------------------------------------------------------------------------------------
# models, mix


def models() -> None:
    """Each model on the same questions. Picture alone, title alone and both, so a model whose
    text side is strong and picture side weak shows as such."""
    for model in E.MODELS:
        if not (E.DATA / "raw" / model / "image.f32.npy").exists():
            print(f"{model}: no raw vectors (run embed.py --only raw --model {model})")
            continue
        ready = E.prepare(model)
        truth = phrase_truth(ready.pieces)
        vocab = E.phrase_vectors(ready.encoder, W.PHRASES, W.KIND_OF)
        print(f"== {model}: {len(ready.pieces)} pieces, {len(truth)} phrases with listings to check against")
        for name, vectors in (("picture only", ready.image), ("title only", ready.text), ("half and half", E.mix(ready.image, ready.text, 0.5))):
            print(_show("  " + name, phrase_scores(vectors, vocab, W.PHRASES, ready.pieces, truth)),
                  f" same-line recall@10 {_line_recall(vectors, ready):.3f}  (class average out {_line_recall(C.class_centred(vectors, ready.pieces), ready):.3f})")
        # A title should find its own picture: how well the two sides agree at all.
        own = ready.text @ ready.image.T
        rank = (own > np.diag(own)[:, None]).sum(axis=1)
        print(f"  a title finds its own picture: first {np.mean(rank == 0):.3f}, in the first ten {np.mean(rank < 10):.3f}")
    ready = E.prepare()
    print(_show("chance", chance(ready.pieces, phrase_truth(ready.pieces))))


def mix() -> None:
    ready = E.prepare()
    pieces, truth = ready.pieces, phrase_truth(ready.pieces)
    vocab = E.phrase_vectors(ready.encoder, W.PHRASES, W.KIND_OF)
    as_picture = E.title_as_picture(ready.image, ready.text)

    def line(name: str, full: np.ndarray, words: np.ndarray = vocab) -> None:
        print(_show(name, phrase_scores(full, words, W.PHRASES, pieces, truth)),
              f" same-line recall@10 {_line_recall(full, ready):.3f} / class average out {_line_recall(C.class_centred(full, pieces), ready):.3f}")

    print("-- the title added as the text model gives it (why that is not done)")
    line("  title only", ready.text)
    line("  picture 0.5 + title 0.5", E.unit(0.5 * ready.image + 0.5 * ready.text))
    print("-- how much of the look is the picture, the rest the title moved into picture space (full size, not centred)")
    for share in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.3, 0.0):
        line(f"  picture share {share:.1f}", E.unit(share * ready.image + (1 - share) * as_picture))
    full = E.mix(ready.image, ready.text)
    print(f"-- wording of the phrases (picture share {E.PICTURE_SHARE}); the last four with the average phrase left in")
    line("  as shipped (by kind)", full)
    line("  as shipped, average phrase left in", full, E.phrase_vectors(ready.encoder, W.PHRASES, W.KIND_OF, apart=False))
    line("  the phrase alone", full, E.phrase_vectors(ready.encoder, W.PHRASES, W.KIND_OF, ensemble=False, apart=False))
    for name, templates in (("phrase + '… furniture'", ("{}", "{} furniture")), ("'a photo of … furniture'", ("a photo of {} furniture",)),
                            ("five wordings", ("{}", "{} style furniture", "a {} interior", "{} home decor", "a piece of {} furniture"))):
        line(f"  {name}", full, E._prototypes(ready.encoder, [[t.format(phrase) for t in templates] for phrase in W.PHRASES]))
    print("-- whose pieces a phrase brings up: the share of 3D renders (Objaverse) among the twenty best of a class, by kind of phrase")
    source, classes = np.array([p["src"] for p in pieces]), np.array([p["cls"] for p in pieces])
    for name, words in (("as shipped", vocab), ("average phrase left in", E.phrase_vectors(ready.encoder, W.PHRASES, W.KIND_OF, apart=False))):
        for group in (("sofa", "loveseat", "sectional"), ("coffee_table", "side_table", "console_table", "dining_table"), ("chair", "armchair")):
            pool = np.flatnonzero(np.isin(classes, group))
            shares = {kind: np.mean([np.mean(source[pool[np.argsort(-(full[pool] @ words[i]))[:20]]] == "objaverse") for i, ph in enumerate(W.PHRASES) if W.KIND_OF[ph] == kind])
                      for kind in W.VOCAB}
            print(f"  {name:24s} {group[0]:13s} renders are {np.mean(source[pool] == 'objaverse'):.2f} of the class; of the twenty best: " + ", ".join(f"{k} {v:.2f}" for k, v in shares.items()))
    print("-- size of the look space: scores, and how much of the full-size answer survives")
    classes = np.array([p["cls"] for p in pieces])
    rng = np.random.default_rng(0)
    sample = rng.choice(len(pieces), size=400, replace=False)
    near_full = np.argsort(-(full[sample] @ full.T), axis=1)[:, 1:11]
    big = [cls for cls, n in collections.Counter(classes).items() if n >= 60 and cls != "other"]
    for centre in (False, True):
        for dim in (32, 64, 128, 192, 256, 384):
            basis = E.fit_basis(full, dim, centre)
            look, words = E.project(full, basis), E.project(vocab, basis)
            near = np.argsort(-(look[sample] @ look.T), axis=1)[:, 1:11]
            same_near = np.mean([len(set(a) & set(b)) / 10 for a, b in zip(near, near_full)])
            same_top = []
            for cls in big:
                members = np.flatnonzero(classes == cls)
                top_full = np.argsort(-(full[members] @ vocab.T), axis=0)[:20]
                top = np.argsort(-(look[members] @ words.T), axis=0)[:20]
                same_top += [len(set(top[:, j]) & set(top_full[:, j])) / 20 for j in range(len(W.PHRASES))]
            print(_show(f"  {dim:3d} numbers, {'centred' if centre else 'as is  '}", phrase_scores(look, words, W.PHRASES, pieces, truth)),
                  f" same-line {_line_recall(look, ready):.3f}  ten nearest kept {same_near:.2f}  phrase top-20 kept {np.mean(same_top):.2f}")
    line("  full size", full)


# --------------------------------------------------------------------------------------------
# tags


def tags() -> None:
    from PIL import Image

    from services.api.library.build import embed_colour as P
    from services.api.objects.providers.catalog import SETS

    out = E.describe(E.prepare())
    ready = out["ready"]
    pieces, labels = ready.pieces, abo_labels()
    listing = [labels.get(p["id"][4:], {}) if p["id"].startswith("abo:") else {} for p in pieces]

    print("-- colour: the tone against the maker's own colour word (where the listing has one)")
    said = []
    for entry in listing:
        found = [P.TONE_WORDS[w] for w in re.findall(r"[a-z-]+", entry.get("colour", "")) if w in P.TONE_WORDS]
        said.append(found[0] if found else None)
    rng = np.random.default_rng(0)
    for kind in ("studio", "flat", "room"):
        rows = [i for i, k in enumerate(ready.kinds) if k == kind and said[i] and said[i] != "multicolour"]
        if not rows:
            continue
        same = np.mean([ready.tones[i] == said[i] for i in rows])
        near = np.mean([ready.tones[i] == said[i] or ready.tones[i] in P._NEAR.get(said[i], ()) for i in rows])
        # The same from the pixels alone, without the maker's word to lean on.
        sample = rng.choice(rows, size=min(600, len(rows)), replace=False)
        blind = [P.palette(np.asarray(Image.open(E.thumb_path(pieces[i]["picture"]))), kind, None, pieces[i]["cls"] in E._PALE_LAST)[1] for i in sample]
        blind_same = np.mean([tone == said[i] for tone, i in zip(blind, sample)])
        blind_near = np.mean([tone == said[i] or tone in P._NEAR.get(said[i], ()) for tone, i in zip(blind, sample)])
        print(f"  {kind:7s} {len(rows):5d} pieces: same word {same:.2f}, same or a neighbouring word {near:.2f}   pixels alone: {blind_same:.2f} / {blind_near:.2f}")
    coded = [(i, entry["code"]) for i, entry in enumerate(listing) if entry.get("code") and re.fullmatch(r"#[0-9a-fA-F]{6}", entry["code"])]
    apart = [min(np.linalg.norm(P.lab_of_hex(colour) - P.lab_of_hex(code.lower())) for colour in ready.palettes[i]) for i, code in coded]
    print(f"  {len(coded)} listings give a hex colour: the nearest palette colour is {np.median(apart):.0f} apart in CIELAB at the median, within 20 for {np.mean(np.array(apart) < 20):.2f}")
    print("  tones:", ", ".join(f"{tone} {n}" for tone, n in collections.Counter(ready.tones).most_common()))

    print("-- styles: against the catalog's sets (a piece's set's style among its one or two)")
    at = {p["id"]: i for i, p in enumerate(pieces)}
    right, total, unsaid_right, unsaid_total = 0, 0, 0, 0
    wrong = collections.Counter()
    for room in SETS:
        for piece_id in room["pieces"]:
            i = at.get(f"abo:{piece_id}")
            if i is None:
                continue
            words = E._said(pieces[i])
            named = any(f" {key} " in words for name, keys, _ in W.STYLES if name == room["style"] for key in keys)
            hit = room["style"] in out["styles"][i]
            right, total = right + hit, total + 1
            if not named:
                unsaid_right, unsaid_total = unsaid_right + hit, unsaid_total + 1
                if not hit:
                    wrong[(room["style"], out["styles"][i][0])] += 1
    print(f"  {right}/{total} right; where the title does not name the style: {unsaid_right}/{unsaid_total}")
    print("  most common misses (set's style, first tag):", wrong.most_common(8))
    print("  styles:", ", ".join(f"{name} {n}" for name, n in collections.Counter(s for styles in out["styles"] for s in styles).most_common()))

    print("-- materials: against the listing's material fields")
    for name, keys, _ in W.MATERIALS:
        says = np.array([any(_mentions(entry.get("material", ""), key) for key in keys) for entry in listing])
        tagged = np.array([name in materials for materials in out["materials"]])
        titled = np.array([any(f" {key} " in E._said(p) for key in keys) for p in pieces])
        quiet = ~titled & np.array([bool(entry.get("material", "").strip(" ;")) for entry in listing])
        if says.sum() >= 20:
            print(f"  {name:13s} tagged {tagged.sum():5d}  listing agrees for {np.mean(says[tagged]) if tagged.any() else 0:.2f} of them, tag finds {np.mean(tagged[says]):.2f} of the listing's;"
                  f"  title silent: {int((tagged & quiet).sum()):4d} tagged, listing agrees {np.mean(says[tagged & quiet]) if (tagged & quiet).any() else 0:.2f}")

    print("-- lux: the average by maker (rank within class, so 0.5 is the middle)")
    makers = collections.defaultdict(list)
    for p, value in zip(pieces, out["lux"]):
        makers[p["maker"] or p["src"]].append(value)
    print("  " + ", ".join(f"{maker} {np.mean(values):.2f} ({len(values)})" for maker, values in sorted(makers.items(), key=lambda kv: -len(kv[1]))[:9]))


# --------------------------------------------------------------------------------------------
# compat


def compat() -> None:
    """Ways of training the projection, scored on the validation lines (never on the test lines:
    those are for the table embed.py prints), then a probe for the maker."""
    ready = E.prepare()
    pieces = ready.pieces
    full = E.mix(ready.image, ready.text)
    look = E.project(full, E.fit_basis(full))
    groups = C.design_groups(pieces, ready.lines)
    split = C.split_groups(groups)
    centred = C.class_centred(look, pieces)

    def scores(vectors: np.ndarray, part: str = "val") -> str:
        everyone, own = C.rank_metrics(vectors, pieces, groups, split[part]), C.rank_metrics(vectors, pieces, groups, split[part], own_maker=True)
        return f"recall@10 {everyone['recall@10']:.3f} mrr {everyone['mrr']:.3f} | own maker {own['recall@10']:.3f} {own['mrr']:.3f}"

    print(f"{len(groups)} groups: {split and {k: len(v) for k, v in split.items()}}; validation lines only below")
    print(f"  {'look as it is':44s}{scores(look)}")
    print(f"  {'look, class average out':44s}{scores(centred)}")
    print(f"  {'colour only':44s}{scores(C.colour_vectors(ready.palettes))}")
    base = dict(E.COMPAT_TRAINING)
    for name, source, change in (
        ("shipped recipe", centred, {}),
        ("the same, another seed", centred, {"seed": 11}),
        ("on the look as it is", look, {}),
        ("on the pictures alone, full size", C.class_centred(ready.image, pieces), {}),
        ("one hidden layer of 512", centred, {"hidden": 512}),
        ("temperature 0.05", centred, {"temperature": 0.05}),
        ("temperature 0.12", centred, {"temperature": 0.12}),
        ("no dropout", centred, {"dropout": 0.0}),
        ("dropout 0.3", centred, {"dropout": 0.3}),
        ("held near its start (0.02)", centred, {"stay": 0.02}),
        ("no same-class negatives", centred, {"hard": 0}),
        ("32 numbers", centred, {"dim": 32}),
        ("128 numbers", centred, {"dim": 128}),
    ):
        settings = {"dim": E.COMPAT_DIM, **base, **change}
        vectors, record = C.train_projection(source, pieces, groups, split["train"], split["val"], **settings)
        print(f"  {name:44s}{scores(vectors)}   best step {record['best_step']}")

    print("-- can the maker be read off the vectors? (5-fold, the five largest makers, same number of pieces each)")
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    shipped, _ = C.train_projection(centred, pieces, groups, split["train"], split["val"], dim=E.COMPAT_DIM, **base)
    makers = np.array([p["maker"] for p in pieces])
    largest = [maker for maker, _ in collections.Counter(m for m in makers if m).most_common(5)]
    rng = np.random.default_rng(0)
    fewest = min((makers == maker).sum() for maker in largest)
    rows = np.concatenate([rng.choice(np.flatnonzero(makers == maker), size=fewest, replace=False) for maker in largest])
    for name, vectors in (("look, first 64 directions", E.project(look, E.fit_basis(look, 64))), ("look, class average out, first 64", E.project(centred, E.fit_basis(centred, 64))), ("learned", shipped)):
        accuracy = cross_val_score(LogisticRegression(max_iter=2000), vectors[rows], makers[rows], cv=5).mean()
        print(f"  {name:36s} maker guessed right {accuracy:.3f}  (chance {1 / len(largest):.2f})")


# --------------------------------------------------------------------------------------------
# weights: how the run-time should put the scores together


def _described_by(piece: dict, styles: list[str], materials: list[str], tone: str) -> set[str]:
    """The words Library says a piece is described by (index.py, Piece.weights), near enough."""
    from services.api.library.index import _CLASS_WORDS, words

    text = " ".join([piece.get("name") or "", piece.get("full") or "", _CLASS_WORDS.get(piece["cls"], piece["cls"].replace("_", " ")),
                     " ".join(piece.get("tags") or []), " ".join(styles), " ".join(materials), tone or "", piece.get("color") or "",
                     piece.get("brand") or "", piece.get("line") or ""])
    return set(words(text))


def weights() -> None:
    """Replays Library.candidates on rooms whose answer is known and that the projection under
    test never saw: the held-out catalog sets (the look in the set's own words, its first piece
    as the anchor, every other piece to be found among all pieces of its class) and the held-out
    design lines (the anchor's own style and tone as the words). Prints how high the right piece
    comes for the weights index.py has now and for others."""
    from services.api.library.index import words
    from services.api.objects.providers.catalog import SET_BY_ID

    out = E.build(write=False)
    ready, report = out["ready"], out["report"]
    pieces, look, vocab = ready.pieces, out["look"], out["vocab"]
    learned, groups, test = report["tested"], report["groups"], report["names"]["test"]
    classes = np.array([p["cls"] for p in pieces])
    at = {p["id"]: i for i, p in enumerate(pieces)}
    phrase_row = {phrase: i for i, phrase in enumerate(W.PHRASES)}
    described = [_described_by(p, out["styles"][i], out["materials"][i], ready.tones[i]) for i, p in enumerate(pieces)]
    lux = out["lux"]

    def spread(values: np.ndarray) -> np.ndarray:
        return (values - values.mean()) / values.std() if values.std() > 1e-6 else np.zeros_like(values)

    # (phrases, anchor rows, the row to find)
    tasks: dict[str, list[tuple[list[str], list[int], int]]] = {"sets, one anchor": [], "sets, two anchors": [], "lines, one anchor": []}
    for name in test:
        rows = groups[name]
        if name.startswith("set:"):
            room = SET_BY_ID[name[4:]]
            said = room["look"].lower()
            phrases = [room["style"].lower()] + [ph for ph in W.PHRASES if W.KIND_OF[ph] != "mood" and _mentions(said, ph) and ph != room["style"].lower()]
            order = [at[f"abo:{i}"] for i in room["pieces"] if f"abo:{i}" in at]
            for count, key in ((1, "sets, one anchor"), (2, "sets, two anchors")):
                tasks[key] += [(phrases, order[:count], target) for target in order[count:] if classes[target] not in {classes[a] for a in order[:count]}]
        else:
            for anchor in rows:
                phrases = [s.lower() for s in out["styles"][anchor] if s.lower() in phrase_row] + [ready.tones[anchor]] * (ready.tones[anchor] in phrase_row)
                tasks["lines, one anchor"] += [(phrases, [anchor], target) for target in rows if classes[target] != classes[anchor] and pieces[target]["picture"] != pieces[anchor]["picture"]]

    def run(key: str, w_look: float, w_words: float, w_goes: float, w_lux: float, space: np.ndarray) -> tuple[float, float, float]:
        ranks = []
        for phrases, anchors, target in tasks[key]:
            pool = np.flatnonzero(classes == classes[target])
            score = np.zeros(len(pool), dtype=np.float32)
            if phrases and w_look:
                score += w_look * spread(np.mean([look[pool] @ vocab[phrase_row[ph]] for ph in phrases if ph in phrase_row], axis=0))
            said = [w for ph in phrases for w in words(ph)]
            if said and w_words:
                score += w_words * np.array([sum(w in described[i] for w in said) / len(said) for i in pool], dtype=np.float32)
            if w_goes:
                goal = space[anchors].sum(axis=0)
                score += w_goes * spread(space[pool] @ (goal / np.linalg.norm(goal)))
            score += w_lux * (lux[pool] - 0.5)
            ranks.append(int((score > score[pool == target][0]).sum()))
        ranks = np.array(ranks)
        return float(np.mean(1 / (ranks + 1))), float(np.mean(ranks < 10)), float(np.median(ranks))

    for key, items in tasks.items():
        print(f"-- {key}: {len(items)} pieces to find (first-ten share, mean 1/rank, median rank)")
        for name, setting in (
            ("as index.py has it  1.0 1.2 1.3 0.8", (1.0, 1.2, 1.3, 0.8, learned)),
            ("the same, look space for anchors", (1.0, 1.2, 1.3, 0.8, look)),
            ("no lux", (1.0, 1.2, 1.3, 0.0, learned)),
            ("lux 0.4", (1.0, 1.2, 1.3, 0.4, learned)),
            ("anchors only", (0.0, 0.0, 1.0, 0.0, learned)),
            ("anchors only, look space", (0.0, 0.0, 1.0, 0.0, look)),
            ("phrases only (look + words)", (1.0, 1.2, 0.0, 0.0, learned)),
            ("look likeness only", (1.0, 0.0, 0.0, 0.0, learned)),
            ("words only", (0.0, 1.2, 0.0, 0.0, learned)),
            ("anchors 2.0", (1.0, 1.2, 2.0, 0.4, learned)),
            ("anchors 3.0", (1.0, 1.2, 3.0, 0.4, learned)),
            ("anchors 2.0, words 0.6", (1.0, 0.6, 2.0, 0.4, learned)),
            ("anchors 2.0, words 0.6, look 0.5", (0.5, 0.6, 2.0, 0.4, learned)),
            ("anchors 2.0, look 1.5", (1.5, 1.2, 2.0, 0.4, learned)),
            ("anchors 2.0, no words", (1.0, 0.0, 2.0, 0.4, learned)),
            ("anchors 2.0, no look likeness", (0.0, 1.2, 2.0, 0.4, learned)),
        ):
            mrr, top, median = run(key, *setting)
            print(f"  {name:40s} first ten {top:.3f}   1/rank {mrr:.3f}   median rank {median:.0f}")


# --------------------------------------------------------------------------------------------
# sheets: pictures to look at

# class group -> the phrases to try in it
_PHRASE_SHEETS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "sofas": (("sofa", "loveseat", "sectional"), ("emerald velvet", "cognac leather", "boucle", "quiet luxury", "mid-century modern", "navy velvet",
                                                 "blush", "japandi", "old money", "hollywood regency", "cozy", "sleek")),
    "armchairs": (("armchair",), ("velvet", "leather", "rattan", "traditional", "blush velvet", "tufted", "scandinavian", "glam", "dark academia",
                                 "curved", "mustard", "english country house")),
    "lamps": (("floor_lamp", "table_lamp", "ceiling_light"), ("brass", "black metal", "art deco", "industrial", "ceramic", "crystal", "rustic", "glam",
                                                             "marble", "minimalist", "coastal", "gilded")),
    "tables": (("coffee_table", "side_table", "console_table", "dining_table"), ("japandi", "marble and gold", "walnut", "glass", "industrial", "farmhouse",
                                                                                "art deco", "rattan", "black metal", "live edge wood", "sculptural", "boutique hotel")),
    "rugs": (("rug",), ("persian pattern", "jute", "geometric pattern", "navy", "blush", "shag", "moroccan trellis", "black and white", "earth tones",
                       "bohemian", "pastel", "moody")),
    "art": (("wall_art", "mirror"), ("abstract pattern", "botanical", "black and white", "gold accents", "coastal", "dark academia", "pastel",
                                     "mid-century modern", "moody", "ocean blues", "parisian apartment", "70s retro")),
    "decor": (("decor", "plant"), ("ceramic", "brass", "terracotta pottery", "bohemian", "black and white", "glass", "rattan", "emerald", "gilded",
                                   "wabi-sabi", "art deco", "navy")),
    "storage": (("dresser", "cabinet", "bookshelf", "tv_stand", "nightstand", "wardrobe"), ("walnut", "painted wood", "industrial", "rattan", "mid-century modern",
                                                                                           "farmhouse", "high gloss", "dark wood", "light wood", "glam", "coastal", "art deco")),
    "beds": (("bed",), ("upholstered", "tufted", "rustic", "navy velvet", "rattan", "farmhouse", "glam", "minimalist", "light wood", "dark wood",
                       "wrought iron", "quiet luxury")),
}
# Pieces to find company for, and the classes to find it in.
_ANCHORS = ("abo:B072FVHS9G", "abo:B082VM4LMQ", "abo:B075X2WN11", "abo:B07B4MG9HX", "abo:B07B4MKFR5", "abo:B07QBMQ4LN")
_COMPANY = ("coffee_table", "side_table", "rug", "floor_lamp", "table_lamp", "wall_art", "armchair", "decor", "plant")


def _sheet(rows: list[tuple[str, list[tuple[dict, str]]]], path, cell: int = 104, across: int = 11, margin: int = 190) -> None:
    """Rows of pictures, each with a label at its left and a short note under each picture."""
    from PIL import Image, ImageDraw

    sheet = Image.new("RGB", (margin + across * cell, len(rows) * (cell + 14)), "white")
    draw = ImageDraw.Draw(sheet)
    for r, (label, items) in enumerate(rows):
        top = r * (cell + 14)
        for n, line in enumerate(label.split("\n")):
            draw.text((4, top + 6 + 12 * n), line, fill="black")
        for c, (piece, note) in enumerate(items[:across]):
            try:
                thumb = Image.open(E.thumb_path(piece["img"])).convert("RGBA")
            except OSError:
                continue
            thumb.thumbnail((cell - 6, cell - 6))
            left = margin + c * cell
            sheet.paste(thumb, (left + (cell - thumb.width) // 2, top + (cell - thumb.height) // 2), thumb)
            draw.text((left + 3, top + cell), note[:17], fill=(90, 90, 90))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, quality=86)


def sheets() -> None:
    """Draws from the written files only, read the way the API reads them."""
    ids = json.loads((ARTIFACTS / "look_ids.json").read_text())
    look = np.load(ARTIFACTS / "look.f16.npy").astype(np.float32)
    goes = np.load(ARTIFACTS / "compat.f16.npy").astype(np.float32)
    words = json.loads((ARTIFACTS / "vocab.json").read_text())
    vocab = dict(zip(words["phrases"], np.load(ARTIFACTS / "vocab.f16.npy").astype(np.float32)))
    with gzip.open(ARTIFACTS / "looks.jsonl.gz", "rt", encoding="utf-8") as lines:
        looks = {row["id"]: row for row in map(json.loads, lines)}
    known = {p["id"]: p for p in E.load_pieces()[0]}
    pieces = [known.get(i) for i in ids]
    classes = np.array([p["cls"] if p else "" for p in pieces])

    def note(i: int) -> str:
        row = looks[ids[i]]
        return f"{row['tone']} {row['lux']:.2f}"

    for name, (group, phrases) in _PHRASE_SHEETS.items():
        pool = np.flatnonzero(np.isin(classes, group))
        rows = []
        for phrase in phrases:
            order = pool[np.argsort(-(look[pool] @ vocab[phrase]))]
            rows.append((f"{phrase}\n({W.KIND_OF[phrase]})", [(pieces[i], note(i)) for i in order[:11]]))
        _sheet(rows, SHEETS / f"phrases_{name}.jpg")
    at = {i: n for n, i in enumerate(ids)}
    for anchor in _ANCHORS:
        if anchor not in at:
            continue
        a = at[anchor]
        for space_name, space in (("compat", goes), ("look", look)):
            rows = [(f"ANCHOR {anchor}\n{(pieces[a].get('name') or '')[:28]}\n{', '.join(looks[anchor]['styles'])}\n{', '.join(looks[anchor]['materials'])}",
                     [(pieces[a], note(a))])]
            for cls in _COMPANY:
                pool = np.flatnonzero(classes == cls)
                order = pool[np.argsort(-(space[pool] @ space[a]))]
                rows.append((f"{cls}\nby {space_name}", [(pieces[i], note(i)) for i in order[:11]]))
            _sheet(rows, SHEETS / f"goes_{anchor.split(':')[1]}_{space_name}.jpg")
    # The tags themselves: a random page with what each piece was given.
    rng = np.random.default_rng(1)
    for page in range(2):
        sample = rng.choice(len(ids), size=44, replace=False)
        _tag_sheet([(pieces[i], looks[ids[i]]) for i in sample if pieces[i]], SHEETS / f"tags_{page}.jpg")
    # The highest and lowest lux of a few classes.
    rows = []
    for cls in ("sofa", "armchair", "coffee_table", "floor_lamp", "rug", "bed", "dresser"):
        pool = np.flatnonzero(classes == cls)
        order = pool[np.argsort([-looks[ids[i]]["lux"] for i in pool])]
        rows.append((f"{cls}\nhighest lux", [(pieces[i], note(i)) for i in order[:11]]))
        rows.append((f"{cls}\nlowest lux", [(pieces[i], note(i)) for i in order[::-1][:11]]))
    _sheet(rows, SHEETS / "lux.jpg")
    print(f"sheets written to {SHEETS}")


def _tag_sheet(items: list[tuple[dict, dict]], path, cell: int = 116, across: int = 11) -> None:
    """Pictures with their tags written under them: styles, materials, tone and palette."""
    from PIL import Image, ImageDraw

    high = cell + 52
    sheet = Image.new("RGB", (across * cell, ((len(items) + across - 1) // across) * high), "white")
    draw = ImageDraw.Draw(sheet)
    for n, (piece, row) in enumerate(items):
        left, top = (n % across) * cell, (n // across) * high
        thumb = Image.open(E.thumb_path(piece["img"])).convert("RGBA")
        thumb.thumbnail((cell - 6, cell - 6))
        sheet.paste(thumb, (left + (cell - thumb.width) // 2, top + (cell - thumb.height) // 2), thumb)
        for k, colour in enumerate(row["palette"]):
            draw.rectangle([left + 3 + 15 * k, top + cell, left + 16 + 15 * k, top + cell + 10], fill=colour, outline="black")
        draw.text((left + 52, top + cell - 1), f"{row['tone']} {row['lux']:.2f}", fill="black")
        draw.text((left + 3, top + cell + 12), ", ".join(row["styles"])[:20], fill=(40, 40, 160))
        draw.text((left + 3, top + cell + 24), ", ".join(row["materials"])[:20], fill=(130, 60, 20))
        draw.text((left + 3, top + cell + 36), piece["cls"][:20], fill=(110, 110, 110))
    sheet.save(path, quality=86)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("what", choices=["models", "mix", "tags", "compat", "weights", "sheets"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    np.set_printoptions(precision=3, suppress=True)
    {"models": models, "mix": mix, "tags": tags, "compat": compat, "weights": weights, "sheets": sheets}[args.what]()


if __name__ == "__main__":
    main()
