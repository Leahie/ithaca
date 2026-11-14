"""Scan-point panoramas (Matterport skyboxes), re-cut as standard GL cube faces.

Matterport's six skybox faces are 0 up, 1 to 4 the sides turning right, 5 down, in a Y-up pano
frame that its rotation quaternion takes to the Model API frame. Fitted against the point cloud's
colors (which come from the same photos): side faces correlate 0.70 with this convention and 0.2
or less with any other. Here the faces are re-cut so that, in GLSL,

    textureCube(skybox, inverse(sweep_rotation) * (p - eye))

needs no flips: face order px nx py ny pz nz, each face laid out as the OpenGL spec's cube table.

Defurnished panoramas come from the model's matterport.model.defurnished view, which Matterport
serves at 2k at most. Its faces are pixel-aligned with the furnished ones (same sweep ids, median
difference ~1/255) and differ only where furniture was painted out, so a 4k defurnished face is a
hybrid: the furnished 4k face, with the regions where the defurnished 2k face differs (dilated and
feathered) replaced by that face upscaled. On the fixture model 0-12% of a face is replaced.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageFilter
from scipy import ndimage
from scipy.spatial.transform import Rotation

from services.api.integrations.matterport import MatterportClient
from services.api.twin.mesh import MODEL_TO_INTERIOR, RAW_DIR

log = logging.getLogger(__name__)

FACES = ("px", "nx", "py", "ny", "pz", "nz")
RESOLUTIONS = ("low", "2k", "high", "4k")
URL_TTL_S = 30 * 60

DEFURNISHED_VIEW = "matterport.model.defurnished"
DEFURNISHED_RESOLUTIONS = ("low", "high", "2k")  # what the defurnished view serves (it rejects 4k)
DEFURNISHED_MAX_RES = "2k"
HYBRID_BLUR_PX = 3           # both faces are blurred before comparing, so JPEG noise and edges don't count
HYBRID_DIFF = 12             # mean |RGB| difference (0-255) that marks a pixel as painted out
HYBRID_OPEN_ITERS = 2        # specks smaller than this (at 2k) are dropped from the mask
HYBRID_GROW_ITERS = 12       # the mask is grown by this many 2k pixels to cover the furniture's halo
HYBRID_FEATHER_PX = 8        # the seam is blended over about this many 4k pixels

# pano frame (Y up) -> Matterport model frame (Z up); then a quarter turn so the sides line up
_Y_UP_TO_Z_UP = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)
_SIDE_TURN = Rotation.from_euler("y", 270, degrees=True).as_matrix()

_urls: dict[tuple[str, str], tuple[float, dict[str, list[str]]]] = {}
_locks: dict[tuple[str, str, str, bool], threading.Lock] = {}
_view_ids: dict[str, str] = {}


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


def skybox_dir(model_id: str, sweep_id: str, res: str, defurnished: bool = False) -> Path:
    return RAW_DIR / model_id / ("skybox_defurnished" if defurnished else "skybox") / res / sweep_id


def skybox_face(model_id: str, sweep_id: str, face: str, res: str = "2k",
                client: MatterportClient | None = None, *, defurnished: bool = False) -> Path:
    """Path to the GL face JPEG, downloading and re-cutting the sweep's skybox on first use."""
    if face not in FACES or res not in RESOLUTIONS:
        raise ValueError(f"face must be one of {FACES}, res one of {RESOLUTIONS}")
    out = skybox_dir(model_id, sweep_id, res, defurnished)
    with _locks.setdefault((model_id, sweep_id, res, defurnished), threading.Lock()):
        if not all((out / f"{name}.jpg").exists() for name in FACES):
            if defurnished:
                faces = load_defurnished_faces(model_id, sweep_id, res, client)
            else:
                faces = load_matterport_faces(model_id, sweep_id, res, client)
            out.mkdir(parents=True, exist_ok=True)
            n = faces[0].shape[0]
            for name in FACES:
                pixels = sample_matterport(faces, gl_face_directions(name, n))
                partial = out / f"{name}.part.jpg"
                Image.fromarray(pixels).save(partial, quality=95, subsampling=0)  # the re-cut is a pixel shuffle; keep it near lossless
                partial.replace(out / f"{name}.jpg")
    return out / f"{face}.jpg"


