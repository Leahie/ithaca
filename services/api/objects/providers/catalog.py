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
from services.api.objects.providers.base import Model3D, Piece, Segment
from services.api.objects.providers.stub import BoxTo3D

log = logging.getLogger(__name__)

# (Poly Haven asset id, furniture class, label, width, depth, height in meters as modelled).
# Sizes were measured from the downloaded models. Assets whose scale is plainly wrong were left out.
PIECES: list[tuple[str, str, str, float, float, float]] = [
    ("sofa_03", "sofa", "Three-seat sofa, rolled arms", 2.73, 0.93, 1.12),
    ("painted_wooden_sofa", "sofa", "Wooden-frame sofa", 2.45, 0.79, 1.28),
    ("Sofa_01", "sofa", "Compact sofa", 1.57, 0.66, 0.80),
    ("sofa_02", "loveseat", "Loveseat", 1.81, 0.82, 0.71),
    ("modern_arm_chair_01", "armchair", "Modern armchair", 0.82, 0.99, 1.02),
    ("ArmChair_01", "armchair", "Upholstered armchair", 0.85, 0.77, 1.07),
    ("mid_century_lounge_chair", "armchair", "Mid-century lounge chair", 1.01, 1.19, 1.17),
    ("GreenChair_01", "armchair", "Green accent chair", 0.67, 0.66, 1.06),
    ("Rockingchair_01", "armchair", "Rocking chair", 0.71, 0.83, 0.99),
    ("chinese_armchair", "armchair", "Carved high-back armchair", 0.85, 0.79, 1.59),
    ("dining_chair_02", "chair", "Dining chair", 0.43, 0.58, 0.97),
    ("painted_wooden_chair_01", "chair", "Painted wooden chair", 0.43, 0.54, 0.96),
    ("SchoolChair_01", "chair", "School chair", 0.57, 0.68, 1.01),
    ("gallinera_chair", "chair", "Woven patio chair", 0.58, 0.60, 1.03),
    ("bar_chair_round_01", "stool", "Round bar stool", 0.48, 0.49, 0.75),
    ("metal_stool_01", "stool", "Metal stool", 0.35, 0.35, 0.88),
    ("Ottoman_01", "ottoman", "Ottoman", 0.88, 0.62, 0.62),
    ("painted_wooden_bench", "bench", "Painted wooden bench", 1.16, 0.50, 0.89),
    ("modern_coffee_table_01", "coffee_table", "Modern coffee table", 0.60, 1.20, 0.39),
    ("modern_coffee_table_02", "coffee_table", "Square coffee table", 1.20, 1.20, 0.37),
    ("coffee_table_round_01", "coffee_table", "Round coffee table", 1.30, 1.30, 0.49),
    ("CoffeeTable_01", "coffee_table", "Large wooden coffee table", 1.54, 0.97, 0.52),
    ("industrial_coffee_table", "coffee_table", "Industrial coffee table", 0.78, 0.76, 0.64),
    ("gothic_coffee_table", "coffee_table", "Carved coffee table", 1.44, 1.44, 0.56),
    ("side_table_01", "side_table", "Side table", 0.55, 0.45, 0.55),
    ("side_table_tall_01", "side_table", "Tall side table", 0.38, 0.38, 0.76),
    ("small_wooden_table_01", "side_table", "Small wooden table", 0.92, 0.44, 0.53),
    ("gallinera_table", "side_table", "Woven patio table", 0.83, 0.52, 0.49),
    ("dining_table", "dining_table", "Dining table for eight", 2.26, 1.39, 0.88),
    ("round_wooden_table_01", "dining_table", "Round wooden table, tall", 1.40, 1.40, 1.01),
    ("round_wooden_table_02", "dining_table", "Round table for two", 0.80, 0.80, 0.75),
    ("metal_office_desk", "desk", "Metal office desk", 2.00, 0.95, 0.79),
    ("SchoolDesk_01", "desk", "Small writing desk", 0.71, 0.55, 0.88),
    ("ClassicConsole_01", "console_table", "Classic console table", 1.54, 0.59, 0.95),
    ("chinese_console_table", "console_table", "Slim console table", 1.72, 0.34, 0.66),
    ("modern_wooden_cabinet", "tv_stand", "Low media cabinet", 2.44, 0.52, 0.68),
    ("painted_wooden_cabinet", "cabinet", "Painted wooden cabinet", 1.19, 0.62, 1.18),
    ("painted_wooden_cabinet_02", "cabinet", "Tall painted cabinet", 1.00, 0.72, 2.57),
    ("vintage_cabinet_01", "cabinet", "Vintage display cabinet", 2.02, 0.67, 2.24),
    ("wooden_bookshelf_worn", "bookshelf", "Wooden bookshelf", 1.37, 0.58, 2.06),
    ("painted_wooden_shelves", "bookshelf", "Small painted shelves", 0.51, 0.37, 1.13),
    ("wooden_display_shelves_01", "bookshelf", "Display shelves", 0.37, 1.08, 1.56),
    ("vintage_wooden_drawer_01", "dresser", "Vintage chest of drawers", 1.72, 1.01, 1.09),
    ("drawer_cabinet", "dresser", "Tall drawer cabinet", 1.14, 0.49, 1.88),
    ("GothicCommode_01", "dresser", "Carved commode", 1.20, 0.58, 1.21),
    ("GothicCabinet_01", "wardrobe", "Carved wardrobe", 1.72, 1.12, 2.36),
    ("ClassicNightstand_01", "nightstand", "Classic nightstand", 0.57, 0.42, 0.70),
    ("painted_wooden_nightstand", "nightstand", "Painted nightstand", 0.50, 0.51, 0.62),
    ("GothicBed_01", "bed", "Carved double bed", 1.49, 2.04, 1.53),
    ("vintage_day_bed", "bed", "Day bed", 1.97, 0.85, 1.13),
    ("potted_plant_02", "plant", "Potted plant", 0.70, 0.66, 0.84),
]
BY_ID = {row[0]: row for row in PIECES}
SOURCE = "Poly Haven (polyhaven.com), CC0 public domain"

# furniture class -> the default model for that class (the first one listed). "sectional" borrows the sofa.
MODELS: dict[str, str] = {}
for _id, _cls, *_ in PIECES:
    MODELS.setdefault(_cls, _id)
MODELS.setdefault("sectional", "sofa_03")


def thumbnail_url(asset_id: str) -> str:
    return f"https://cdn.polyhaven.com/asset_img/thumbs/{asset_id}.png?width=256&height=256"


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
    def generate(self, image: bytes, segment: Segment, piece: Piece) -> Model3D:
        asset_id = MODELS.get(piece.item_class)
        if asset_id:
            try:
                return Model3D(fetch(asset_id))
            except Exception as e:  # offline, or the asset moved: a box is better than nothing
                log.warning("catalog model %s unavailable (%s); using a box", asset_id, e)
        return BoxTo3D().generate(image, segment, piece)


if __name__ == "__main__":  # python -m services.api.objects.providers.catalog : download everything now
    for item_class, asset in MODELS.items():
        try:
            print(f"{item_class:14} {asset:28} {len(fetch(asset)) / 1e6:.1f} MB")
        except Exception as e:
            print(f"{item_class:14} {asset:28} FAILED: {e}")
