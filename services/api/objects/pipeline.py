"""Photo -> furniture in the user's inventory (PLAN.md section 4). Owner: B.

    read the image (vision LLM) -> for each piece: cut it out (segmentation) -> build a 3D model ->
    size and orient it -> save the item

A video of a room goes the same way, from the still frame that shows each piece best.

One bad piece doesn't sink the others: each piece either becomes a valid item or is reported with
the reason (B6). `progress(pct, note)` is called along the way.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from packages.schema.inventory import InventoryItem, ItemAttributes, ItemDims
from services.api.furniture.store import FurnitureStore
from services.api.integrations.config import IntegrationNotConfigured, env
from services.api.objects import files
from services.api.objects.classes import FURNITURE_CLASSES, TYPICAL_SIZE_M
from services.api.objects.normalize import NormalizeError, normalize_glb
from services.api.objects.providers.base import Describer, FramePiece, ImageTo3D, Piece, RoomDescriber, Segment, Segmenter


@dataclass
class Providers:
    describer: Describer
    segmenter: Segmenter
    to_3d: ImageTo3D


@dataclass
class IngestResult:
    kind: str
    items: list[InventoryItem] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # "label: reason"

    def summary(self) -> str:
        parts = [f"added {i.label} ({i.dims_m.w:.2f} × {i.dims_m.d:.2f} × {i.dims_m.h:.2f} m, item_id {i.item_id})" for i in self.items]
        parts += [f"could not add {s}" for s in self.skipped]
        return "; ".join(parts) or "no furniture was found in the image"


def provider_name() -> str:
    """fal = real segmentation and 3D (needs FAL_KEY); catalog = free ready-made models sized to
    the photo's dimensions; stub = plain boxes, no network. Unset: fal with a key, else catalog."""
    name = env("OBJECT_PROVIDER").lower()
    return name if name in ("fal", "catalog", "stub") else ("fal" if env("FAL_KEY") else "catalog")


def segmenter_name() -> str:
    """fal = SAM 3 on fal.ai; local = on this machine (providers/local.py); whole = no cutting,
    the piece is the whole image. Unset: fal when the provider is fal, else whole."""
    name = env("OBJECT_SEGMENTER").lower()
    return name if name in ("fal", "local", "whole") else ("fal" if provider_name() == "fal" else "whole")


def default_segmenter() -> Segmenter:
    if segmenter_name() == "fal":
        from services.api.objects.providers.fal import FalSegmenter

        return FalSegmenter()
    if segmenter_name() == "local":
        from services.api.objects.providers.local import LocalSegmenter

        return LocalSegmenter()
    from services.api.objects.providers.stub import WholeImageSegmenter

    return WholeImageSegmenter()


def default_providers() -> Providers:
    from services.api.objects.providers import stub

    has_llm = env("ANTHROPIC_API_KEY") if env("LLM_PROVIDER", "anthropic").lower() == "anthropic" else env("OPENROUTER_API_KEY")
    if has_llm:
        from services.api.objects.providers.vision import LlmDescriber

        describer: Describer = LlmDescriber()
    else:
        describer = stub.FilenameDescriber()
    if provider_name() == "fal":
        from services.api.objects.providers.fal import FalImageTo3D

        return Providers(describer, default_segmenter(), FalImageTo3D())
    if provider_name() == "catalog":
        from services.api.objects.providers.catalog import CatalogTo3D

        return Providers(describer, default_segmenter(), CatalogTo3D())
    return Providers(describer, stub.WholeImageSegmenter(), stub.BoxTo3D())


@dataclass
class CutPiece:
    piece: FramePiece
    segment: Segment


