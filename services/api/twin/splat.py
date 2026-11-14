"""The hackathon's aerial Gaussian splat of the property (trained from drone photos by Matterport).

`splat.ply` is a standard 3DGS file (position, normal, SH degree-0 colour, logit opacity, log
scale, rotation quaternion; 17 floats per Gaussian) in its own local frame: Z up, roughly meters,
not turned to north. About a quarter of the 6 M Gaussians are the property and its near
surroundings; the rest are background blobs up to kilometers away (the horizon).

Placing it in the interior frame (a similarity: rotation, uniform scale, shift):
  1. 2D: roofs onto the house outline (align_2d), yaw, scale and shift in (east, north).
  2. Height: the ground in a ring around the house, splat vs the MatterPak point cloud.
  3. ICP (point to point, with scale) against the point cloud, with a shrinking match distance.
     The drone sees roofs and the scanner rooms, so only exterior walls, eaves, the yard and the
     pool deck overlap; that is enough to pin it to a few centimeters.
The house itself is cut out of the splat (the scanned interior mesh is the house; the drone's
roof would hide it from above); every other Gaussian is written unchanged, and the viewer applies
the transform.

    python -m services.api.twin.splat onXQLXhfyQv            # download, align, publish
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import requests
from scipy.spatial import cKDTree

from services.api.integrations.config import fixtures_dir
from services.api.twin import frames
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


def splat_path(property_id: str) -> Path:
    """Where the API serves it from: the download without the house."""
    return fixtures_dir() / "blobs" / property_id / "splat.ply"


def transform_path(property_id: str) -> Path:
    return fixtures_dir() / "blobs" / property_id / "splat.json"


def _apply(t: np.ndarray, p: np.ndarray) -> np.ndarray:
    return p @ t[:3, :3].T + t[:3, 3]


def _umeyama(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Least-squares similarity taking src onto dst (Umeyama 1991), as a 4x4 matrix."""
    ms, md = src.mean(axis=0), dst.mean(axis=0)
    s, d = src - ms, dst - md
    u, sigma, vt = np.linalg.svd(d.T @ s / len(src))
    e = np.eye(3)
    if np.linalg.det(u @ vt) < 0:
        e[2, 2] = -1
    r = u @ e @ vt
    c = np.trace(np.diag(sigma) @ e) / (s ** 2).sum(axis=1).mean()
    t = np.eye(4)
    t[:3, :3] = c * r
    t[:3, 3] = md - c * r @ ms
    return t


def align(twin, rows: np.memmap, cloud_interior: np.ndarray, sample: int = 400_000) -> tuple[np.ndarray, dict]:
    """4x4 matrix taking splat coordinates to the interior frame, and how well it fits."""
    from shapely import contains_xy

    to_world = frames.from_column_major(twin.interior_to_world)
    cloud = _apply(to_world, cloud_interior.astype(np.float64))
    outline = footprint(twin)

    # The house is near the splat's origin; center the crop on what is solid there.
    first = select(rows, (0.0, 0.0), 40.0, min_opacity=0.5)
    center = tuple(np.median(first[:, :2], axis=0))
    near = select(rows, center, 60.0)
    best = align_2d(roof_points(near), outline)
    t = np.eye(4)
    t[:2, :2] = best["matrix_2d"]
    t[:2, 3] = best["offset_2d"]
    t[2, 2] = best["scale"]

    solid = near[opacity(near) > 0.6, :3].astype(np.float64)
    ring = outline.buffer(8.0).difference(outline.buffer(1.0))
    w = _apply(t, solid)
    in_cloud = contains_xy(ring, cloud[:, 0], cloud[:, 1])
    in_splat = contains_xy(ring, w[:, 0], w[:, 1])
    t[2, 3] = np.percentile(cloud[in_cloud, 2], 10) - np.percentile(w[in_splat, 2], 10)

    lo, hi = cloud.min(axis=0) - 2.0, cloud.max(axis=0) + 2.0
    w = _apply(t, solid)
    src = solid[np.all((w >= lo) & (w <= hi), axis=1)]
    src = src[np.random.default_rng(0).choice(len(src), min(sample, len(src)), replace=False)]
    tree = cKDTree(cloud)
    start = t.copy()
    for limit_m in (1.0, 0.6, 0.4, 0.25, 0.15, 0.1, 0.1, 0.07, 0.07, 0.05):
        moved = _apply(t, src)
        d, i = tree.query(moved, distance_upper_bound=limit_m)
        hit = np.isfinite(d)
        t = _umeyama(moved[hit], cloud[i[hit]]) @ t

    d, _ = tree.query(_apply(t, src), distance_upper_bound=1.0)
    report = {
        "yaw_deg_2d": best["yaw_deg"],
        "scale": round(float(np.cbrt(np.linalg.det(t[:3, :3]))), 4),
        "icp_shift_m": round(float(np.linalg.norm(_apply(t, src) - _apply(start, src), axis=1).mean()), 3),
        "within_15cm": round(float((d < 0.15).mean()), 3),
        "median_m": round(float(np.median(d[np.isfinite(d)])), 3),
    }
    return np.linalg.inv(to_world) @ t, report


