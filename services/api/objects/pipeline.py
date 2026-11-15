"""Photo -> furniture in the user's inventory (PLAN.md section 4). Owner: B.

    read the image (vision LLM) -> for each piece: cut it out (segmentation) -> build a 3D model ->
    size and orient it -> save the item

A video of a room goes the same way, from the still frame that shows each piece best.

A photo of the person's own room (ingest_room_photo) goes further: everything of theirs in it is
taken, the small things too, and what the 3D model says about where each stood in the photo is
kept: the scale all of them share, what stood on what and where, how high things hung. The house
is then furnished from that (furniture/own_room.py).

One bad piece doesn't sink the others: each piece either becomes a valid item or is reported with
the reason (B6). `progress(pct, note)` is called along the way.
"""

from __future__ import annotations

import math
import statistics
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as TookTooLong
from dataclasses import dataclass, field

from packages.schema.inventory import InventoryItem, ItemAttributes, ItemDims
from services.api.furniture.store import FurnitureStore
from services.api.integrations.config import IntegrationNotConfigured, env
from services.api.objects import files
from services.api.objects.classes import FURNITURE_CLASSES, MIN_TOP_M, NO_TOP, NO_TYPICAL_SIZE, SIZE_ANCHORS, TYPICAL_SIZE_M
from services.api.objects.normalize import NormalizeError, hang_up, normalize_glb
from services.api.objects.providers.base import (
    Describer, FramePiece, ImageTo3D, Model3D, PhotoPose, Piece, RoomDescriber, RoomThing, Segment, Segmenter, contains,
)


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
        parts = [f"added {i.label} ({i.dims_m.w:.2f} × {i.dims_m.d:.2f} × {i.dims_m.h:.2f} m, piece #{i.tag})" for i in self.items]
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
    at: tuple[float, float] | None = None  # the spot the user tapped on it


def _size(piece: Piece, item_class: str, estimate: tuple[float, float, float] | None) -> tuple[float | None, float | None, float | None]:
    """(w, d, h) to size the model to. Dimensions printed in the image come first. Without any,
    the 3D model's own estimate is used when it is believable for the class: within 2x of the
    typical size (a bed estimated 0.9 m long is not). Otherwise the sizing step falls back to
    the typical size. "other" has no meaningful typical size, so its estimate always stands."""
    printed = (piece.width_m, piece.depth_m, piece.height_m)
    if any(printed) or estimate is None:
        return printed
    typical = max(TYPICAL_SIZE_M[item_class])
    return estimate if item_class in NO_TYPICAL_SIZE or typical / 2 <= max(estimate) <= typical * 2 else printed


def _build(job: _Job, item_class: str, providers: Providers) -> tuple[Segment, bytes, tuple[float, float, float]]:
    """One piece: cut it out, build its 3D model, size it. Raises with the reason if a step fails."""
    segment = providers.segmenter.segment(job.image, job.mime_type, job.piece.segment_prompt, job.piece.box, at=job.at)
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
            result.items.append(store.put_item(item))  # as stored: with its number
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
    at: tuple[float, float] | None = None,
) -> IngestResult:
    """An image -> inventory items: up to 4 pieces, or with `at` (the spot the user tapped, as
    fractions of the image) only the thing there."""
    if at is not None:
        progress(5, "Looking at what you tapped")
        piece = _tapped_piece(providers.describer, image, mime_type, filename, at)
        return _ingest("tapped_photo", [_Job(piece, image, mime_type, source_url, at)], user_id, store, providers, progress)
    progress(5, "Reading the image")
    reading = providers.describer.read(image, mime_type, filename)
    jobs = [_Job(piece, image, mime_type, source_url) for piece in reading.pieces[:4]]
    return _ingest(reading.kind, jobs, user_id, store, providers, progress)


