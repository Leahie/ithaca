"""The hackathon's aerial Gaussian splat of the property (trained from drone photos by Matterport).

`splat.ply` is a standard 3DGS file (position, normal, SH degree-0 colour, logit opacity, log
scale, rotation quaternion; 17 floats per Gaussian) in its own local frame: Z up, roughly meters,
not turned to north. Only the property and its near surroundings are kept; the rest of the 6 M
Gaussians are background blobs up to kilometers away.

    python -m services.api.twin.splat onXQLXhfyQv
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import requests

from services.api.twin.mesh import RAW_DIR

log = logging.getLogger(__name__)

RESOURCES = "https://beta-app.matterport.com/2026-princeton-hackathon/resources"
FIELDS = ("x", "y", "z", "nx", "ny", "nz", "f_dc_0", "f_dc_1", "f_dc_2", "opacity",
          "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3")
SH_C0 = 0.28209479177387814
CHUNK = 500_000


def splat_dir(model_id: str) -> Path:
    return RAW_DIR / model_id / "splat"


def download(model_id: str) -> Path:
    """splat.ply and the sky cubemap it was trained against (each link redirects to a signed URL)."""
    out = splat_dir(model_id)
    out.mkdir(parents=True, exist_ok=True)
    for face in ("px", "nx", "py", "ny", "pz", "nz"):
        target = out / f"envmap_{face}.jpg"
        if not target.exists():
            r = requests.get(f"{RESOURCES}/{model_id}/splat/envmap/{face}.jpg", timeout=60)
            r.raise_for_status()
            target.write_bytes(r.content)
    ply = out / "splat.ply"
    if not ply.exists():
        with requests.get(f"{RESOURCES}/{model_id}/splat/splat.ply", stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(ply.with_suffix(".part"), "wb") as f:
                for block in r.iter_content(8 << 20):
                    f.write(block)
        ply.with_suffix(".part").rename(ply)
    return ply


def open_ply(path: Path) -> np.memmap:
    """(N, 17) float32 rows, memory-mapped (the file is hundreds of MB)."""
    with open(path, "rb") as f:
        header = []
        while (line := f.readline().decode("ascii").strip()) != "end_header":
            header.append(line)
        offset = f.tell()
    props = tuple(h.split()[-1] for h in header if h.startswith("property"))
    if props != FIELDS:
        raise ValueError(f"unexpected splat layout: {props}")
    return np.memmap(path, dtype=np.float32, mode="r", offset=offset).reshape(-1, len(FIELDS))


def select(rows: np.memmap, center_xy: tuple[float, float], radius_m: float,
           min_opacity: float = 0.0) -> np.ndarray:
    """Rows within radius_m (horizontally) of center_xy, read chunk by chunk."""
    kept = []
    for start in range(0, len(rows), CHUNK):
        block = np.asarray(rows[start:start + CHUNK])
        near = np.hypot(block[:, 0] - center_xy[0], block[:, 1] - center_xy[1]) <= radius_m
        if min_opacity > 0:
            near &= opacity(block) >= min_opacity
        kept.append(block[near])
    return np.concatenate(kept)


def opacity(rows: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-rows[:, 9]))


def colors(rows: np.ndarray) -> np.ndarray:
    return np.clip(0.5 + SH_C0 * rows[:, 6:9], 0.0, 1.0)


def footprint(twin) -> "BaseGeometry":
    """Union of every room outline, in world (east, north) meters."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    m = np.array(twin.interior_to_world).reshape(4, 4, order="F")
    shapes = []
    for room in twin.rooms:
        corners = np.array([[x, 0.0, z, 1.0] for x, z in room.polygon]) @ m.T
        shapes.append(Polygon(corners[:, :2]).buffer(0))
    return unary_union(shapes).buffer(0.3).buffer(-0.3)


def roof_points(rows: np.ndarray, min_rise_m: float = 2.5) -> np.ndarray:
    """(x, y) of opaque Gaussians well above the local ground: roofs and tree crowns."""
    solid = rows[opacity(rows) > 0.5]
    ground = np.percentile(solid[:, 2], 5)
    return solid[solid[:, 2] > ground + min_rise_m, :2]


def align_2d(roof_xy: np.ndarray, outline, cell_m: float = 0.25, ring_m: tuple[float, float] = (1.0, 4.0),
             yaws_deg=np.arange(0.0, 360.0, 1.0), scales=(0.9, 0.95, 1.0, 1.05, 1.1)) -> dict:
    """Rotation, scale and shift taking splat (x, y) onto world (east, north).

    Scores how well the roof covers the house outline (+1 inside it) without spilling into a ring
    just outside it (-1; the ring starts past the eave overhang). Exhaustive over yaw and scale,
    FFT cross-correlation over the shift.
    """
    from shapely import contains_xy

    minx, miny, maxx, maxy = outline.bounds
    pad = ring_m[1] + 2.0
    size = int(np.ceil(max(maxx - minx, maxy - miny) / cell_m + 2 * pad / cell_m)) + 1
    size = 1 << int(np.ceil(np.log2(size * 2)))  # room to slide the roof around
    ox, oy = minx - pad - (size * cell_m - (maxx - minx) - 2 * pad) / 2, miny - pad - (size * cell_m - (maxy - miny) - 2 * pad) / 2
    gx, gy = np.meshgrid(ox + (np.arange(size) + 0.5) * cell_m, oy + (np.arange(size) + 0.5) * cell_m, indexing="ij")
    inside = contains_xy(outline, gx, gy)
    near = contains_xy(outline.buffer(ring_m[1]), gx, gy) & ~contains_xy(outline.buffer(ring_m[0]), gx, gy)
    kernel = inside.astype(np.float32) - near.astype(np.float32)
    kernel_f = np.conj(np.fft.rfft2(kernel))

    centered = roof_xy - roof_xy.mean(axis=0)
    best = {"score": -np.inf}
    for scale in scales:
        for yaw in yaws_deg:
            c, s = np.cos(np.radians(yaw)), np.sin(np.radians(yaw))
            p = scale * centered @ np.array([[c, s], [-s, c]])
            img, _, _ = np.histogram2d(p[:, 0], p[:, 1], bins=size,
                                       range=[[-size * cell_m / 2, size * cell_m / 2]] * 2)
            score = np.fft.irfft2(np.fft.rfft2(np.minimum(img, 1.0)) * kernel_f, s=(size, size))
            k = np.unravel_index(np.argmax(score), score.shape)
            if score[k] > best["score"]:
                # score[k] = sum_x roof[x + k] * kernel[x]: roof cell r lands on kernel cell r - k
                lag = np.array([v - size if v > size // 2 else v for v in k], float)
                shift = np.array([ox, oy]) + size * cell_m / 2 - lag * cell_m
                best = {"score": float(score[k]), "yaw_deg": float(yaw), "scale": float(scale), "shift": shift}
    yaw = np.radians(best["yaw_deg"])
    rot = best["scale"] * np.array([[np.cos(yaw), -np.sin(yaw)], [np.sin(yaw), np.cos(yaw)]])
    best["matrix_2d"] = rot
    best["offset_2d"] = best["shift"] - rot @ roof_xy.mean(axis=0)
    return best


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model_id")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows = open_ply(download(args.model_id))
    print(f"{len(rows):,} Gaussians")


if __name__ == "__main__":
    main()
