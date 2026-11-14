"""Side-by-side check of room meshes: shaded renders from a scan position, and numbers.

    python compare_meshes.py <data dir of gs_export --fuse> <view name> name=mesh.glb name=mesh.ply ...
                             [crop=u0,v0,u1,v1 ...]   (parts of the face, 0..1; default the whole face)

Writes <data>/compare_<view>_<k>.jpg per crop: the photo, then each mesh ray-cast from the same camera
and shaded by its surface normal only (no texture, so the geometry is what shows). Prints, per mesh: triangle
count, distance from the cloud to the mesh (accuracy and completeness), and how rough the mesh is
where the room's flat cloud patches are (smoothness).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import open3d as o3d
import trimesh
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).parent))
from fuse_depth import read_views  # noqa: E402

RENDER_PX = 1024


def load(path: Path) -> o3d.geometry.TriangleMesh:
    mesh = trimesh.load(path, force="mesh", process=False)
    return o3d.geometry.TriangleMesh(o3d.utility.Vector3dVector(np.asarray(mesh.vertices, np.float64)),
                                     o3d.utility.Vector3iVector(np.asarray(mesh.faces, np.int32)))


def scene_of(mesh: o3d.geometry.TriangleMesh) -> o3d.t.geometry.RaycastingScene:
    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(mesh))
    return scene


def render(scene, view: dict, n: int, crop: tuple[float, float, float, float]) -> np.ndarray:
    """Normal-shaded image of the crop (u0, v0, u1, v1 in 0..1 of the face) at n pixels wide."""
    u0, v0, u1, v1 = crop
    h = round(n * (v1 - v0) / (u1 - u0))
    u = (u0 + (np.arange(n) + 0.5) / n * (u1 - u0)) * 2 - 1
    v = (v0 + (np.arange(h) + 0.5) / h * (v1 - v0)) * 2 - 1
    uu, vv = np.meshgrid(u, v)
    d_cam = np.stack([uu, vv, np.ones_like(uu)], axis=-1)
    d_cam /= np.linalg.norm(d_cam, axis=-1, keepdims=True)
    d = d_cam @ view["r"]  # camera -> world is r transposed; row vectors
    rays = np.concatenate([np.broadcast_to(view["center"], d.shape), d], axis=-1).astype(np.float32)
    hit = scene.cast_rays(o3d.core.Tensor(rays))
    normal = hit["primitive_normals"].numpy()
    t = hit["t_hit"].numpy()
    facing = np.abs(np.einsum("hwk,hwk->hw", normal, d))
    light = d_cam @ view["r"] + 0.6 * np.array([0.3, 1.0, 0.2])  # headlight plus a little from above
    light /= np.linalg.norm(light, axis=-1, keepdims=True)
    flip = np.sign(-np.einsum("hwk,hwk->hw", normal, d))[..., None]
    shade = np.clip(np.einsum("hwk,hwk->hw", normal * flip, -light), 0, 1)
    img = 40 + 200 * (0.25 * facing + 0.75 * shade)
    img[~np.isfinite(t)] = 0
    return np.repeat(img.astype(np.uint8)[..., None], 3, axis=2)


def numbers(mesh: o3d.geometry.TriangleMesh, scene, cloud: np.ndarray, flat: np.ndarray) -> dict:
    d = scene.compute_distance(o3d.core.Tensor(cloud.astype(np.float32))).numpy()
    tris = np.asarray(mesh.triangles)
    verts = np.asarray(mesh.vertices)
    # Roughness: mesh vertices near each flat cloud patch, rms about that patch's own plane.
    tree = cKDTree(verts)
    rough = []
    for center, normal in flat:
        near = tree.query_ball_point(center, 0.06)
        if len(near) >= 12:
            off = (verts[near] - center) @ normal
            rough.append(np.std(off))
    return {"triangles": int(len(tris)),
            "cloud_to_mesh_median_mm": round(1000 * float(np.median(d)), 2),
            "cloud_to_mesh_p90_mm": round(1000 * float(np.quantile(d, 0.9)), 2),
            "cloud_within_1cm": round(float((d < 0.01).mean()), 3),
            "flat_patch_roughness_mm": round(1000 * float(np.median(rough)), 2) if rough else None,
            "flat_patches": len(rough)}


def flat_patches(cloud: np.ndarray, count: int = 400, radius: float = 0.06) -> np.ndarray:
    """(center, normal) of cloud neighborhoods that are planes to within 1 mm: walls, floor, table tops."""
    rng = np.random.default_rng(0)
    tree = cKDTree(cloud)
    out = []
    for i in rng.choice(len(cloud), count * 8, replace=False):
        near = cloud[tree.query_ball_point(cloud[i], radius)]
        if len(near) < 60:
            continue
        c = near.mean(axis=0)
        w, vec = np.linalg.eigh(np.cov((near - c).T))
        if np.sqrt(w[0]) < 0.001 and w[1] > (radius / 4) ** 2:  # thin, and wide in both other directions
            out.append((c, vec[:, 0]))
        if len(out) == count:
            break
    return out


def main() -> None:
    data, view_name = Path(sys.argv[1]), sys.argv[2]
    crops = [tuple(float(x) for x in a.split("=")[1].split(",")) for a in sys.argv[3:] if a.startswith("crop=")]
    meshes = [a.split("=", 1) for a in sys.argv[3:] if not a.startswith("crop=")]
    crops = crops or [(0, 0, 1, 1)]
    view = next(v for v in read_views(data) if Path(v["name"]).stem == view_name)
    volume = json.loads((data / "volume.json").read_text())
    cloud = np.load(data / "cloud.npz")["points"].astype(np.float64)
    inside = np.all((cloud > np.array(volume["lo"]) + 0.25) & (cloud < np.array(volume["hi"]) - 0.25), axis=1)
    cloud = cloud[inside]
    flat = flat_patches(cloud)
    sample = cloud[np.random.default_rng(1).choice(len(cloud), 300_000, replace=False)]
    photo = Image.open(data / "images" / view["name"]).convert("RGB")
    rows = [[] for _ in crops]
    for k, (u0, v0, u1, v1) in enumerate(crops):
        n = photo.size[0]
        tile = photo.crop((round(u0 * n), round(v0 * n), round(u1 * n), round(v1 * n)))
        rows[k].append(np.asarray(tile.resize((RENDER_PX, round(RENDER_PX * (v1 - v0) / (u1 - u0))), Image.LANCZOS)))
    report = {}
    for name, path in meshes:
        mesh = load(Path(path))
        scene = scene_of(mesh)
        report[name] = numbers(mesh, scene, sample, flat)
        print(name, json.dumps(report[name]))
        for k, crop in enumerate(crops):
            img = Image.fromarray(render(scene, view, RENDER_PX, crop))
            ImageDraw.Draw(img).text((12, 10), name, fill=(255, 255, 0))
            rows[k].append(np.asarray(img))
    for k, row in enumerate(rows):
        Image.fromarray(np.concatenate(row, axis=1)).save(data / f"compare_{view_name}_{k}.jpg", quality=88)
    (data / f"compare_{view_name}.json").write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