def _tapped_piece(describer: Describer, image: bytes, mime_type: str, filename: str, at: tuple[float, float]) -> Piece:
    """The piece at the tapped spot. A describer that can't read a single spot lists the pieces in
    the image, and the one whose box holds the spot (else the most prominent) is taken."""
    if hasattr(describer, "read_at"):
        return describer.read_at(image, mime_type, at)
    pieces = describer.read(image, mime_type, filename).pieces
    hit = [p for p in pieces if p.box and len(p.box) == 4 and contains(p.box, at)]
    return (hit or pieces or [Piece(label="Object", item_class="other", segment_prompt="object")])[0]


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
    return store.put_item(item)  # as stored: with its number


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
        source_url=catalog.thumbnail_url(catalog_id), product_url=None,
    )


# --- a photo of the person's own room ---------------------------------------------------------------

ROOM_WORKERS = 14  # every thing of a photo cut out and built at once: each is mostly waiting on a model service
# The model service works through a photo's things a few at a time, about half a minute each, and
# now and then one request hangs for minutes. Past this, the room is set up with what is ready.
BUILD_LIMIT_S = 130.0


@dataclass
class SceneThing:
    """One thing from a photo of someone's room: the piece it became, and how it stood there."""

    item: InventoryItem
    on_wall: bool = False
    rests_on: str | None = None  # item_id of the piece it stood on
    beside: str | None = None  # item_id of the piece on the floor it went with
    # Where it was, measured from the middle of the piece it stood on (rests_on) or hung nearest
    # (anchor): metres toward that piece's front, and toward its right. None: not known.
    offset_m: tuple[float, float] | None = None
    turn_deg: float | None = None  # its front, turned this far from that piece's front toward that piece's right
    anchor: str | None = None  # on a wall: item_id of the piece on the floor it hung nearest
    hang_m: float | None = None  # on a wall: how high its underside hung above the floor


@dataclass
class RoomScene:
    """What a photo of someone's room became."""

    room: str
    things: list[SceneThing] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # "label: reason"


def _to_camera(pose: PhotoPose):
    """The 3x3 matrix that takes the object's own axes into the photo's camera frame. In the
    object's frame +z is up, -y its front and +x its right (worked out from real answers: every
    object of one photo then agrees on which way is up, and a chair at a desk faces the desk)."""
    import numpy as np

    w, x, y, z = np.array(pose.rotation) / np.linalg.norm(pose.rotation)
    rotation = np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
    return rotation.T


class _Geometry:
    """The things of one photo on a common floor: where each is on the floor plane, how high its
    middle is, and which way its front and its right point. In the photo's own units."""

    def __init__(self, poses: dict[int, PhotoPose]) -> None:
        import numpy as np

        axes = {n: _to_camera(p) for n, p in poses.items()}
        ups = {n: m @ np.array([0.0, 0.0, 1.0]) for n, m in axes.items()}
        # Up is what most of the objects call up. A flat thing (a rug, a picture) is often rebuilt
        # lying another way: it is left out, and so is its front, which then means nothing.
        up = np.mean(list(ups.values()), axis=0)
        for _ in range(2):
            up = up / (np.linalg.norm(up) or 1.0)
            self.upright = {n for n, v in ups.items() if float(v @ up) > 0.9}
            if len(self.upright) < 2:
                break
            up = np.mean([ups[n] for n in self.upright], axis=0)
        up = up / (np.linalg.norm(up) or 1.0)
        self.trusted = len(self.upright) >= 2 and len(self.upright) >= len(poses) / 2
        a = np.array([1.0, 0.0, 0.0]) - up[0] * up
        a = a / np.linalg.norm(a)
        b = np.cross(up, a)

        def flat(v) -> tuple[float, float] | None:
            x, y = float(v @ a), float(v @ b)
            length = math.hypot(x, y)
            return (x / length, y / length) if length > 0.3 else None  # tipped over: its front says nothing about the floor

        self.at = {n: (float(np.array(p.translation) @ a), float(np.array(p.translation) @ b)) for n, p in poses.items()}
        self.height = {n: float(np.array(p.translation) @ up) for n, p in poses.items()}
        self.front = {n: flat(m @ np.array([0.0, -1.0, 0.0])) if n in self.upright else None for n, m in axes.items()}
        self.right = {n: flat(m @ np.array([1.0, 0.0, 0.0])) if n in self.upright else None for n, m in axes.items()}

    def between(self, a: int, b: int) -> tuple[float, float]:
        return self.at[b][0] - self.at[a][0], self.at[b][1] - self.at[a][1]

    def from_piece(self, piece: int, thing: int) -> tuple[float, float] | None:
        """Where `thing` is from the middle of `piece`: toward the piece's front, toward its right."""
        front, right = self.front[piece], self.right[piece]
        if front is None or right is None:
            return None
        dx, dy = self.between(piece, thing)
        return dx * front[0] + dy * front[1], dx * right[0] + dy * right[1]

    def turn(self, piece: int, thing: int) -> float | None:
        """How far `thing`'s front is turned from `piece`'s front toward the piece's right, degrees."""
        f, r, mine = self.front[piece], self.right[piece], self.front[thing]
        if f is None or r is None or mine is None:
            return None
        return math.degrees(math.atan2(mine[0] * r[0] + mine[1] * r[1], mine[0] * f[0] + mine[1] * f[1]))


