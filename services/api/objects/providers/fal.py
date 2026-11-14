"""fal.ai providers: SAM 3 for segmentation, SAM 3D Objects or TRELLIS 2 for image-to-3D.

Request and response fields follow fal's API pages (fal-ai/sam-3/image, fal-ai/sam-3/3d-objects,
fal-ai/trellis-2). SAM 3 and SAM 3D Objects were run against the live API on 2026-11-14: about 2 s
and 30 s a piece. The TRELLIS 2 branch has not been run.
"""

from __future__ import annotations

import io

import httpx
import trimesh
from PIL import Image

from services.api.integrations.config import IntegrationNotConfigured, env
from services.api.objects.providers.base import Model3D, Piece, Segment, cut_out, pick


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
    """SAM 3 with a text prompt. Returns None when it finds nothing (near the hint, if given)."""

    def segment(self, image: bytes, mime_type: str, prompt: str, near: list[float] | None = None) -> Segment | None:
        fal = _client()
        result = fal.subscribe(
            "fal-ai/sam-3/image",
            arguments={
                "image_url": fal.upload(image, mime_type),
                "prompt": prompt,
                "apply_mask": False,
                "output_format": "png",
                "return_multiple_masks": True,  # one mask per thing the prompt matches
                "max_masks": 5,
                "include_scores": True,
                "include_boxes": True,
            },
        )
        masks = result.get("masks") or []
        scores, boxes = result.get("scores") or [], result.get("boxes") or []
        if len(scores) == len(boxes) == len(masks):
            # fal's boxes are [center x, center y, width, height] as fractions of the image
            best = pick([(score, [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]) for score, (cx, cy, w, h) in zip(scores, boxes)], near)
        else:
            best = 0 if masks else None
        if best is None:
            return None
        return cut_out(image, Image.open(io.BytesIO(_download(masks[best]["url"]))))


class FalImageTo3D:
    """OBJECT_3D_MODEL=sam3d (default) | trellis.

    SAM 3D Objects (about $0.02) sees the whole photo and the mask, so it fills in what other
    things hide, and it estimates the real size. Its model is upright with the front toward +Z.
    TRELLIS 2 (about $0.30) sees only the cut-out, so it needs a piece that is fully visible.
    """

    def generate(self, image: bytes, segment: Segment, piece: Piece) -> Model3D:
        fal = _client()
        if env("OBJECT_3D_MODEL", "sam3d").lower() == "trellis":
            result = fal.subscribe(
                "fal-ai/trellis-2",
                arguments={"image_url": fal.upload(segment.cutout_png, "image/png"), "resolution": 1024, "decimation_target": 40000, "texture_size": 1024},
            )
        else:
            mime_type = Image.open(io.BytesIO(image)).get_format_mimetype() or "image/jpeg"
            result = fal.subscribe(
                "fal-ai/sam-3/3d-objects",
                arguments={"image_url": fal.upload(image, mime_type), "mask_urls": [fal.upload(segment.mask_png, "image/png")], "export_textured_glb": True},
            )
        file = result.get("model_glb")
        if not file:
            raise RuntimeError("the 3D model service returned no GLB file")
        glb = _download(file["url"])
        return Model3D(glb, _real_size(glb, result.get("metadata")))


def _real_size(glb: bytes, metadata: list[dict] | None) -> tuple[float, float, float] | None:
    """(w, d, h) in meters from SAM 3D Objects: its model fits a unit box, and `scale` is the factor
    to the size it estimated from the photo. None for models that give no scale."""
    try:
        scale = [float(s) for s in metadata[0]["scale"][0]]
        x, y, z = trimesh.load(io.BytesIO(glb), file_type="glb", force="scene").extents
        size = (float(x) * scale[0], float(z) * scale[2], float(y) * scale[1])  # glTF is Y up
    except Exception:
        return None
    return size if all(0.01 < side < 20 for side in size) else None
