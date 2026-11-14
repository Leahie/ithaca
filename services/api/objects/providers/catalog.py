"""OBJECT_PROVIDER=catalog: free, ready-made furniture models stand in for generated ones.

For each furniture class it uses one CC0 model from Poly Haven (https://polyhaven.com, public
domain, no attribution required). The pipeline then sizes it to the dimensions read from the
user's image. The result looks like real furniture but is a generic piece, not the one in the
photo. A model is downloaded the first time its class is needed and cached under data/catalog/.
Classes without a model, or a failed download, fall back to a plain box.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import trimesh

from services.api.integrations.config import REPO_ROOT, env
from services.api.objects.providers.base import Piece
from services.api.objects.providers.stub import BoxTo3D

log = logging.getLogger(__name__)

# furniture class -> Poly Haven asset id
MODELS = {
    "sofa": "sofa_03",
    "loveseat": "sofa_02",
    "sectional": "sofa_03",
    "armchair": "modern_arm_chair_01",
    "chair": "dining_chair_02",
    "stool": "wooden_stool_02",
    "ottoman": "Ottoman_01",
    "bench": "painted_wooden_bench",
    "coffee_table": "modern_coffee_table_01",
    "side_table": "side_table_01",
    "dining_table": "dining_table",
    "desk": "metal_office_desk",
    "console_table": "ClassicConsole_01",
    "tv_stand": "modern_wooden_cabinet",
    "bookshelf": "wooden_bookshelf_worn",
    "cabinet": "painted_wooden_cabinet",
    "dresser": "vintage_wooden_drawer_01",
    "wardrobe": "GothicCabinet_01",
    "nightstand": "ClassicNightstand_01",
    "bed": "GothicBed_01",
    "plant": "potted_plant_04",
}
API = "https://api.polyhaven.com/files/"
HEADERS = {"User-Agent": "furniture-catalog-fetch/0.1"}


def catalog_dir() -> Path:
    path = Path(env("CATALOG_DIR") or REPO_ROOT / "data" / "catalog")
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch(asset_id: str) -> bytes:
    """One Poly Haven model as a single GLB (1k textures). Cached on disk."""
    cached = catalog_dir() / f"{asset_id}.glb"
    if cached.exists():
        return cached.read_bytes()
    with httpx.Client(timeout=60, headers=HEADERS, follow_redirects=True) as http:
        listing = http.get(API + asset_id).raise_for_status().json()["gltf"]["1k"]["gltf"]
        folder = catalog_dir() / "_src" / asset_id
        files = {f"{asset_id}.gltf": listing["url"], **{rel: f["url"] for rel, f in listing.get("include", {}).items()}}
        for rel, url in files.items():
            target = (folder / rel).resolve()
            if folder.resolve() not in target.parents:
                raise ValueError(f"unexpected file path in the model listing: {rel}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(http.get(url).raise_for_status().content)
    scene = trimesh.load(folder / f"{asset_id}.gltf", force="scene")
    glb = scene.export(file_type="glb")
    cached.write_bytes(glb)
    return glb


class CatalogTo3D:
    def generate(self, cutout_png: bytes, piece: Piece) -> bytes:
        asset_id = MODELS.get(piece.item_class)
        if asset_id:
            try:
                return fetch(asset_id)
            except Exception as e:  # offline, or the asset moved: a box is better than nothing
                log.warning("catalog model %s unavailable (%s); using a box", asset_id, e)
        return BoxTo3D().generate(cutout_png, piece)


if __name__ == "__main__":  # python -m services.api.objects.providers.catalog : download everything now
    for item_class, asset in MODELS.items():
        try:
            print(f"{item_class:14} {asset:28} {len(fetch(asset)) / 1e6:.1f} MB")
        except Exception as e:
            print(f"{item_class:14} {asset:28} FAILED: {e}")