def publish(property_id: str, model_id: str | None = None) -> dict:
    """Download, align against the point cloud and put both where the API serves them."""
    from services.api.twin.cloud import load_cloud
    from services.api.twin.service import get_twin

    twin = get_twin(property_id)
    model_id = model_id or twin.sources.matterport_model_id or property_id
    ply = download(model_id)
    points, _ = load_cloud(model_id, 0.02)
    matrix, report = align(twin, open_ply(ply), points)
    kept = write_without_house(ply, splat_path(property_id), matrix, twin)
    report["gaussians"] = kept
    transform_path(property_id).write_text(json.dumps({
        "splat_to_interior": frames.to_column_major(matrix),
        "fit": report,
    }, indent=2) + "\n", encoding="utf-8")
    return report


def write_without_house(source: Path, target: Path, to_interior: np.ndarray, twin,
                        wall_m: float = 0.3, eave_m: float = 1.0, eave_above_m: float = 0.5) -> int:
    """Copy of the splat minus the house: everything over the outline (grown by wall_m), and above
    eave_above_m over the floor within eave_m of it (eaves, gutters), so the ground stays."""
    from shapely import contains_xy

    from services.api.twin.mesh import house_footprint

    inner, outer = house_footprint(twin, wall_m), house_footprint(twin, eave_m)
    floor = min(r.floor_y for r in twin.rooms)
    rows = open_ply(source)
    with open(source, "rb") as f:
        header = b""
        while not header.endswith(b"end_header\n"):
            header += f.readline()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()  # it may be a hard link to the download; never write through it
    body = target.with_suffix(".part")
    kept = 0
    with open(body, "wb") as out:
        for start in range(0, len(rows), CHUNK):
            block = np.asarray(rows[start:start + CHUNK])
            p = _apply(to_interior, block[:, :3].astype(np.float64))
            house = contains_xy(inner, p[:, 0], p[:, 2])
            house |= contains_xy(outer, p[:, 0], p[:, 2]) & (p[:, 1] > floor + eave_above_m)
            out.write(np.ascontiguousarray(block[~house]).tobytes())
            kept += int((~house).sum())
    lines = header.decode("ascii").split("\n")
    lines = [f"element vertex {kept}" if line.startswith("element vertex") else line for line in lines]
    with open(target, "wb") as out, open(body, "rb") as src:
        out.write("\n".join(lines).encode("ascii"))
        while block := src.read(64 << 20):
            out.write(block)
    body.unlink()
    return kept


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    parser.add_argument("--matterport-model-id", help="defaults to the twin's")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    report = publish(args.property_id, args.matterport_model_id)
    print(f"wrote {splat_path(args.property_id)} and {transform_path(args.property_id).name}: {report}")


if __name__ == "__main__":
    main()
