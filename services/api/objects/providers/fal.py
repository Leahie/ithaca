"""fal.ai providers: SAM 3 for segmentation, TRELLIS 2 or SAM 3D Objects for image-to-3D.

Request and response fields follow fal's model pages (fal-ai/sam-3/image, fal-ai/trellis-2,
fal-ai/sam-3/3d-objects) as read on 2026-11-14. Nothing here has been run against the live API yet.
"""

from __future__ import annotations

import io

import httpx
from PIL import Image

from services.api.integrations.config import IntegrationNotConfigured, env
from services.api.objects.providers.base import Piece, Segment


def _client():
    if not env("FAL_KEY"):
        raise IntegrationNotConfigured("fal.ai", "FAL_KEY")
    import fal_client

    return fal_client  # reads FAL_KEY from the environment


def _download(url: str) -> bytes:
    response = httpx.get(url, timeout=120, follow_redirects=True)
    response.raise_for_status()
    return response.content


class FalSegmenter:
    """SAM 3 with a text prompt. Returns the best mask, or None when it finds nothing."""

    def segment(self, image: bytes, mime_type: str, prompt: str) -> Segment | None:
        fal = _client()
        result = fal.subscribe(
            "fal-ai/sam-3/image",
            arguments={"image_url": fal.upload(image, mime_type), "prompt": prompt, "apply_mask": False, "output_format": "png"},
        )
        masks = result.get("masks") or []
        if not masks:
            return None
        picture = Image.open(io.BytesIO(image)).convert("RGBA")
        mask = Image.open(io.BytesIO(_download(masks[0]["url"]))).convert("L").resize(picture.size)
        box = mask.getbbox()
        if box is None:
            return None
        picture.putalpha(mask)
        mask_png, cutout_png = io.BytesIO(), io.BytesIO()
        mask.save(mask_png, "PNG")
        picture.crop(box).save(cutout_png, "PNG")
        return Segment(mask_png.getvalue(), cutout_png.getvalue())


class FalImageTo3D:
    """OBJECT_3D_MODEL=trellis (default) | sam3d.

    TRELLIS 2 returns an upright object from a single cut-out image (about $0.30 each).
    SAM 3D Objects is cheaper but reconstructs the object as posed in the photo.
    """

    def generate(self, cutout_png: bytes, piece: Piece) -> bytes:
        fal = _client()
        image_url = fal.upload(cutout_png, "image/png")
        if env("OBJECT_3D_MODEL", "trellis").lower() == "sam3d":
            result = fal.subscribe("fal-ai/sam-3/3d-objects", arguments={"image_url": image_url, "prompt": piece.segment_prompt, "export_textured_glb": True})
        else:
            result = fal.subscribe(
                "fal-ai/trellis-2",
                arguments={"image_url": image_url, "resolution": 1024, "decimation_target": 40000, "texture_size": 1024},
            )
        glb = result.get("model_glb")
        if not glb:
            raise RuntimeError("the 3D model service returned no GLB file")
        return _download(glb["url"])
