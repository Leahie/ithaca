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


class RoomThing(BaseModel):
    """One thing in a photo of someone's own room: a piece of furniture, or a belonging or
    decoration that stands on one, hangs on the wall, or stands on the floor. Only the fields a
    room photo needs: the model writes one of these per thing, and every field it writes is time."""

    label: str = Field(description='Short name a person would use, e.g. "White desk".')
    item_class: str = Field(description=f"One of: {', '.join(FURNITURE_CLASSES)}.")
    segment_prompt: str = Field(description='Two or three plain words that pick it out in the photo: its colour or material and what it is, e.g. "white desk". The whole thing, never its parts ("white dresser", not "five-drawer dresser").')
    box: list[float] = Field(description="Rough box around the whole thing, as fractions of the image width and height: [left, top, right, bottom], each from 0 to 1.")
    rests_on: int | None = Field(None, description="If it stands or lies on a piece of furniture in this list (a lamp on a desk, a plant on a dresser): that piece's number, counting from 0. Empty for things on the floor or on the wall.")
    on_wall: bool = Field(False, description="True if it is fixed to a wall: a picture, a mirror, a wall lamp, a shelf, a clock.")
    beside: int | None = Field(None, description="For a piece on the floor that goes with another (a nightstand beside the bed, a chair at the desk, a lamp by the armchair): that piece's number.")

    def piece(self) -> Piece:
        """The same thing as the rest of the pipeline knows a piece."""
        return Piece(label=self.label, item_class=self.item_class, segment_prompt=self.segment_prompt, box=self.box)


class RoomPhotoReading(BaseModel):
    room: str = Field(description="What kind of room this is, in a word or two: bedroom, living room, office.")
    things: list[RoomThing] = Field(description="Furniture first, largest first, then the smaller things. Each thing once. At most 14.")


@dataclass
class PhotoPose:
    """Where the 3D model says an object is in the photo, in the camera's own frame (SAM 3D
    Objects). Only comparisons between objects of one photo are made from it: which way is up,
    what stands on what, how far apart, how they are turned to each other (pipeline.py)."""

    rotation: tuple[float, float, float, float]  # quaternion (w, x, y, z), camera frame to the object's own
    translation: tuple[float, float, float]  # the object's middle
    scale: float  # of the unit-sized model, in the photo's own (roughly metric) units


@dataclass
class Segment:
    mask_png: bytes  # white = the piece, black = everything else, same size as the image
    cutout_png: bytes  # the piece on a transparent background, cropped to it


@dataclass
class Model3D:
    glb: bytes  # glTF binary of the piece, any scale and position
    size_m: tuple[float, float, float] | None = None  # (w, d, h) in meters, when the model estimates the real size
    pose: PhotoPose | None = None  # where it is in the photo, when the model says


MIN_OVERLAP = 0.1  # a detection must overlap the hint box at least this much (intersection over union)


def overlap(a: list[float], b: list[float]) -> float:
    """Intersection over union of two [left, top, right, bottom] boxes."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    both = w * h
    return both / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - both)


def contains(box: list[float], at: tuple[float, float]) -> bool:
    return box[0] <= at[0] <= box[2] and box[1] <= at[1] <= box[3]


def pick(found: list[tuple[float, list[float]]], near: list[float] | None, at: tuple[float, float] | None = None) -> int | None:
    """Which detection is the piece. `found` holds (score, box) per detection, boxes as fractions of
    the image like Piece.box. A prompt alone can land on a look-alike (a monitor for "air
    purifier"), so with a hint only detections near it count, and with a tapped spot only those
    around the spot. None when nothing qualifies."""
    if at is not None:
        found = [(score if contains(box, at) else -1.0, box) for score, box in found]
        ranked = [(score, n) for n, (score, _) in enumerate(found) if score >= 0]
        return max(ranked)[1] if ranked else None
    if near and len(near) == 4:
        ranked = [(score * overlap(box, near), n) for n, (score, box) in enumerate(found) if overlap(box, near) >= MIN_OVERLAP]
    else:
        ranked = [(score, n) for n, (score, _) in enumerate(found)]
    return max(ranked)[1] if ranked else None


def covers(mask: Image.Image, at: tuple[float, float]) -> bool:
    """Whether a mask (any mode, white = the piece) covers a spot given as fractions of the image."""
    gray = mask.convert("L")
    x, y = min(int(at[0] * gray.width), gray.width - 1), min(int(at[1] * gray.height), gray.height - 1)
    return gray.getpixel((x, y)) > 127


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


class TapDescriber(Protocol):
    def read_at(self, image: bytes, mime_type: str, at: tuple[float, float]) -> Piece:
        """The one thing at the spot the user tapped (fractions of the image), with its box."""
        ...


class RoomDescriber(Protocol):
    def read_frames(self, frames: list[bytes]) -> RoomReading:
        """The furniture seen across several JPEG frames of one room, each piece listed once."""
        ...


class RoomPhotoDescriber(Protocol):
    def read_room(self, image: bytes, mime_type: str) -> RoomPhotoReading:
        """Everything of the person's in one photo of their room, with what stands on what."""
        ...


class Segmenter(Protocol):
    def segment(self, image: bytes, mime_type: str, prompt: str, near: list[float] | None = None, at: tuple[float, float] | None = None) -> Segment | None:
        """Cut out the thing `prompt` names. `near` is Piece.box: where it roughly is, if known.
        `at` is a spot the user tapped on it (fractions of the image): the cut-out must cover it."""
        ...


class BoxSegmenter(Protocol):
    def segment_box(self, image: bytes, mime_type: str, box: list[float]) -> Segment | None:
        """Cut out whatever fills `box` ([left, top, right, bottom] as fractions of the image):
        for a thing no words find (a woven stool, a small wall lamp)."""
        ...


class ImageTo3D(Protocol):
    def generate(self, image: bytes, segment: Segment, piece: Piece) -> Model3D:
        """A 3D model of the piece. `image` is the whole photo and `segment` marks the piece in it."""
        ...
