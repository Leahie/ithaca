"""Segmentation on this machine, no API key: Grounding DINO finds the thing a text prompt names and
SAM 2.1 traces its outline. Runs on a GPU when there is one, else on the CPU (about 10 s a piece).

Needs the packages in services/api/objects/requirements-segment.txt. The model weights (about
1.7 GB) download from Hugging Face on first use and are cached.
"""

from __future__ import annotations

import importlib.util
import io
import threading

import numpy as np
from PIL import Image

from services.api.integrations.config import IntegrationNotConfigured, env
from services.api.objects.providers.base import Segment, cut_out, pick

DETECTOR = "IDEA-Research/grounding-dino-base"
OUTLINER = "facebook/sam2.1-hiera-large"
MIN_SCORE = 0.25  # detections below this are noise
MIN_PART = 0.02  # mask islands smaller than this share of the mask are dropped as specks
MIN_OUTLINE = 0.7  # SAM's own estimate of how well its mask fits; cluttered things score ~0.4

_models = None
_lock = threading.Lock()


def available() -> bool:
    return all(importlib.util.find_spec(name) for name in ("torch", "transformers", "scipy"))


def _load():
    """(torch, device, detector processor, detector, SAM processor, SAM), loaded once."""
    global _models
    if _models is None:
        if not available():
            raise IntegrationNotConfigured("local segmentation", "up with: pip install -r services/api/objects/requirements-segment.txt")
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor, Sam2Model, Sam2Processor

        device = "cuda" if torch.cuda.is_available() else "cpu"
        detector, outliner = env("SEGMENT_DETECTOR") or DETECTOR, env("SEGMENT_OUTLINER") or OUTLINER
        _models = (
            torch,
            device,
            AutoProcessor.from_pretrained(detector),
            AutoModelForZeroShotObjectDetection.from_pretrained(detector).to(device).eval(),
            Sam2Processor.from_pretrained(outliner),
            Sam2Model.from_pretrained(outliner).to(device).eval(),
        )
    return _models


def _without_specks(mask: np.ndarray) -> np.ndarray:
    from scipy import ndimage

    islands, count = ndimage.label(mask)
    if count < 2:
        return mask
    sizes = np.bincount(islands.ravel())[1:]
    return np.isin(islands, 1 + np.flatnonzero(sizes >= MIN_PART * mask.sum()))


class LocalSegmenter:
    """Returns None when the prompt finds nothing, and raises ValueError when the thing is found
    but has no clear outline. The mask covers only what is visible: a desk behind a chair comes
    back in pieces, which SAM 3D Objects can still rebuild and a cut-out based model cannot."""

    def segment(self, image: bytes, mime_type: str, prompt: str, near: list[float] | None = None, at: tuple[float, float] | None = None) -> Segment | None:
        picture = Image.open(io.BytesIO(image)).convert("RGB")
        with _lock:  # one model, one image at a time
            torch, device, read, detector, prepare, outliner = _load()
            with torch.no_grad():
                inputs = read(images=picture, text=prompt.lower().strip(" .") + ".", return_tensors="pt").to(device)
                found = read.post_process_grounded_object_detection(
                    detector(**inputs), inputs.input_ids, threshold=MIN_SCORE, text_threshold=MIN_SCORE, target_sizes=[picture.size[::-1]]
                )[0]
                boxes = [box.tolist() for box in found["boxes"]]
                w, h = picture.size
                best = pick([(float(score), [box[0] / w, box[1] / h, box[2] / w, box[3] / h]) for score, box in zip(found["scores"], boxes)], near, at)
                if best is None:
                    return None
                box = boxes[best]
                inputs = prepare(images=picture, input_boxes=[[box]], return_tensors="pt").to(device)
                # SAM proposes three outlines (a part, the whole, something between) and scores each.
                traced = outliner(**inputs, multimask_output=True)
                masks = prepare.post_process_masks(traced.pred_masks.cpu(), inputs["original_sizes"].cpu())[0][0]
                scores = traced.iou_scores.flatten().cpu()
        best = int(scores.argmax())
        if float(scores[best]) < MIN_OUTLINE:
            raise ValueError("its outline is not clear in the photo")
        mask = _without_specks(masks[best].numpy() > 0)
        return cut_out(image, Image.fromarray(mask.astype(np.uint8) * 255))
