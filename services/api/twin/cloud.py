"""The MatterPak point cloud (cloud.xyz: x y z r g b per line, model frame, about 46 M points).

Streamed out of the remote zip, rotated into the interior frame and thinned on a voxel grid, so
the 1.9 GB text file never touches the disk. Build-time only (needs the Matterport keys once).

    python -m services.api.twin.cloud onXQLXhfyQv [--voxel 0.005]
"""

from __future__ import annotations

import argparse
import io
import logging
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from services.api.integrations import http_zip
from services.api.integrations.matterport import MatterportClient
from services.api.twin.mesh import MODEL_TO_INTERIOR, RAW_DIR

log = logging.getLogger(__name__)

CLOUD_MEMBER = "cloud.xyz"
PARSE_BYTES = 64 << 20


def cloud_path(model_id: str, voxel_m: float) -> Path:
    return RAW_DIR / model_id / f"cloud_{round(voxel_m * 1000)}mm.npz"


def load_cloud(model_id: str, voxel_m: float = 0.005) -> tuple[np.ndarray, np.ndarray]:
    """(points (N, 3) float32 in the interior frame, colors (N, 3) uint8)."""
    path = cloud_path(model_id, voxel_m)
    if not path.exists():
        extract_cloud(model_id, voxel_m)
    data = np.load(path)
    return data["points"], data["colors"]


def iter_points(model_id: str, chunk: int = 2_000_000, voxel_m: float = 0.005):
    """The cloud's points in (chunk, 3) blocks, read straight from the npz without loading it whole."""
    path = cloud_path(model_id, voxel_m)
    if not path.exists():
        extract_cloud(model_id, voxel_m)
    with zipfile.ZipFile(path) as z, z.open("points.npy") as f:
        version = np.lib.format.read_magic(f)
        shape, fortran, dtype = np.lib.format._read_array_header(f, version)
        if fortran or len(shape) != 2:
            raise ValueError(f"unexpected points layout in {path}")
        row = shape[1] * dtype.itemsize
        left = shape[0]
        while left:
            n = min(chunk, left)
            yield np.frombuffer(f.read(n * row), dtype=dtype).reshape(n, shape[1])
            left -= n


def thin(points: np.ndarray, colors: np.ndarray, voxel_m: float) -> tuple[np.ndarray, np.ndarray]:
    """One point per voxel (the first one seen)."""
    keys = np.zeros(len(points), np.int64)
    for axis, shift in ((0, 42), (1, 21), (2, 0)):
        keys |= (np.floor(points[:, axis] / voxel_m).astype(np.int64) + (1 << 20)) << shift
    _, first = np.unique(keys, return_index=True)
    return points[first], colors[first]


def extract_cloud(model_id: str, voxel_m: float = 0.005, client: MatterportClient | None = None) -> Path:
    client = client or MatterportClient()
    remote = http_zip.HttpRangeFile(client.matterpak_url(model_id))
    info = next(i for i in http_zip.list_members(remote) if Path(i.filename).name == CLOUD_MEMBER)
    rotation = MODEL_TO_INTERIOR[:3, :3]
    kept_p, kept_c = [], []
    pending = b""
    parsed = 0
    started = time.time()

    def parse(blob: bytes) -> None:
        nonlocal parsed
        table = pd.read_csv(io.BytesIO(blob), sep=" ", header=None, engine="c",
                            dtype={0: np.float64, 1: np.float64, 2: np.float64, 3: np.uint8, 4: np.uint8, 5: np.uint8})
        values = table.to_numpy()
        p = (values[:, :3] @ rotation.T).astype(np.float32)
        c = values[:, 3:6].astype(np.uint8)
        parsed += len(table)
        del table, values
        p, c = thin(p, c, voxel_m)
        kept_p.append(p)
        kept_c.append(c)

    for data in http_zip.iter_member(remote, info):
        pending += data
        if len(pending) >= PARSE_BYTES:
            cut = pending.rfind(b"\n") + 1
            parse(pending[:cut])
            pending = pending[cut:]
            log.info("cloud: %.0f%% (%.1f M points read, %.1f M kept, %.0f s)",
                     100 * remote.bytes_read / info.compress_size, parsed / 1e6,
                     sum(len(p) for p in kept_p) / 1e6, time.time() - started)
    if pending.strip():
        parse(pending)

    # Thinned per chunk only: overlap between scans survives until a crop is thinned (cheap there).
    points, colors = np.concatenate(kept_p), np.concatenate(kept_c)
    del kept_p[:], kept_c[:]
    out = cloud_path(model_id, voxel_m)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out, points=points, colors=colors)
    log.info("cloud: %.1f M points -> %.1f M, %s", parsed / 1e6, len(points) / 1e6, out)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model_id")
    parser.add_argument("--voxel", type=float, default=0.005, help="thinning voxel size in meters")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    extract_cloud(args.model_id, args.voxel)


if __name__ == "__main__":
    main()
