"""Interior mesh: MatterPak OBJ -> interior.glb (glTF binary, meters, Y up: I-G15; <= 25 MB: C9).

The MatterPak OBJ is in the Model API frame (Z up), the same frame as the wall graph and panos.
Only the OBJ, its .mtl and its texture atlases are pulled out of the zip; the point cloud stays
on the server.

    python -m services.api.twin.mesh onXQLXhfyQv [--max-texture 1024]
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

from services.api.integrations.config import REPO_ROOT, fixtures_dir
from services.api.integrations.http_zip import HttpRangeFile, list_members, read_member
from services.api.integrations.matterport import MatterportClient
from services.api.twin import frames

log = logging.getLogger(__name__)

RAW_DIR = REPO_ROOT / "data" / "matterport"  # gitignored: the raw downloads are large
MAX_GLB_BYTES = 25 * 1024 * 1024
DOWNLOAD_THREADS = 8

MODEL_TO_INTERIOR = np.eye(4)
MODEL_TO_INTERIOR[:3, :3] = frames.INTERIOR_TO_MODEL.T


def interior_glb_path(property_id: str) -> Path:
    return fixtures_dir() / "blobs" / property_id / "interior.glb"


def download_matterpak_mesh(model_id: str, client: MatterportClient | None = None) -> Path:
    """The MatterPak's OBJ (with .mtl and textures beside it), downloaded once and kept."""
    dest = RAW_DIR / model_id / "matterpak"
    marker = dest / "complete.json"
    if marker.exists():
        return dest / json.loads(marker.read_text(encoding="utf-8"))["obj"]

    remote = HttpRangeFile((client or MatterportClient()).matterpak_url(model_id))
    members = list_members(remote)
    obj = next((m for m in members if m.filename.lower().endswith(".obj")), None)
    if obj is None:
        raise RuntimeError(f"MatterPak for {model_id} has no OBJ")
    stem = Path(obj.filename).stem
    wanted = [m for m in members if Path(m.filename).stem == stem or Path(m.filename).name.startswith(stem + "_")]
    dest.mkdir(parents=True, exist_ok=True)

    def fetch(info):
        (dest / Path(info.filename).name).write_bytes(read_member(remote, info))

    with ThreadPoolExecutor(DOWNLOAD_THREADS) as pool:
        list(pool.map(fetch, wanted))
    marker.write_text(json.dumps({"obj": Path(obj.filename).name, "files": len(wanted)}), encoding="utf-8")
    log.info("downloaded %d MatterPak files (%.1f MB) to %s", len(wanted), remote.bytes_read / 1e6, dest)
    return dest / Path(obj.filename).name


def build_interior_glb(obj_path: Path, out_path: Path, max_texture_px: int = 1024) -> dict:
    scene = trimesh.load(obj_path, force="scene", process=False)
    textures = 0
    for geometry in scene.geometry.values():
        material = getattr(geometry.visual, "material", None)
        image = getattr(material, "image", None)
        if image is not None:
            material.image = _shrink(image, max_texture_px)
            textures += 1
    scene.apply_transform(MODEL_TO_INTERIOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(scene.export(file_type="glb"))
    compressed = False
    if out_path.stat().st_size > MAX_GLB_BYTES:
        _meshopt_compress(out_path)
        compressed = True

    lo, hi = scene.bounds
    return {
        "triangles": int(sum(len(g.faces) for g in scene.geometry.values())),
        "textures": textures,
        "bytes": out_path.stat().st_size,
        "meshopt": compressed,
        "bounds_min": [round(float(v), 2) for v in lo],
        "bounds_max": [round(float(v), 2) for v in hi],
    }


def _shrink(image: Image.Image, max_px: int) -> Image.Image:
    image = image.convert("RGB")
    image.thumbnail((max_px, max_px), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    buffer.seek(0)
    return Image.open(buffer)  # format == "JPEG", so the glTF exporter keeps it as JPEG


def _meshopt_compress(path: Path) -> None:
    """Quantize and meshopt-compress geometry (EXT_meshopt_compression; three.js: MeshoptDecoder)."""
    npx = shutil.which("npx")
    if npx is None:
        raise RuntimeError(f"{path} is over budget and npx (for gltfpack) is not installed")
    with tempfile.TemporaryDirectory() as tmp:
        packed = Path(tmp) / "packed.glb"
        subprocess.run([npx, "-y", "gltfpack", "-i", str(path), "-o", str(packed), "-cc", "-kn", "-km"],
                       check=True, capture_output=True)
        shutil.move(packed, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    parser.add_argument("--matterport-model-id", help="defaults to property_id")
    parser.add_argument("--max-texture", type=int, default=1024, help="longest texture side in pixels")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    obj = download_matterpak_mesh(args.matterport_model_id or args.property_id)
    out = interior_glb_path(args.property_id)
    stats = build_interior_glb(obj, out, args.max_texture)
    print(f"wrote {out} ({stats['bytes'] / 1e6:.1f} MB)")
    print(f"  {stats['triangles']:,} triangles, {stats['textures']} textures, meshopt: {stats['meshopt']}")
    print(f"  interior bounds {stats['bounds_min']} .. {stats['bounds_max']} (x, y up, z)")
    if stats["bytes"] > MAX_GLB_BYTES:
        print(f"  over the {MAX_GLB_BYTES / 1e6:.0f} MB budget; rerun with a smaller --max-texture")


if __name__ == "__main__":
    main()
