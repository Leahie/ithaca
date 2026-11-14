"""Still frames from a phone video of a room: the sharpest frame from each stretch of the video.

A hand-held walk-through is mostly motion blur, and a blurred frame gives a bad cut-out. So the
video is sampled several times a second and each equal stretch of it gives its sharpest frame.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
SAMPLE_FPS = 6
MAX_SIDE_PX = 1600


def sharpness(jpeg: bytes) -> float:
    """Variance of the Laplacian: high for crisp edges, low for motion blur."""
    gray = np.asarray(Image.open(io.BytesIO(jpeg)).convert("L"), dtype=np.float32)
    laplacian = 4 * gray[1:-1, 1:-1] - gray[:-2, 1:-1] - gray[2:, 1:-1] - gray[1:-1, :-2] - gray[1:-1, 2:]
    return float(laplacian.var())


def sharp_frames(video: Path, count: int = 8) -> list[bytes]:
    """Up to `count` JPEG frames in time order, upright (ffmpeg applies the phone's rotation)."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is not installed; it is needed to read frames from a video")
    with tempfile.TemporaryDirectory() as folder:
        fit = f"scale='min(iw,{MAX_SIDE_PX})':'min(ih,{MAX_SIDE_PX})':force_original_aspect_ratio=decrease"
        run = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"fps={SAMPLE_FPS},{fit}", "-q:v", "2", f"{folder}/%05d.jpg"],
            capture_output=True,
            text=True,
        )
        samples = [path.read_bytes() for path in sorted(Path(folder).glob("*.jpg"))]
    if run.returncode != 0 or not samples:
        raise RuntimeError(f"could not read frames from {video.name}: {run.stderr.strip() or 'no video stream'}")
    stretches = np.array_split(np.arange(len(samples)), min(count, len(samples)))
    return [samples[max(stretch, key=lambda i: sharpness(samples[i]))] for stretch in stretches]
