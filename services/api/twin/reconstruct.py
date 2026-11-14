"""Surface reconstruction from the MatterPak point cloud (screened Poisson, Kazhdan & Hoppe 2013).

Matterport's own OBJ is a low-poly dollhouse proxy: flat panels with the photos stretched over
them. The point cloud keeps the real shape (tub rims, sills, door casings), so the mesh is rebuilt
from it: normals from local planes, flipped to face the scan position that saw each point,
Poisson, then the low-density skin Poisson invents over unseen space is trimmed away.

    python -m services.api.twin.reconstruct room onXQLXhfyQv <room_id>   # proof: one room
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import open3d as o3d
import trimesh
from scipy.spatial import cKDTree

from services.api.integrations.config import REPO_ROOT
from services.api.twin import service
from services.api.twin.cloud import load_cloud, thin
from services.api.twin.mesh import MODEL_TO_INTERIOR, download_matterpak_mesh

log = logging.getLogger(__name__)

COMPARE_DIR = REPO_ROOT / "out" / "compare"
VOXEL_M = 0.005


def crop(points: np.ndarray, colors: np.ndarray, lo, hi) -> tuple[np.ndarray, np.ndarray]:
    inside = np.all((points >= lo) & (points <= hi), axis=1)
    return points[inside], colors[inside]


def oriented_cloud(points: np.ndarray, colors: np.ndarray, viewpoints: np.ndarray,
                   radius_m: float = 0.04, max_nn: int = 30) -> o3d.geometry.PointCloud:
    """Normals from local plane fits, each flipped toward its nearest scan position."""
    pcd = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points.astype(np.float64)))
    pcd.colors = o3d.utility.Vector3dVector(colors.astype(np.float64) / 255.0)
    pcd.estimate_normals(o3d.geometry.KDTreeSearchParamHybrid(radius=radius_m, max_nn=max_nn))
    normals = np.asarray(pcd.normals)
    _, nearest = cKDTree(viewpoints).query(points)
    facing = np.einsum("ij,ij->i", normals, viewpoints[nearest] - points)
    normals[facing < 0] *= -1
    pcd.normals = o3d.utility.Vector3dVector(normals)
    return pcd


def poisson(pcd: o3d.geometry.PointCloud, depth: int, trim_quantile: float = 0.03,
            max_edge_m: float = 0.05) -> o3d.geometry.TriangleMesh:
    mesh, density = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=depth, scale=1.05)
    density = np.asarray(density)
    mesh.remove_vertices_by_mask(density < np.quantile(density, trim_quantile))
    mesh = mesh.crop(pcd.get_axis_aligned_bounding_box())
    # Poisson also bridges gaps (open doorways, windows) with surfaces far from any point.
    vertices = np.asarray(mesh.vertices)
    gap, _ = cKDTree(np.asarray(pcd.points)).query(vertices, distance_upper_bound=max_edge_m)
    mesh.remove_vertices_by_mask(~np.isfinite(gap))
    mesh.remove_degenerate_triangles()
    mesh.remove_unreferenced_vertices()
    return mesh


def to_trimesh(mesh: o3d.geometry.TriangleMesh) -> trimesh.Trimesh:
    mesh.compute_vertex_normals()
    colors = (np.clip(np.asarray(mesh.vertex_colors), 0, 1) * 255).astype(np.uint8)
    return trimesh.Trimesh(np.asarray(mesh.vertices), np.asarray(mesh.triangles),
                           vertex_normals=np.asarray(mesh.vertex_normals), vertex_colors=colors, process=False)


def original_crop(model_id: str, lo, hi) -> trimesh.Scene:
    """The current MatterPak-OBJ mesh, cut to the same box, for comparison."""
    scene = trimesh.load(download_matterpak_mesh(model_id), force="scene", process=False)
    scene.apply_transform(MODEL_TO_INTERIOR)
    out = trimesh.Scene()
    for name, geometry in _baked(scene):
        centers = geometry.triangles_center
        keep = np.all((centers >= lo) & (centers <= hi), axis=1)
        if keep.any():
            out.add_geometry(geometry.submesh([np.flatnonzero(keep)], append=True), geom_name=name)
    return out


def _baked(scene: trimesh.Scene):
    for node in scene.graph.nodes_geometry:
        transform, name = scene.graph[node]
        yield name, scene.geometry[name].copy().apply_transform(transform)


def room_proof(property_id: str, room_id: str, depth: int = 10, margin_m: float = 0.35,
               target_triangles: int = 150_000) -> dict:
    twin = service.get_twin(property_id)
    model_id = twin.sources.matterport_model_id
    room = next(r for r in twin.rooms if r.room_id == room_id)
    xz = np.array(room.polygon)
    lo = np.array([xz[:, 0].min() - margin_m, room.floor_y - 0.15, xz[:, 1].min() - margin_m])
    hi = np.array([xz[:, 0].max() + margin_m, room.floor_y + room.height_m + 0.25, xz[:, 1].max() + margin_m])
    viewpoints = np.array([[s.position.x, s.position.y, s.position.z] for s in twin.sweeps
                           if s.floor_id == room.floor_id])

    points, colors = thin(*crop(*load_cloud(model_id), lo, hi), VOXEL_M)
    log.info("room %s: %d points in %s .. %s", room.label, len(points), lo.round(2), hi.round(2))
    pcd = oriented_cloud(points, colors, viewpoints)
    mesh = poisson(pcd, depth)
    full = len(mesh.triangles)
    if full > target_triangles:
        mesh = mesh.simplify_quadric_decimation(target_triangles)

    out_dir = COMPARE_DIR / room_id
    out_dir.mkdir(parents=True, exist_ok=True)
    to_trimesh(mesh).export(out_dir / "poisson.glb")
    original = original_crop(model_id, lo, hi)
    original.export(out_dir / "original.glb")
    return {"room": room.label, "points": len(points), "poisson_triangles": full,
            "kept_triangles": len(mesh.triangles),
            "original_triangles": int(sum(len(g.faces) for g in original.geometry.values())),
            "out": str(out_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    room = sub.add_parser("room")
    room.add_argument("property_id")
    room.add_argument("room_id")
    room.add_argument("--depth", type=int, default=10)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(room_proof(args.property_id, args.room_id, args.depth))


if __name__ == "__main__":
    main()