def load_defurnished_faces(model_id: str, sweep_id: str, res: str,
                           client: MatterportClient | None = None) -> list[np.ndarray]:
    """Matterport-order defurnished faces: the view's own up to 2k, the furnished hybrid above."""
    view_id = _defurnished_view_id(model_id, client)
    if res in DEFURNISHED_RESOLUTIONS:
        return load_matterport_faces(view_id, sweep_id, res, client)
    furnished = load_matterport_faces(model_id, sweep_id, res, client)
    painted = load_matterport_faces(view_id, sweep_id, DEFURNISHED_MAX_RES, client)
    out, changed = [], []
    for f, d in zip(furnished, painted):
        face, frac = hybrid_face(f, d)
        out.append(face)
        changed.append(frac)
    log.info("defurnished %s %s %s: %s%% of each face from %s", model_id, sweep_id, res,
             "/".join(f"{100 * c:.0f}" for c in changed), DEFURNISHED_MAX_RES)
    return out


def hybrid_face(furnished: np.ndarray, defurnished: np.ndarray) -> tuple[np.ndarray, float]:
    """The furnished face (N, N, 3) with its painted-out regions taken from the smaller defurnished
    face (n, n, 3) of the same view, upscaled. Also returns the fraction of the face replaced."""
    big, small = furnished.shape[0], defurnished.shape[0]
    f_img, d_img = Image.fromarray(furnished), Image.fromarray(defurnished)
    blur = ImageFilter.GaussianBlur(HYBRID_BLUR_PX)
    f_small = np.asarray(f_img.resize((small, small), Image.LANCZOS).filter(blur), np.int16)
    diff = np.abs(np.asarray(d_img.filter(blur), np.int16) - f_small).mean(axis=2)
    mask = ndimage.binary_opening(diff > HYBRID_DIFF, iterations=HYBRID_OPEN_ITERS)
    if not mask.any():
        return furnished, 0.0
    mask = ndimage.binary_dilation(mask, iterations=HYBRID_GROW_ITERS)
    alpha = Image.fromarray(mask.astype(np.uint8) * 255).resize((big, big), Image.BILINEAR)
    alpha = np.asarray(alpha.filter(ImageFilter.GaussianBlur(HYBRID_FEATHER_PX)), np.float32)[..., None] / 255
    up = np.asarray(d_img.resize((big, big), Image.LANCZOS), np.float32)
    out = furnished * (1 - alpha) + up * alpha
    return np.clip(out + 0.5, 0, 255).astype(np.uint8), float(mask.mean())


def _defurnished_view_id(model_id: str, client: MatterportClient | None) -> str:
    if model_id not in _view_ids:
        _view_ids[model_id] = (client or MatterportClient()).view_id(model_id, DEFURNISHED_VIEW)
    return _view_ids[model_id]


def load_matterport_faces(model_id: str, sweep_id: str, res: str,
                          client: MatterportClient | None = None) -> list[np.ndarray]:
    for attempt in range(2):
        urls = _skybox_urls(model_id, res, client).get(sweep_id)
        if not urls or len(urls) != 6:
            raise KeyError(f"no {res} skybox for sweep {sweep_id}")
        try:
            return [np.asarray(Image.open(io.BytesIO(_get(u))).convert("RGB")) for u in urls]
        except requests.HTTPError as err:
            # The signed links can expire well before URL_TTL_S (410 Gone, 403): fetch new ones once.
            if attempt or err.response is None or err.response.status_code not in (403, 410):
                raise
            _urls.pop((model_id, res), None)
    raise AssertionError("unreachable")


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