def _photo_scale(classes: dict[int, str], models: dict[int, Model3D]) -> float:
    """What to multiply the photo's sizes by to get metres. The 3D model's own sizes come out too
    small or too large by one factor for the whole photo; furniture of known size says by how much."""
    ratios = [
        max(TYPICAL_SIZE_M[classes[n]]) / max(m.size_m)
        for n, m in models.items()
        if m.size_m and classes[n] in SIZE_ANCHORS
    ]
    return min(max(statistics.median(ratios), 0.4), 4.0) if ratios else 1.0


LONGEST_M = {"bed": 2.15, "crib": 1.45}  # no bed is longer than this, whatever the photo says


def _room_size(item_class: str, size_m: tuple[float, float, float] | None, scale: float) -> tuple[float | None, float | None, float | None]:
    """(w, d, h) to size a thing from a room photo to: its size in the photo, at the photo's
    scale. Furniture whose size then comes out unbelievable for its kind (a bed cut off by the
    edge of the photo) falls back to the typical size."""
    if size_m is None:
        return (None, None, None)
    size = [v * scale for v in size_m]
    fit = min(1.0, 4.0 / max(size)) * max(1.0, 0.03 / max(size))  # nothing larger than 4 m or smaller than 3 cm
    size = [v * fit for v in size]
    if item_class in NO_TYPICAL_SIZE or item_class == "plant":
        return (size[0], size[1], size[2])
    typical = TYPICAL_SIZE_M[item_class]
    believable = 0.55 <= max(size) / max(typical) <= 1.35 and (item_class == "rug" or 0.5 <= size[2] / typical[2] <= 1.8)
    if not believable:
        return (None, None, None)
    # A wide-angle photo stretches what is near the camera. Beds come in standard lengths.
    shrink = min(1.0, LONGEST_M.get(item_class, math.inf) / max(size))
    return (size[0] * shrink, size[1] * shrink, size[2] * shrink)


