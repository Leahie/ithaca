"""What the object pipeline needs from outside models. fal.ai and friends stay behind these (B7)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal, Protocol

from PIL import Image
from pydantic import BaseModel, Field

from services.api.objects.classes import FURNITURE_CLASSES


class Piece(BaseModel):
    """One piece of furniture seen in an image."""

    label: str = Field(description='Short name a person would use, e.g. "Gray three-seat sofa".')
    item_class: str = Field(description=f"One of: {', '.join(FURNITURE_CLASSES)}.")
    segment_prompt: str = Field(description='Two or three plain words that pick this piece out in the image, e.g. "gray sofa".')
    width_m: float | None = Field(None, description="Only if the image states it. Convert to meters. Width is the long side you face.")
    depth_m: float | None = Field(None, description="Only if the image states it, in meters. Front to back.")
    height_m: float | None = Field(None, description="Only if the image states it, in meters.")
    style: str | None = None
    color: str | None = None
    material: str | None = None
    price: float | None = Field(None, description="Only if shown.")
    product_title: str | None = Field(None, description="The product name, if this is a product page.")
    box: list[float] | None = Field(None, description="Rough box around the piece, as fractions of the image width and height: [left, top, right, bottom], each from 0 to 1.")


class ImageReading(BaseModel):
    kind: Literal["product_page", "product_photo", "room_photo", "other"]
    pieces: list[Piece] = Field(description="Furniture worth adding to the user's inventory, most prominent first. At most 4.")


class FramePiece(Piece):
    frame: int = Field(description="Number of the frame that shows this piece best.")
    box: list[float] = Field(description="Rough box around the piece in that frame, as fractions of the frame width and height: [left, top, right, bottom], each from 0 to 1.")


class RoomReading(BaseModel):
    pieces: list[FramePiece] = Field(description="Each distinct piece once, most prominent first. At most 8.")


@dataclass
class Segment:
    mask_png: bytes  # white = the piece, black = everything else, same size as the image
    cutout_png: bytes  # the piece on a transparent background, cropped to it


@dataclass
class Model3D:
    glb: bytes  # glTF binary of the piece, any scale and position
    size_m: tuple[float, float, float] | None = None  # (w, d, h) in meters, when the model estimates the real size


MIN_OVERLAP = 0.1  # a detection must overlap the hint box at least this much (intersection over union)


def overlap(a: list[float], b: list[float]) -> float:
    """Intersection over union of two [left, top, right, bottom] boxes."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    both = w * h
    return both / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - both)


def pick(found: list[tuple[float, list[float]]], near: list[float] | None) -> int | None:
    """Which detection is the piece. `found` holds (score, box) per detection, boxes as fractions of
    the image like Piece.box. A prompt alone can land on a look-alike (a monitor for "air
    purifier"), so with a hint only detections near it count. None when nothing qualifies."""
    if near and len(near) == 4:
        ranked = [(score * overlap(box, near), n) for n, (score, box) in enumerate(found) if overlap(box, near) >= MIN_OVERLAP]
    else:
        ranked = [(score, n) for n, (score, _) in enumerate(found)]
    return max(ranked)[1] if ranked else None


def cut_out(image: bytes, mask: Image.Image) -> Segment | None:
    """The Segment for a mask (any mode, white = the piece). None when the mask is empty."""
    picture = Image.open(io.BytesIO(image)).convert("RGBA")
    mask = mask.convert("L").resize(picture.size)
    box = mask.getbbox()
    if box is None:
        return None
    picture.putalpha(mask)
    mask_png, cutout_png = io.BytesIO(), io.BytesIO()
    mask.save(mask_png, "PNG")
    picture.crop(box).save(cutout_png, "PNG")
    return Segment(mask_png.getvalue(), cutout_png.getvalue())


class Describer(Protocol):
    def read(self, image: bytes, mime_type: str, filename: str) -> ImageReading: ...


class RoomDescriber(Protocol):
    def read_frames(self, frames: list[bytes]) -> RoomReading:
        """The furniture seen across several JPEG frames of one room, each piece listed once."""
        ...


class Segmenter(Protocol):
    def segment(self, image: bytes, mime_type: str, prompt: str, near: list[float] | None = None) -> Segment | None:
        """Cut out the thing `prompt` names. `near` is Piece.box: where it roughly is, if known."""
        ...


class ImageTo3D(Protocol):
    def generate(self, image: bytes, segment: Segment, piece: Piece) -> Model3D:
        """A 3D model of the piece. `image` is the whole photo and `segment` marks the piece in it."""
        ...
