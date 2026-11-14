"""Interior mesh: MatterPak OBJ -> interior.glb (glTF binary, meters, Y up: I-G15).

The MatterPak OBJ is in the Model API frame (Z up), the same frame as the wall graph and panos.
Only the OBJ, its .mtl and its texture atlases are pulled out of the zip; the point cloud stays
on the server.

Full resolution by default: the texture atlases (2048 px) go into the GLB byte for byte, and the
geometry is not quantized. About 80 MB for onXQLXhfyQv. --max-texture shrinks them if needed.

    python -m services.api.twin.mesh onXQLXhfyQv [--max-texture 1024] [--keep-yard]
"""

from __future__ import annotations

import argparse
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from shapely import contains_xy
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from services.api.integrations.config import REPO_ROOT, fixtures_dir
from services.api.integrations.http_zip import HttpRangeFile, list_members, read_member
from services.api.integrations.matterport import MatterportClient
from services.api.twin import frames

log = logging.getLogger(__name__)

RAW_DIR = REPO_ROOT / "data" / "matterport"  # gitignored: the raw downloads are large
DOWNLOAD_THREADS = 8
WELD_M = 1e-4
MIN_PIECE_FACES = 200
FOOTPRINT_MARGIN_M = 0.5

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


def build_interior_glb(obj_path: Path, out_path: Path, max_texture_px: int | None = None,
                       footprint: BaseGeometry | None = None, min_piece_faces: int = MIN_PIECE_FACES) -> dict:
    """footprint: the house outline in the interior (x, z) plane; scan geometry outside it (the
    yard, with daylight baked into its photos) is dropped. max_texture_px None keeps the original
    texture files unchanged."""
    scene = trimesh.load(obj_path, force="scene", process=False)
    for geometry in scene.geometry.values():
        geometry.apply_transform(MODEL_TO_INTERIOR)
    before = int(sum(len(g.faces) for g in scene.geometry.values()))
    _drop_faces(scene, footprint, min_piece_faces)
    _smooth_normals(scene)

    originals = _texture_files(obj_path)
    textures = 0
    for geometry in scene.geometry.values():
        material = getattr(geometry.visual, "material", None)
        image = getattr(material, "image", None)
        if image is None:
            continue
        source = originals.get(getattr(material, "name", None) or "")
        if max_texture_px is None and source is not None:
            material.image = _as_is(source)
        else:
            material.image = _shrink(image, max_texture_px or max(image.size))
        textures += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(scene.export(file_type="glb", include_normals=True))

    lo, hi = scene.bounds
    triangles = int(sum(len(g.faces) for g in scene.geometry.values()))
    return {
        "triangles": triangles,
        "dropped": before - triangles,
        "textures": textures,
        "bytes": out_path.stat().st_size,
        "bounds_min": [round(float(v), 2) for v in lo],
        "bounds_max": [round(float(v), 2) for v in hi],
    }


def house_footprint(twin, margin_m: float = FOOTPRINT_MARGIN_M) -> BaseGeometry:
    """Union of the room and floor-opening polygons, grown to take in the walls and sills."""
    shapes = [Polygon(r.polygon) for r in twin.rooms]
    shapes += [Polygon(o.polygon) for o in twin.floor_openings if getattr(o, "polygon", None)]
    return unary_union([s.buffer(0) for s in shapes]).buffer(margin_m)


def _welded(scene: trimesh.Scene) -> tuple[list[trimesh.Trimesh], np.ndarray, np.ndarray]:
    """Scene meshes, their faces as indices into one position-welded vertex list, and the per-mesh
    vertex offsets. The OBJ is split into one chunk per texture atlas, with vertices duplicated
    along every chunk and UV seam."""
    meshes = list(scene.geometry.values())
    offsets = np.cumsum([0] + [len(m.vertices) for m in meshes])
    vertices = np.concatenate([m.vertices for m in meshes])
    _, weld = np.unique(np.round(vertices / WELD_M).astype(np.int64), axis=0, return_inverse=True)
    weld = weld.reshape(-1)
    faces = np.concatenate([m.faces + offsets[i] for i, m in enumerate(meshes)])
    return meshes, weld[faces], offsets