def ingest_room_photo(
    image: bytes,
    mime_type: str,
    user_id: str,
    store: FurnitureStore,
    providers: Providers,
    progress: Callable[[float, str], None] = lambda pct, note: None,
) -> RoomScene:
    """One photo of the person's own room -> every thing of theirs in it as a piece in their
    furniture (up to 14: furniture, lamps, plants, rugs, pictures, things standing on the
    furniture), with how it stood in the photo. Each thing is cut out (SAM 3) and rebuilt in 3D
    (SAM 3D Objects), side by side. Furnishing a room with the result is furniture/own_room.py."""
    from services.api.furniture import house

    if not hasattr(providers.describer, "read_room"):
        raise IntegrationNotConfigured("reading a photo of a room", "ANTHROPIC_API_KEY or OPENROUTER_API_KEY")
    progress(4, "Looking at your room")
    reading = providers.describer.read_room(image, mime_type)
    things: list[RoomThing] = [t for t in reading.things if len(t.box) == 4]
    scene = RoomScene(room=reading.room)
    if not things:
        return scene
    classes = {n: (t.item_class if t.item_class in FURNITURE_CLASSES else "other") for n, t in enumerate(things)}
    done, waiting = 0, True

    def build(n: int) -> tuple[Segment, Model3D]:
        nonlocal done
        thing = things[n]
        segment = providers.segmenter.segment(image, mime_type, thing.segment_prompt, thing.box)
        if segment is None and hasattr(providers.segmenter, "segment_box"):
            # The words found nothing (a woven stool, a small wall lamp): take whatever fills its box.
            # Only then: in a full room the box is rougher than the words, and what fills it is often
            # the wrong thing (a pillow for the shelf behind the bed).
            segment = providers.segmenter.segment_box(image, mime_type, thing.box)
        if segment is None:
            raise NormalizeError("it could not be found in the photo")
        model = providers.to_3d.generate(image, segment, thing.piece())
        done += 1
        if waiting:  # a thing that comes in after the others were given up on reports nothing
            progress(15 + 65 * done / len(things), f"Rebuilt {done} of {len(things)} things in 3D")
        return segment, model

    progress(12, f"Found {len(things)} things. Cutting them out and rebuilding them in 3D")
    built: dict[int, tuple[Segment, Model3D]] = {}
    pool = ThreadPoolExecutor(max_workers=ROOM_WORKERS, thread_name_prefix="room-thing")
    started = time.monotonic()
    jobs = {n: pool.submit(build, n) for n in range(len(things))}
    for n, job in jobs.items():
        try:
            built[n] = job.result(timeout=max(1.0, BUILD_LIMIT_S - (time.monotonic() - started)))
        except TookTooLong:
            scene.skipped.append(f"{things[n].label}: it took too long to rebuild")
        except Exception as e:  # one thing failing must not lose the others
            scene.skipped.append(f"{things[n].label}: {e}")
    waiting = False
    pool.shutdown(wait=False, cancel_futures=True)  # a request still hanging is left to finish by itself

    # Every thing is sized at the one scale the photo has, so a lamp is the right size next to its desk.
    models = {n: model for n, (_, model) in built.items()}
    scale = _photo_scale(classes, models)
    rough = {n: _room_size(classes[n], models[n].size_m, scale) for n in built}  # (None, None, None): the typical size will do

    def size_of(n: int) -> tuple[float, float, float]:
        return rough[n] if rough[n][0] else TYPICAL_SIZE_M[classes[n]]  # type: ignore[return-value]

    # How they stood. From the 3D model's own placing of each thing where it gave one, else from
    # what the reading said.
    posed = {n: model.pose for n, model in models.items() if model.pose is not None}
    geometry = _Geometry(posed) if len(posed) >= 2 else None
    if geometry is not None and not geometry.trusted:  # the objects disagree about which way is up
        geometry = None
    seen = lambda n: geometry is not None and n in posed  # noqa: E731

    def metres(a: int, b: int) -> float:
        return math.hypot(*geometry.between(a, b)) * scale

    # What stood on what. A table lamp or a small decoration stands on something whether or not
    # the reading said so; anything else only if it said so.
    wants_top = {n for n in built if not things[n].on_wall and (things[n].rests_on is not None or classes[n] in ("table_lamp", "decor"))}
    on_floor = [n for n in built if n not in wants_top and not things[n].on_wall]
    has_top = lambda s_: classes[s_] not in NO_TOP and size_of(s_)[2] >= MIN_TOP_M  # noqa: E731
    rests_on: dict[int, int] = {}
    for n in sorted(wants_top):
        can = [s_ for s_ in on_floor if has_top(s_)]
        under = None
        if seen(n):
            # The piece whose top it is over, and not below: the nearest such.
            reach = lambda s_: math.hypot(size_of(s_)[0], size_of(s_)[1]) / 2 + 0.1  # noqa: E731
            over = [s_ for s_ in can if seen(s_) and metres(s_, n) <= reach(s_) and (geometry.height[n] - geometry.height[s_]) * scale > -0.05]
            under = min(over, key=lambda s_: metres(s_, n), default=None)
        said = things[n].rests_on
        if under is None and said is not None and said in can:
            under = said
        if under is None:
            on_floor.append(n)  # nothing to stand on came through: it stands on the floor
        else:
            rests_on[n] = under

    # A photo's sizes for small and for odd things run large. What stood on a piece is no wider
    # than that piece; a plant or a decoration is not the size of a wardrobe.
    def no_wider_than(n: int, limit: float) -> None:
        if rough[n][0] and max(rough[n][0], rough[n][1]) > limit:
            shrink = limit / max(rough[n][0], rough[n][1])
            rough[n] = (rough[n][0] * shrink, rough[n][1] * shrink, rough[n][2] * shrink)

    for n in built:
        if n in rests_on:
            no_wider_than(n, 0.9 * max(size_of(rests_on[n])[:2]))
        elif classes[n] in NO_TYPICAL_SIZE or classes[n] == "plant":
            no_wider_than(n, 1.2)
    progress(82, "Sizing your things")

    hangs = {n for n in built if things[n].on_wall and n not in rests_on}
    lying: set[int] = set()  # hung things whose model came out lying on its back: tipped up to hang

    def sized(n: int):
        try:
            glb, stated = models[n].glb, rough[n]
            if n in hangs:
                glb, tipped = hang_up(glb)
                if tipped:  # what was its depth is now its height
                    lying.add(n)
                    stated = (stated[0], stated[2], stated[1])
            return normalize_glb(glb, classes[n], stated)
        except NormalizeError as e:
            return e

    # Reading and writing each model takes a second or two: side by side, like the building.
    with ThreadPoolExecutor(max_workers=ROOM_WORKERS, thread_name_prefix="room-size") as sizing:
        fitted = dict(zip(built, sizing.map(sized, built)))
    items: dict[int, InventoryItem] = {}
    for n, (segment, _model) in built.items():
        thing = things[n]
        if isinstance(fitted[n], NormalizeError):
            scene.skipped.append(f"{thing.label}: {fitted[n]}")
            continue
        glb, (w, d, h) = fitted[n]
        item_id = uuid.uuid4()
        items[n] = store.put_item(InventoryItem(
            item_id=item_id,
            user_id=user_id,
            label=thing.label,
            item_class=classes[n],
            attributes=ItemAttributes(),
            dims_m=ItemDims(w=w, d=d, h=h),
            glb_url=files.save(f"items/{item_id}.glb", glb),
            # The picture of a thing from a room photo is its cut-out, not the whole room.
            source_image_url=files.save(f"items/{item_id}.cut.png", segment.cutout_png),
            mask_url=None,
            price=None,
            product_url=None,
        ))
    on_floor = [n for n in on_floor if n in items]
    rests_on = {n: under for n, under in rests_on.items() if n in items and under in items}

    floor_height = None
    if geometry is not None:
        bases = [geometry.height[n] - built[n][1].size_m[2] / 2 for n in on_floor if seen(n) and built[n][1].size_m]
        floor_height = statistics.median(bases) if bases else None

    for n, item in items.items():
        thing = things[n]
        entry = SceneThing(item=item, on_wall=thing.on_wall and n not in rests_on)
        if n in rests_on:
            under = rests_on[n]
            entry.rests_on = str(items[under].item_id)
            if seen(n) and seen(under):
                offset = geometry.from_piece(under, n)
                entry.offset_m = (offset[0] * scale, offset[1] * scale) if offset else None
                entry.turn_deg = geometry.turn(under, n)
        elif entry.on_wall:
            near = [s for s in on_floor if seen(n) and seen(s) and classes[s] != "rug" and metres(s, n) <= 2.5]
            anchor = min(near, key=lambda s: metres(s, n), default=None)
            if anchor is not None:
                entry.anchor = str(items[anchor].item_id)
                offset = geometry.from_piece(anchor, n)
                entry.offset_m = (offset[0] * scale, offset[1] * scale) if offset else None
            size = built[n][1].size_m
            if seen(n) and floor_height is not None and size:
                tall = size[1] if n in lying else size[2]
                entry.hang_m = min(max((geometry.height[n] - tall / 2 - floor_height) * scale, 0.4), 2.2)
        elif thing.beside is not None and thing.beside in on_floor and thing.beside != n:
            entry.beside = str(items[thing.beside].item_id)
        scene.things.append(entry)
    return scene
