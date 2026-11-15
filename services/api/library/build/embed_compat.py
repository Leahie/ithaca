"""Which pieces belong together: the data, the training and the test behind compat.f16.npy.

The look vectors put a sofa next to other sofas. A designer needs the other thing: given this
sofa, which coffee table, which rug. The only honest evidence of "these go together" in the
collections is what was designed together:

    lines   one maker's design line ("Rivet Sloane", "Stone & Beam Parson"): its sofa, its
            armchair, its ottoman; its bed, its nightstand, its dresser
    sets    the catalog's 27 hand-made room sets, which also cross makers and kinds (a sofa
            with a rug and a lamp)

A small projection of the look vectors is trained so that two pieces of one group and of
different classes come out close, and closer than other pieces of the second one's class.
Whether that worked is measured on groups the training never saw, against the plain look
vectors and against colour alone (rank_metrics). Only a projection that wins is shipped.

Two traps, and what is done about them:

    the class   A sofa is not more like a sofa-shaped table. Negatives are always of the same
                class as the positive, so knowing the class earns nothing.
    the maker   Every line belongs to one maker, and a maker's photos share a studio and a
                light. A projection could learn "same photographer" and score well. So half of
                the negatives come from the same maker as the positive, the test is also run
                with only the anchor's own maker's pieces as candidates (where knowing the
                maker is worth nothing), and the sets, which mix makers, are part of both
                training and test.
"""

from __future__ import annotations

import collections
import logging

import numpy as np

log = logging.getLogger("library.embed")

SEED = 7
HELD_OUT_LINES = 0.20  # of the lines that span two classes or more
VALIDATION_LINES = 0.10  # of the rest: when to stop training
HELD_OUT_SETS = 6  # of the catalog's 27


def unit(rows: np.ndarray) -> np.ndarray:
    return rows / np.maximum(np.linalg.norm(rows, axis=-1, keepdims=True), 1e-9)


def design_groups(pieces: list[dict], lines: dict[str, tuple[str, str]]) -> dict[str, list[int]]:
    """group name -> rows of its pieces. "line:rivet/Sloane", "set:sloane_living". Only groups
    with pieces of two classes or more: the rest say nothing about what goes with what."""
    from services.api.objects.providers.catalog import SETS

    row = {p["id"]: i for i, p in enumerate(pieces)}
    groups: dict[str, list[int]] = collections.defaultdict(list)
    for piece_id, (maker, line) in lines.items():
        if piece_id in row:
            groups[f"line:{maker}/{line}"].append(row[piece_id])
    for room in SETS:
        groups[f"set:{room['id']}"] = [row[f"abo:{i}"] for i in room["pieces"] if f"abo:{i}" in row]
    return {name: sorted(rows) for name, rows in groups.items() if len({pieces[i]["cls"] for i in rows}) >= 2}


def split_groups(groups: dict[str, list[int]], seed: int = SEED) -> dict[str, list[str]]:
    """{"train", "val", "test"} -> group names. A held-out set takes with it every line one of
    its pieces belongs to, so nothing of a test group was ever seen together in training."""
    rng = np.random.default_rng(seed)
    sets = sorted(name for name in groups if name.startswith("set:"))
    lines = sorted(name for name in groups if name.startswith("line:"))
    test = set(rng.permutation(sets)[:HELD_OUT_SETS].tolist())
    held = {i for name in test for i in groups[name]}
    test |= {name for name in lines if held & set(groups[name])}
    rest = [name for name in rng.permutation(lines).tolist() if name not in test]
    more = max(0, round(HELD_OUT_LINES * len(lines)) - sum(name.startswith("line:") for name in test))
    test |= set(rest[:more])
    rest = rest[more:]
    val = set(rest[:round(VALIDATION_LINES * len(rest))])
    train = [name for name in groups if name not in test and name not in val]
    return {"train": sorted(train), "val": sorted(val), "test": sorted(test)}


def _membership(groups: dict[str, list[int]], count: int) -> np.ndarray:
    """(pieces, most groups one piece is in): the numbers of the groups each piece is in, -1 padded.
    Two pieces that share a group are never each other's negative."""
    of: list[list[int]] = [[] for _ in range(count)]
    for number, rows in enumerate(groups.values()):
        for i in rows:
            of[i].append(number)
    width = max(1, max(len(g) for g in of))
    table = np.full((count, width), -1, dtype=np.int64)
    for i, numbers in enumerate(of):
        table[i, :len(numbers)] = numbers
    return table