def cut_from_frames(
    frames: list[bytes],
    describer: RoomDescriber,
    segmenter: Segmenter,
    progress: Callable[[float, str], None] = lambda pct, note: None,
) -> tuple[list[CutPiece], list[str]]:
    """Find the furniture in JPEG frames of one room (a single photo is one frame) and cut each
    piece out of the frame that shows it best. Returns (cut pieces, "label: reason" for the rest)."""
    progress(5, "Looking for furniture")
    pieces = describer.read_frames(frames).pieces[:8]
    cuts: list[CutPiece] = []
    skipped: list[str] = []
    for n, piece in enumerate(pieces):
        progress(15 + 85 * n / len(pieces), f"Cutting out the {piece.label}")
        try:
            segment = segmenter.segment(frames[piece.frame], "image/jpeg", piece.segment_prompt, piece.box)
        except Exception as e:  # one piece failing must not lose the others
            skipped.append(f"{piece.label}: {e}")
            continue
        if segment is None:
            skipped.append(f"{piece.label}: it could not be found in frame {piece.frame}")
        else:
            cuts.append(CutPiece(piece, segment))
    progress(100, "Done")
    return cuts, skipped


@dataclass
class _Job:
    piece: Piece
    image: bytes  # the photo or video frame the piece is cut from
    mime_type: str
    source_url: str


def _size(piece: Piece, item_class: str, estimate: tuple[float, float, float] | None) -> tuple[float | None, float | None, float | None]:
    """(w, d, h) to size the model to. Dimensions printed in the image come first. Without any,
    the 3D model's own estimate is used when it is believable for the class: within 2x of the
    typical size (a bed estimated 0.9 m long is not). Otherwise the sizing step falls back to
    the typical size. "other" has no meaningful typical size, so its estimate always stands."""
    printed = (piece.width_m, piece.depth_m, piece.height_m)
    if any(printed) or estimate is None:
        return printed
    typical = max(TYPICAL_SIZE_M[item_class])
    return estimate if item_class == "other" or typical / 2 <= max(estimate) <= typical * 2 else printed


def _build(job: _Job, item_class: str, providers: Providers) -> tuple[Segment, bytes, tuple[float, float, float]]:
    """One piece: cut it out, build its 3D model, size it. Raises with the reason if a step fails."""
    segment = providers.segmenter.segment(job.image, job.mime_type, job.piece.segment_prompt, job.piece.box)
    if segment is None:
        raise NormalizeError("it could not be found in the image")
    model = providers.to_3d.generate(job.image, segment, job.piece)
    glb, dims = normalize_glb(model.glb, item_class, _size(job.piece, item_class, model.size_m))
    return segment, glb, dims


def _ingest(kind: str, jobs: list[_Job], user_id: str, store: FurnitureStore, providers: Providers, progress: Callable[[float, str], None]) -> IngestResult:
    """Build every piece and save the ones that come out valid. The pieces are built side by side:
    each is mostly waiting on a model service, so a room takes about as long as one piece."""
    result = IngestResult(kind=kind)
    classes = [job.piece.item_class if job.piece.item_class in FURNITURE_CLASSES else "other" for job in jobs]
    progress(10, f"Cutting out and building {len(jobs)} piece(s)" if jobs else "No furniture found")
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="piece") as pool:
        builds = [pool.submit(_build, job, item_class, providers) for job, item_class in zip(jobs, classes)]
        for n, (job, item_class, build) in enumerate(zip(jobs, classes, builds)):
            piece = job.piece
            try:
                segment, glb, (w, d, h) = build.result()
            except Exception as e:  # one piece failing must not lose the others
                result.skipped.append(f"{piece.label}: {e}")
                continue
            finally:
                progress(10 + 85 * (n + 1) / len(jobs), f"Finished the {piece.label}")
            item_id = uuid.uuid4()
            item = InventoryItem(
                item_id=item_id,
                user_id=user_id,
                label=piece.label,
                item_class=item_class,
                attributes=ItemAttributes(style=piece.style, color=piece.color, material=piece.material),
                dims_m=ItemDims(w=w, d=d, h=h),
                glb_url=files.save(f"items/{item_id}.glb", glb),
                source_image_url=job.source_url,
                mask_url=files.save(f"items/{item_id}.mask.png", segment.mask_png),
                price=piece.price,
                product_url=None,
            )
            store.put_item(item)
            result.items.append(item)
    progress(100, "Done")
    return result


