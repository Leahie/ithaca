"""A library piece's 3D model, and the piece as furniture someone owns.

Each collection keeps its models its own way (objects/providers/catalog.py for Amazon Berkeley
Objects, library/polyhaven.py, library/objaverse.py). `fetch` hides that: the model as one GLB,
its front along +Z. `add` puts the piece into a person's furniture at its real size, through the
same sizing rules as every other model (objects/normalize.py).
"""

from __future__ import annotations

import io
import logging
import math
from concurrent.futures import ThreadPoolExecutor

from packages.schema.inventory import InventoryItem, ItemAttributes
from services.api.furniture.store import FurnitureStore
from services.api.library.index import Piece, library
from services.api.objects import pipeline
from services.api.objects.normalize import NormalizeError

log = logging.getLogger(__name__)

FETCH_WORKERS = 6  # models downloaded side by side when a whole room is furnished


def fetch(piece: Piece) -> bytes:
    """The piece's model as a GLB, turned so its front faces +Z. Downloaded on first use, then cached."""
    if piece.src == "abo":
        from services.api.objects.providers import catalog

        glb = catalog.fetch(piece.model)
    elif piece.src == "polyhaven":
        from services.api.library import polyhaven

        glb = polyhaven.fetch(piece.model)
    elif piece.src == "objaverse":
        from services.api.library import objaverse

        glb = objaverse.fetch(piece.model)
    else:
        raise KeyError(f"no such collection: {piece.src}")
    if piece.turn:
        import trimesh

        scene = trimesh.load(io.BytesIO(glb), file_type="glb", force="scene")
        scene.apply_transform(trimesh.transformations.rotation_matrix(math.radians(piece.turn), [0, 1, 0]))
        glb = scene.export(file_type="glb")
    return glb


def add(piece: Piece, user_id: str, store: FurnitureStore, label: str | None = None) -> InventoryItem:
    """Put a library piece into a person's furniture. Raises NormalizeError when the model cannot
    be brought to the piece's size, and whatever the download raises when it cannot be fetched."""
    glb = fetch(piece)
    stated: tuple[float | None, float | None, float | None] = (None, None, None) if piece.approx else (piece.w, piece.d, piece.h)
    try:
        item = pipeline.import_glb(glb, label or piece.name, piece.cls, stated, user_id, store, source_url=piece.img, product_url=piece.url)
    except NormalizeError:
        if piece.approx:
            raise
        # The index's size and the model's own shape disagree: keep the model's shape and trust
        # the index for its largest side only, which is the one a listing gets right.
        longest = max(range(3), key=lambda k: stated[k] or 0.0)
        one = tuple(v if k == longest else None for k, v in enumerate(stated))
        log.warning("library: %s does not have the shape its index line says; sized by its longest side", piece.id)
        item = pipeline.import_glb(glb, label or piece.name, piece.cls, one, user_id, store, source_url=piece.img, product_url=piece.url)
    look = ItemAttributes(
        style=piece.styles[0] if piece.styles else None, color=piece.color or piece.tone, material=piece.materials[0] if piece.materials else None,
    )
    return store.put_item(item.model_copy(update={"attributes": look}))


def add_many(pieces: list[Piece], user_id: str, store: FurnitureStore, progress=lambda done, total, piece: None) -> tuple[list[InventoryItem | None], list[str]]:
    """Add several pieces side by side (each is a download, then sizing its model). Returns the
    new items in the order asked (None where one failed) and "name: reason" for each failure.
    One piece that cannot be fetched does not stop the others."""
    from concurrent.futures import as_completed

    items: list[InventoryItem | None] = [None] * len(pieces)
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS, thread_name_prefix="library-add") as pool:
        jobs = {pool.submit(add, piece, user_id, store): n for n, piece in enumerate(pieces)}
        for done, job in enumerate(as_completed(jobs), start=1):
            n = jobs[job]
            try:
                items[n] = job.result()
            except Exception as e:
                log.warning("library: could not add %s: %s", pieces[n].id, e)
                failed.append(f"{pieces[n].name}: {'its model could not be sized' if isinstance(e, NormalizeError) else 'its model could not be loaded'}")
            progress(done, len(pieces), pieces[n])
    return items, failed


def piece_of(item: InventoryItem) -> Piece | None:
    """The library piece an owned item was made from, if it was: known by its picture."""
    return library().by_img.get(item.source_image_url) if item.source_image_url else None