def _related(table: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Whether pieces a[...] and b[...] (same shape) share a group."""
    ga, gb = table[a], table[b]
    return ((ga[..., :, None] == gb[..., None, :]) & (ga[..., :, None] >= 0)).any(axis=(-1, -2))


def train_projection(x: np.ndarray, pieces: list[dict], groups: dict[str, list[int]], train: list[str], val: list[str] | None,
                     dim: int = 64, steps: int = 1500, batch: int = 256, hard: int = 48, temperature: float = 0.07,
                     hidden: int = 0, dropout: float = 0.1, rate: float = 1e-3, decay: float = 0.05, stay: float = 0.0,
                     seed: int = SEED) -> tuple[np.ndarray, dict]:
    """Trains the projection and returns (every piece's compatibility vector, a record of the run).

    Each step takes pairs (anchor, positive) of one group and two classes, and for each pair a
    handful of pieces of the positive's class from other groups, half of them the positive's own
    maker's. The anchor must pick its positive out of those and out of the other pairs' positives.

    `hidden` > 0 puts one hidden layer in; 0 is a plain linear map. `stay` holds a linear map near
    where it started (the main directions of the input), so pieces unlike any in the training,
    another collection's renders, are not sent somewhere arbitrary. With `val` the best step by
    validation recall is kept; without it (the final run on every group) all `steps` are run.
    """
    import torch

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    classes = np.array([p["cls"] for p in pieces])
    makers = np.array([str(p.get("maker") or "") for p in pieces])
    pictures = np.array([p["picture"] for p in pieces])
    table = _membership(groups, len(pieces))
    barred = {i for name in groups if name not in train for i in groups[name]}  # held-out pieces never train

    # A group as {class: rows}, without the held-out pieces.
    by_class: list[dict[str, np.ndarray]] = []
    for name in train:
        parts: dict[str, list[int]] = collections.defaultdict(list)
        for i in groups[name]:
            if i not in barred:
                parts[classes[i]].append(i)
        if len(parts) >= 2:
            by_class.append({cls: np.array(rows) for cls, rows in parts.items()})
    usable = np.array([i for i in range(len(pieces)) if i not in barred])
    pool = {cls: usable[classes[usable] == cls] for cls in set(classes[usable])}
    own = {(cls, maker): rows[makers[rows] == maker] for cls, rows in pool.items() for maker in set(makers[rows])}
    log.info("compat: %d training groups, %d pieces in them", len(by_class), len({i for g in by_class for rows in g.values() for i in rows}))

    data = torch.tensor(x, dtype=torch.float32)
    width = x.shape[1]
    if hidden:
        net = torch.nn.Sequential(torch.nn.Dropout(dropout), torch.nn.Linear(width, hidden), torch.nn.GELU(),
                                  torch.nn.Dropout(dropout), torch.nn.Linear(hidden, dim))
        start = None
    else:
        net = torch.nn.Sequential(torch.nn.Dropout(dropout), torch.nn.Linear(width, dim, bias=False))
        # Start from the directions the input varies most along: already a fair answer.
        centred = x[usable] - x[usable].mean(axis=0)
        _, _, axes = np.linalg.svd(centred, full_matrices=False)
        start = torch.tensor(axes[:dim], dtype=torch.float32)
        with torch.no_grad():
            net[1].weight.copy_(start)
    optimiser = torch.optim.AdamW(net.parameters(), lr=rate, weight_decay=decay)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimiser, max_lr=rate, total_steps=steps, pct_start=0.1)

    def embed() -> np.ndarray:
        net.eval()
        with torch.no_grad():
            out = torch.nn.functional.normalize(net(data), dim=-1).numpy()
        net.train()
        return out

    def draw() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        anchors, positives = [], []
        while len(anchors) < batch:
            group = by_class[rng.integers(len(by_class))]
            first, second = rng.choice(sorted(group), size=2, replace=False)
            a, p = rng.choice(group[first]), rng.choice(group[second])
            if pictures[a] != pictures[p]:  # one photo used for two pieces teaches nothing
                anchors.append(a)
                positives.append(p)
        anchors, positives = np.array(anchors), np.array(positives)
        negatives = np.empty((batch, hard), dtype=np.int64)
        for row, p in enumerate(positives if hard else []):
            everyone, same_maker = pool[classes[p]], own[(classes[p], makers[p])]
            half = hard // 2 if len(same_maker) > 8 else 0
            negatives[row] = np.concatenate([rng.choice(same_maker, size=half), rng.choice(everyone, size=hard - half)])
        return anchors, positives, negatives

    record = {"steps": steps, "best_step": steps, "val": []}
    best, best_state = -1.0, None
    net.train()
    for step in range(1, steps + 1):
        anchors, positives, negatives = draw()
        za = torch.nn.functional.normalize(net(data[anchors]), dim=-1)
        zp = torch.nn.functional.normalize(net(data[positives]), dim=-1)
        to_batch = za @ zp.T
        # What is in fact a match must not be pushed away: the same group, or the same photo.
        wrong_batch = torch.tensor(_related(table, anchors[:, None].repeat(batch, 1), positives[None, :].repeat(batch, 0)))
        wrong_batch.fill_diagonal_(False)
        logits = to_batch.masked_fill(wrong_batch, -1e4)
        if hard:
            zn = torch.nn.functional.normalize(net(data[negatives.reshape(-1)]), dim=-1).reshape(batch, hard, dim)
            wrong_hard = torch.tensor(_related(table, anchors[:, None].repeat(hard, 1), negatives) | (pictures[negatives] == pictures[positives][:, None]))
            logits = torch.cat([logits, torch.einsum("bk,bhk->bh", za, zn).masked_fill(wrong_hard, -1e4)], dim=1)
        logits = logits / temperature
        loss = torch.nn.functional.cross_entropy(logits, torch.arange(batch))
        if stay and start is not None:
            loss = loss + stay * ((net[1].weight - start) ** 2).sum()
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()
        schedule.step()
        if val and (step % 100 == 0 or step == steps):
            score = rank_metrics(embed(), pieces, groups, val)["recall@10"]
            record["val"].append((step, round(loss.item(), 3), round(score, 4)))
            if score > best:
                best, record["best_step"] = score, step
                best_state = {key: value.clone() for key, value in net.state_dict().items()}
    if best_state is not None:
        net.load_state_dict(best_state)
    return embed(), record


def rank_metrics(vectors: np.ndarray | None, pieces: list[dict], groups: dict[str, list[int]], names: list[str],
                 own_maker: bool = False, score=None) -> dict:
    """How well a group's pieces of OTHER classes rank for an anchor piece of the group, among
    every piece of the library that is not of the anchor's class.

    recall@10: of the anchor's matches (ten at most), the share found in the first ten.
    mrr: 1 / the rank of the first match. Both averaged over a group's anchors, then over the
    groups, so a line of two hundred sofas counts as much as a line of six pieces.

    `own_maker` keeps only the anchor's own maker's pieces as candidates: there, knowing who took
    the photo is worth nothing. `score(anchor row) -> a score per piece` replaces the cosine of
    `vectors`, for baselines that are not vectors.
    """
    classes = np.array([p["cls"] for p in pieces])
    makers = np.array([str(p.get("maker") or "") for p in pieces])
    pictures = np.array([p["picture"] for p in pieces])
    recall, mrr, anchors, pairs, used = [], [], 0, 0, 0
    for name in names:
        rows = np.array(groups[name])
        group_recall, group_mrr = [], []
        for a in rows:
            matches = rows[(classes[rows] != classes[a]) & (pictures[rows] != pictures[a])]
            candidate = classes != classes[a]
            if own_maker:
                candidate &= makers == makers[a]
                matches = matches[makers[matches] == makers[a]]
            if not len(matches):
                continue
            scores = score(a) if score is not None else vectors @ vectors[a]
            order = np.flatnonzero(candidate)
            order = order[np.argsort(-scores[order], kind="stable")]
            hit = np.isin(order, matches)
            group_recall.append(hit[:10].sum() / min(10, len(matches)))
            group_mrr.append(1.0 / (np.flatnonzero(hit)[0] + 1))
            anchors += 1
            pairs += len(matches)
        if group_recall:
            recall.append(np.mean(group_recall))
            mrr.append(np.mean(group_mrr))
            used += 1
    return {"recall@10": float(np.mean(recall)) if recall else 0.0, "mrr": float(np.mean(mrr)) if mrr else 0.0,
            "groups": used, "anchors": anchors, "pairs": pairs}


def class_centred(rows: np.ndarray, pieces: list[dict]) -> np.ndarray:
    """The look vectors with each class's average taken out: what is left is how a piece differs
    from others of its kind, which is what two kinds can share."""
    out = rows.astype(np.float32).copy()
    classes = np.array([p["cls"] for p in pieces])
    for cls in set(classes):
        members = classes == cls
        out[members] -= out[members].mean(axis=0)
    return unit(out)


def colour_vectors(palettes: list[list[str]]) -> np.ndarray:
    """A vector per piece from its colours alone, for the colour-only baseline: how much of the
    palette falls near each of the tone swatches (the first colour counts most)."""
    from services.api.library.build.embed_colour import TONES, lab_of_hex

    swatches = np.array([lab_of_hex(colour) for name, colour in TONES.items() if name != "multicolour"])
    out = np.zeros((len(palettes), len(swatches)), dtype=np.float32)
    for row, colours in enumerate(palettes):
        for colour, weight in zip(colours, (0.6, 0.25, 0.15)):
            distance = np.linalg.norm(swatches - lab_of_hex(colour), axis=1)
            out[row] += weight * np.exp(-0.5 * (distance / 14.0) ** 2)
    return unit(out)
