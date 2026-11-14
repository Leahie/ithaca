"""Photo -> furniture in the user's inventory (PLAN.md section 4). Owner: B.

    read the image (vision LLM) -> for each piece: cut it out (segmentation) -> build a 3D model ->
    size and orient it -> save the item

One bad piece doesn't sink the others: each piece either becomes a valid item or is reported with
the reason (B6). `progress(pct, note)` is called along the way.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from packages.schema.inventory import InventoryItem, ItemAttributes, ItemDims
from services.api.furniture.store import FurnitureStore
from services.api.integrations.config import env
from services.api.objects import files
from services.api.objects.classes import FURNITURE_CLASSES
from services.api.objects.normalize import NormalizeError, normalize_glb
from services.api.objects.providers.base import Describer, ImageTo3D, Segmenter


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


def default_providers() -> Providers:
    from services.api.objects.providers import stub

    has_llm = env("ANTHROPIC_API_KEY") if env("LLM_PROVIDER", "anthropic").lower() == "anthropic" else env("OPENROUTER_API_KEY")
    if has_llm:
        from services.api.objects.providers.vision import LlmDescriber

        describer: Describer = LlmDescriber()
    else:
        describer = stub.FilenameDescriber()
    if provider_name() == "fal":
        from services.api.objects.providers.fal import FalImageTo3D, FalSegmenter

        return Providers(describer, FalSegmenter(), FalImageTo3D())
    if provider_name() == "catalog":
        from services.api.objects.providers.catalog import CatalogTo3D

        return Providers(describer, stub.WholeImageSegmenter(), CatalogTo3D())
    return Providers(describer, stub.WholeImageSegmenter(), stub.BoxTo3D())


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
    result = IngestResult(kind=reading.kind)
    pieces = reading.pieces[:4]
    for n, piece in enumerate(pieces):
        base = 10 + 85 * n / len(pieces)
        step = 85 / len(pieces)
        item_class = piece.item_class if piece.item_class in FURNITURE_CLASSES else "other"
        try:
            progress(base, f"Cutting out the {piece.label}")
            segment = providers.segmenter.segment(image, mime_type, piece.segment_prompt)
            if segment is None:
                raise NormalizeError("it could not be found in the image")
            progress(base + step * 0.25, f"Building a 3D model of the {piece.label}")
            raw_glb = providers.to_3d.generate(segment.cutout_png, piece)
            progress(base + step * 0.85, f"Sizing the {piece.label}")
            glb, (w, d, h) = normalize_glb(raw_glb, item_class, (piece.width_m, piece.depth_m, piece.height_m))
        except Exception as e:  # one piece failing must not lose the others
            result.skipped.append(f"{piece.label}: {e}")
            continue
        item_id = uuid.uuid4()
        item = InventoryItem(
            item_id=item_id,
            user_id=user_id,
            label=piece.label,
            item_class=item_class,
            attributes=ItemAttributes(style=piece.style, color=piece.color, material=piece.material),
            dims_m=ItemDims(w=w, d=d, h=h),
            glb_url=files.save(f"items/{item_id}.glb", glb),
            source_image_url=source_url,
            mask_url=files.save(f"items/{item_id}.mask.png", segment.mask_png),
            price=piece.price,
            product_url=None,
        )
        store.put_item(item)
        result.items.append(item)
    progress(100, "Done")
    return result


def import_glb(
    glb: bytes,
    label: str,
    item_class: str,
    stated: tuple[float | None, float | None, float | None],
    user_id: str,
    store: FurnitureStore,
    source_url: str = "",
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
        product_url=None,
    )
    store.put_item(item)
    return item
