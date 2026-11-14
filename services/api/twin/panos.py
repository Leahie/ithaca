"""Scan-point panoramas (Matterport skyboxes), re-cut as standard GL cube faces.

Matterport's six skybox faces are 0 up, 1 to 4 the sides turning right, 5 down, in a Y-up pano
frame that its rotation quaternion takes to the Model API frame. Fitted against the point cloud's
colors (which come from the same photos): side faces correlate 0.70 with this convention and 0.2
or less with any other. Here the faces are re-cut so that, in GLSL,

    textureCube(skybox, inverse(sweep_rotation) * (p - eye))

needs no flips: face order px nx py ny pz nz, each face laid out as the OpenGL spec's cube table.
"""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from scipy.spatial.transform import Rotation

from services.api.integrations.matterport import MatterportClient
from services.api.twin.mesh import MODEL_TO_INTERIOR, RAW_DIR

log = logging.getLogger(__name__)

FACES = ("px", "nx", "py", "ny", "pz", "nz")
RESOLUTIONS = ("low", "2k", "high", "4k")
URL_TTL_S = 30 * 60

# pano frame (Y up) -> Matterport model frame (Z up); then a quarter turn so the sides line up
_Y_UP_TO_Z_UP = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)
_SIDE_TURN = Rotation.from_euler("y", 270, degrees=True).as_matrix()

_urls: dict[tuple[str, str], tuple[float, dict[str, list[str]]]] = {}


def pano_to_interior(rotation: dict) -> np.ndarray:
    """3x3 matrix taking pano-frame directions to the interior frame (rotation: Matterport's x y z w)."""
    q = Rotation.from_quat([rotation[k] for k in "xyzw"]).as_matrix()
    return MODEL_TO_INTERIOR[:3, :3] @ q @ _Y_UP_TO_Z_UP @ _SIDE_TURN


def interior_quaternion(rotation: dict) -> tuple[float, float, float, float]:
    x, y, z, w = Rotation.from_matrix(pano_to_interior(rotation)).as_quat()
    return float(x), float(y), float(z), float(w)


def gl_face_directions(face: str, n: int) -> np.ndarray:
    """(n, n, 3) pano-frame directions through the pixel centers of a GL cube face (row 0 = top)."""
    s = (np.arange(n) + 0.5) / n * 2 - 1
    sc, tc = np.meshgrid(s, s)  # sc across columns, tc down rows
    one = np.ones_like(sc)
    d = {
        "px": (one, -tc, -sc), "nx": (-one, -tc, sc),
        "py": (sc, one, tc), "ny": (sc, -one, -tc),
        "pz": (sc, -tc, one), "nz": (-sc, -tc, -one),
    }[face]
    return np.stack(d, axis=-1)


def sample_matterport(faces: list[np.ndarray], local: np.ndarray) -> np.ndarray:
    """Colors of pano-frame directions (..., 3) from Matterport's six faces (nearest pixel)."""
    flat = local.reshape(-1, 3)
    x, y, z = flat.T
    ax = np.abs(flat)
    dom = np.argmax(ax, axis=1)
    m = ax.max(axis=1)
    picks = (
        (1, (dom == 2) & (z < 0), x / m, -y / m),
        (2, (dom == 0) & (x > 0), z / m, -y / m),
        (3, (dom == 2) & (z > 0), -x / m, -y / m),
        (4, (dom == 0) & (x < 0), -z / m, -y / m),
        (0, (dom == 1) & (y > 0), x / m, -z / m),
        (5, (dom == 1) & (y < 0), x / m, z / m),
    )
    out = np.zeros((len(flat), 3), np.uint8)
    for f, mask, s, t in picks:
        img = faces[f]
        n = img.shape[0]
        col = np.clip(((s[mask] + 1) / 2 * n).astype(int), 0, n - 1)
        row = np.clip(((t[mask] + 1) / 2 * n).astype(int), 0, n - 1)
        out[mask] = img[row, col]
    return out.reshape(local.shape)


def skybox_dir(model_id: str, sweep_id: str, res: str) -> Path:
    return RAW_DIR / model_id / "skybox" / res / sweep_id


def skybox_face(model_id: str, sweep_id: str, face: str, res: str = "2k",
                client: MatterportClient | None = None) -> Path:
    """Path to the GL face JPEG, downloading and re-cutting the sweep's skybox on first use."""
    if face not in FACES or res not in RESOLUTIONS:
        raise ValueError(f"face must be one of {FACES}, res one of {RESOLUTIONS}")
    out = skybox_dir(model_id, sweep_id, res)
    if not (out / f"{face}.jpg").exists():
        faces = load_matterport_faces(model_id, sweep_id, res, client)
        out.mkdir(parents=True, exist_ok=True)
        n = faces[0].shape[0]
        for name in FACES:
            pixels = sample_matterport(faces, gl_face_directions(name, n))
            Image.fromarray(pixels).save(out / f"{name}.jpg", quality=90)
    return out / f"{face}.jpg"


def load_matterport_faces(model_id: str, sweep_id: str, res: str,
                          client: MatterportClient | None = None) -> list[np.ndarray]:
    urls = _skybox_urls(model_id, res, client).get(sweep_id)
    if not urls or len(urls) != 6:
        raise KeyError(f"no {res} skybox for sweep {sweep_id}")
    return [np.asarray(Image.open(io.BytesIO(_get(u))).convert("RGB")) for u in urls]


def _skybox_urls(model_id: str, res: str, client: MatterportClient | None) -> dict[str, list[str]]:
    """Signed CDN URLs, refetched after URL_TTL_S (they expire)."""
    hit = _urls.get((model_id, res))
    if hit and time.time() - hit[0] < URL_TTL_S:
        return hit[1]
    urls = (client or MatterportClient()).skybox_urls(model_id, res)
    _urls[(model_id, res)] = (time.time(), urls)
    return urls


def _get(url: str) -> bytes:
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content
