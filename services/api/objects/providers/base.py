"""What the object pipeline needs from outside models. fal.ai and friends stay behind these (B7)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

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


class ImageReading(BaseModel):
    kind: Literal["product_page", "product_photo", "room_photo", "other"]
    pieces: list[Piece] = Field(description="Furniture worth adding to the user's inventory, most prominent first. At most 4.")


@dataclass
class Segment:
    mask_png: bytes  # white = the piece, black = everything else, same size as the image
    cutout_png: bytes  # the piece on a transparent background, cropped to it


class Describer(Protocol):
    def read(self, image: bytes, mime_type: str, filename: str) -> ImageReading: ...


class Segmenter(Protocol):
    def segment(self, image: bytes, mime_type: str, prompt: str) -> Segment | None: ...


class ImageTo3D(Protocol):
    def generate(self, cutout_png: bytes, piece: Piece) -> bytes:
        """A GLB (glTF binary) of the piece, any scale and position."""
        ...