def _drop_faces(scene: trimesh.Scene, footprint: BaseGeometry | None, min_piece_faces: int) -> None:
    """Drop small disconnected pieces (scan floaters) and, given a footprint, everything outside it."""
    meshes, faces, _ = _welded(scene)
    edges = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]]])
    n = int(faces.max()) + 1
    graph = coo_matrix((np.ones(len(edges)), (edges[:, 0], edges[:, 1])), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    piece = labels[faces[:, 0]]
    keep = np.bincount(piece)[piece] >= min_piece_faces
    if footprint is not None:
        centers = np.concatenate([m.triangles_center for m in meshes])
        keep &= contains_xy(footprint, centers[:, 0], centers[:, 2])

    start = 0
    for name, mesh in list(scene.geometry.items()):
        mask = keep[start : start + len(mesh.faces)]
        start += len(mesh.faces)
        if not mask.any():
            scene.delete_geometry(name)
        elif not mask.all():
            mesh.update_faces(mask)
            mesh.remove_unreferenced_vertices()


def _smooth_normals(scene: trimesh.Scene) -> None:
    """Area-weighted vertex normals shared across chunk and UV seams. Without NORMAL, three.js
    shades every triangle flat and the scan's coarse facets show."""
    meshes, faces, offsets = _welded(scene)
    vertices = np.concatenate([m.vertices for m in meshes])
    corners = np.concatenate([m.faces + offsets[i] for i, m in enumerate(meshes)])
    tri = vertices[corners]
    face_normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    summed = np.zeros((int(faces.max()) + 1, 3))
    for k in range(3):
        np.add.at(summed, faces[:, k], face_normals)
    weld = np.zeros(len(vertices), dtype=np.int64)
    weld[corners.reshape(-1)] = faces.reshape(-1)
    normals = summed[weld]
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    for i, mesh in enumerate(meshes):
        mesh.vertex_normals = normals[offsets[i] : offsets[i + 1]]


def _texture_files(obj_path: Path) -> dict[str, Path]:
    """Material name -> its texture file (map_Kd), from the .mtl files beside the OBJ."""
    out: dict[str, Path] = {}
    for mtl in obj_path.parent.glob("*.mtl"):
        name = None
        for line in mtl.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.strip().partition(" ")
            if key == "newmtl":
                name = value.strip()
            elif key == "map_Kd" and name and (obj_path.parent / value.strip()).exists():
                out[name] = obj_path.parent / value.strip()
    return out


def _as_is(path: Path) -> Image.Image:
    """The texture file, embedded unchanged: trimesh's GLB export calls image.save() for each
    texture, which would re-encode the JPEG."""
    data = path.read_bytes()
    image = Image.open(io.BytesIO(data))
    image.save = lambda f, *args, **kwargs: f.write(data)
    return image


def _shrink(image: Image.Image, max_px: int) -> Image.Image:
    image = image.convert("RGB")
    image.thumbnail((max_px, max_px), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    buffer.seek(0)
    return Image.open(buffer)  # format == "JPEG", so the glTF exporter keeps it as JPEG


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    parser.add_argument("--matterport-model-id", help="defaults to property_id")
    parser.add_argument("--max-texture", type=int, default=None,
                        help="shrink textures to this longest side in pixels (default: keep the originals)")
    parser.add_argument("--keep-yard", action="store_true", help="keep scan geometry outside the house footprint")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from services.api.twin.service import get_twin  # service -> ingest -> mesh

    obj = download_matterpak_mesh(args.matterport_model_id or args.property_id)
    out = interior_glb_path(args.property_id)
    footprint = None if args.keep_yard else house_footprint(get_twin(args.property_id))
    stats = build_interior_glb(obj, out, args.max_texture, footprint)
    print(f"wrote {out} ({stats['bytes'] / 1e6:.1f} MB)")
    print(f"  {stats['triangles']:,} triangles ({stats['dropped']:,} dropped), {stats['textures']} textures")
    print(f"  interior bounds {stats['bounds_min']} .. {stats['bounds_max']} (x, y up, z)")


if __name__ == "__main__":
    main()
