"""OBJECT_PROVIDER=stub: no network. The mask is the whole image and the 3D model is a plain box of
the typical size for its class. Lets the whole pipeline run without a fal.ai key, and in tests."""

from __future__ import annotations

import io

import trimesh
from PIL import Image

from services.api.objects.classes import TYPICAL_SIZE_M
from services.api.objects.providers.base import ImageReading, Piece, Segment


class FilenameDescriber:
    """Used when there is no LLM either: guesses the class from the file name."""

    def read(self, image: bytes, mime_type: str, filename: str) -> ImageReading:
        name = filename.lower().replace("-", "_").replace(" ", "_")
        item_class = next((c for c in sorted(TYPICAL_SIZE_M, key=len, reverse=True) if c in name), "other")
        label = item_class.replace("_", " ").capitalize()
        return ImageReading(kind="product_photo", pieces=[Piece(label=label, item_class=item_class, segment_prompt=label.lower())])


class WholeImageSegmenter:
    def segment(self, image: bytes, mime_type: str, prompt: str) -> Segment:
        picture = Image.open(io.BytesIO(image)).convert("RGBA")
        mask, cutout = io.BytesIO(), io.BytesIO()
        Image.new("L", picture.size, 255).save(mask, "PNG")
        picture.save(cutout, "PNG")
        return Segment(mask.getvalue(), cutout.getvalue())


class BoxTo3D:
    def generate(self, cutout_png: bytes, piece: Piece) -> bytes:
        w, d, h = TYPICAL_SIZE_M.get(piece.item_class, TYPICAL_SIZE_M["other"])
        box = trimesh.creation.box(extents=(w, h, d))  # glTF is Y up: (x, y, z) = (w, h, d)
        r, g, b = Image.open(io.BytesIO(cutout_png)).convert("RGB").resize((1, 1)).getpixel((0, 0))  # mean color
        box.visual.vertex_colors = (r, g, b, 255)
        return trimesh.Scene(box).export(file_type="glb")