def ingest_image(
    image: bytes,
    mime_type: str,
    filename: str,
    source_url: str,
    user_id: str,
    store: FurnitureStore,
    providers: Providers,
    progress: Callable[[float, str], None] = lambda pct, note: None,
) -> IngestResult:
    progress(5, "Reading the image")
    reading = providers.describer.read(image, mime_type, filename)
    jobs = [_Job(piece, image, mime_type, source_url) for piece in reading.pieces[:4]]
    return _ingest(reading.kind, jobs, user_id, store, providers, progress)


def save_frames(frames: list[bytes], user_id: str) -> list[str]:
    """Store video stills so each item can point at the frame it came from. Returns their URLs."""
    folder = f"frames/{user_id}/{uuid.uuid4().hex}"
    return [files.save(f"{folder}/{n:02d}.jpg", frame) for n, frame in enumerate(frames)]


def ingest_frames(
    frames: list[bytes],
    source_urls: list[str],
    user_id: str,
    store: FurnitureStore,
    providers: Providers,
    progress: Callable[[float, str], None] = lambda pct, note: None,
) -> IngestResult:
    """A room video -> inventory items. `frames` are JPEG stills of one room (video.sharp_frames)
    and `source_urls` their stored URLs; each piece is built from the frame that shows it best."""
    if not hasattr(providers.describer, "read_frames"):
        raise IntegrationNotConfigured("reading a room video", "ANTHROPIC_API_KEY or OPENROUTER_API_KEY")
    progress(5, "Looking for furniture")
    pieces = providers.describer.read_frames(frames).pieces[:8]
    jobs = [_Job(piece, frames[piece.frame], "image/jpeg", source_urls[piece.frame]) for piece in pieces]
    return _ingest("room_video", jobs, user_id, store, providers, progress)


def import_glb(
    glb: bytes,
    label: str,
    item_class: str,
    stated: tuple[float | None, float | None, float | None],
    user_id: str,
    store: FurnitureStore,
    source_url: str = "",
    product_url: str | None = None,
) -> InventoryItem:
    """Add a 3D file the user already has (for example one generated on a TRELLIS demo site).
    It goes through the same sizing rules as a generated model. Raises NormalizeError if it fails."""
    item_class = item_class if item_class in FURNITURE_CLASSES else "other"
    sized, (w, d, h) = normalize_glb(glb, item_class, stated)
    item_id = uuid.uuid4()
    item = InventoryItem(
        item_id=item_id,
        user_id=user_id,
        label=label,
        item_class=item_class,
        attributes=ItemAttributes(),
        dims_m=ItemDims(w=w, d=d, h=h),
        glb_url=files.save(f"items/{item_id}.glb", sized),
        source_image_url=source_url,
        mask_url=None,
        price=None,
        product_url=product_url,
    )
    store.put_item(item)
    return item


def add_from_catalog(
    catalog_id: str,
    user_id: str,
    store: FurnitureStore,
    label: str | None = None,
    size: tuple[float | None, float | None, float | None] = (None, None, None),
) -> InventoryItem:
    """Add a ready-made catalog piece to the user's inventory, at its own size unless all three of
    `size` are given. Raises KeyError for an unknown id, NormalizeError if it can't be sized."""
    from services.api.objects.providers import catalog

    _id, item_class, name, w, d, h = catalog.BY_ID[catalog_id]
    stated = size if all(size) else (w, d, h)
    return import_glb(
        catalog.fetch(catalog_id), label or name, item_class, stated, user_id, store,
        source_url=catalog.thumbnail_url(catalog_id), product_url=f"https://polyhaven.com/a/{catalog_id}",
    )
