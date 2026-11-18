"""Scan photos as a COLMAP-format dataset, for Gaussian-splat training (gsplat, nerfstudio).

Each sweep's 4k skybox is six 90-degree pinhole cameras (4096 px, focal 2048 px, principal point at
the center), all at the sweep's position. Poses come straight from the twin (Sweep.position and
Sweep.rotation, the pano frame -> interior frame), so no structure-from-motion is needed. The face
images are the GL cube faces of twin/panos.py; a face whose GL layout is mirrored is flipped so it
is a plain pinhole image. The MatterPak point cloud, cropped to the area, is the initial point set.

Everything is in the interior frame (Y up, meters), the same frame as interior.glb.

    python -m services.api.twin.gs_export onXQLXhfyQv --room <room_id> [--out out/gs/<name>]
    python -m services.api.twin.gs_export onXQLXhfyQv --all

Layout written (what gsplat's COLMAP parser reads):
    <out>/images/<sweep>_<face>.jpg        4096 px
    <out>/images_2/<sweep>_<face>.jpg      2048 px (for --data_factor 2)
    <out>/sparse/0/{cameras,images,points3D}.txt
    <out>/check.json                       pose check: photo vs point-cloud colors per face. It ignores
                                           occlusion, so ~0.15-0.3 is normal for good poses (checked
                                           Nov 17 by rendering the cloud from the cameras: it lines up)
    <out>/cloud.npz, volume.json           with --fuse: inputs of infra/recon/fuse_depth.py (see fuse_inputs)
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial.transform import Rotation
from shapely import contains_xy
from shapely.geometry import Polygon

from services.api.integrations.config import REPO_ROOT
from services.api.twin import panos

log = logging.getLogger(__name__)

FACES = panos.FACES
RES = "4k"
OUT_ROOT = REPO_ROOT / "out" / "gs"


def face_basis(face: str) -> tuple[np.ndarray, bool]:
    """3x3 M with pano-frame direction = M @ (x, y, 1) for camera pixel coordinates x right and
    y down in [-1, 1], and whether the face must be mirrored left-right to make M a rotation."""
    n = 2
    d = panos.gl_face_directions(face, n).reshape(-1, 3)
    s = (np.arange(n) + 0.5) / n * 2 - 1
    sc, tc = np.meshgrid(s, s)
    a = np.stack([sc.ravel(), tc.ravel(), np.ones(n * n)], axis=1)
    m = np.linalg.lstsq(a, d, rcond=None)[0].T
    flip = np.linalg.det(m) < 0
    if flip:
        m[:, 0] *= -1
    return m, bool(flip)


def camera_to_interior(sweep, face: str) -> tuple[np.ndarray, bool]:
    """Rotation taking camera coordinates (OpenCV: x right, y down, z forward) to the interior frame."""
    m, flip = face_basis(face)
    q = sweep.rotation
    r = Rotation.from_quat([q.x, q.y, q.z, q.w]).as_matrix()
    return r @ m, flip


def load_points(property_id: str, model_id: str) -> tuple[np.ndarray, np.ndarray]:
    """The whole point cloud in the interior frame, from the full-resolution export if present,
    else from twin/cloud.py's 5 mm cache."""
    from services.api.twin.mesh import RAW_DIR

    ply = RAW_DIR / model_id / "full_res" / "cloud_full.ply"
    if ply.exists():
        with open(ply, "rb") as f:
            while f.readline().strip() != b"end_header":
                pass
            rec = np.fromfile(f, dtype=[("p", "<f4", 3), ("c", "u1", 3)])
        return rec["p"], rec["c"]
    from services.api.twin.cloud import load_cloud

    return load_cloud(model_id, 0.005)


def voxel_thin(points: np.ndarray, colors: np.ndarray, voxel_m: float) -> tuple[np.ndarray, np.ndarray]:
    keys = np.floor(points / voxel_m).astype(np.int64)
    _, first = np.unique(keys, axis=0, return_index=True)
    return points[first], colors[first]


def area_mask(points: np.ndarray, rooms, margin_m: float = 0.3) -> np.ndarray:
    keep = np.zeros(len(points), bool)
    for r in rooms:
        poly = Polygon(r.polygon).buffer(margin_m)
        y = points[:, 1]
        band = (y > r.floor_y - margin_m) & (y < r.floor_y + r.height_m + margin_m)
        keep |= band & contains_xy(poly, points[:, 0], points[:, 2])
    return keep


def project(points: np.ndarray, r_c2w: np.ndarray, center: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Pixel (col, row) of each point in a face camera, and whether it lands in the image in front."""
    cam = (points - center) @ r_c2w
    z = cam[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        u = (cam[:, 0] / z + 1) / 2 * n
        v = (cam[:, 1] / z + 1) / 2 * n
    ok = (z > 0.05) & (u >= 0) & (u < n) & (v >= 0) & (v < n)
    return np.stack([u, v], axis=1), ok


def fuse_inputs(points: np.ndarray, colors: np.ndarray, rooms, sweeps, out: Path,
                occluder_margin_m: float = 1.0, volume_margin_m: float = 0.15) -> None:
    """What infra/recon/fuse_depth.py needs besides the photos and poses.

    cloud.npz: every cloud point in the box around the rooms and all exported sweeps, unthinned.
    Depth rendered from it is right for a sweep outside the room too, because the walls between
    that sweep and the room are in it (the room-only points3D would show the room's walls from
    behind). volume.json: the box to reconstruct, the rooms' own extent."""
    xz = np.concatenate([np.array(r.polygon) for r in rooms])
    y0 = min(r.floor_y for r in rooms)
    y1 = max(r.floor_y + r.height_m for r in rooms)
    vol_lo = np.array([xz[:, 0].min(), y0, xz[:, 1].min()]) - volume_margin_m
    vol_hi = np.array([xz[:, 0].max(), y1, xz[:, 1].max()]) + volume_margin_m
    centers = np.array([[s.position.x, s.position.y, s.position.z] for s in sweeps])
    lo = np.minimum(vol_lo, centers.min(axis=0)) - occluder_margin_m
    hi = np.maximum(vol_hi, centers.max(axis=0)) + occluder_margin_m
    inside = np.all((points >= lo) & (points <= hi), axis=1)
    np.savez(out / "cloud.npz", points=points[inside].astype(np.float32), colors=colors[inside])
    (out / "volume.json").write_text(json.dumps({"lo": vol_lo.round(4).tolist(), "hi": vol_hi.round(4).tolist()}) + "\n")
    log.info("fuse inputs: %d cloud points, volume %s .. %s", int(inside.sum()), vol_lo.round(2), vol_hi.round(2))


def export(property_id: str, out: Path, room_ids: list[str] | None, voxel_m: float = 0.01,
           min_visible: float = 0.02, fuse: bool = False) -> dict:
    from services.api.twin.service import get_twin

    twin = get_twin(property_id)
    model_id = twin.sources.matterport_model_id
    rooms = [r for r in twin.rooms if room_ids is None or r.room_id in room_ids]
    if not rooms:
        raise SystemExit(f"no rooms match {room_ids}")
    by_id = {s.sweep_id: s for s in twin.sweeps}
    if room_ids is None:
        sweeps = [s for s in twin.sweeps if s.rotation]
    else:
        inside = [s for s in twin.sweeps if s.room_id in room_ids and s.rotation]
        ids = {s.sweep_id for s in inside} | {n for s in inside for n in (s.neighbors or [])}
        sweeps = [by_id[i] for i in sorted(ids) if by_id[i].rotation]

    all_points, all_colors = load_points(property_id, model_id)
    keep = area_mask(all_points, rooms)
    points, colors = voxel_thin(all_points[keep], all_colors[keep], voxel_m)
    log.info("%d rooms, %d sweeps, %d points (%.0f mm voxels)", len(rooms), len(sweeps), len(points), voxel_m * 1000)
    probe = points[np.random.default_rng(0).choice(len(points), min(200_000, len(points)), replace=False)]
    probe_idx = np.random.default_rng(1).choice(len(points), min(50_000, len(points)), replace=False)

    if out.exists():
        shutil.rmtree(out)
    (out / "images").mkdir(parents=True)
    (out / "images_2").mkdir()
    (out / "sparse" / "0").mkdir(parents=True)
    if fuse:
        fuse_inputs(all_points, all_colors, rooms, sweeps, out)
    del all_points, all_colors

    jobs, lines, checks = [], [], {}
    n = 4096
    for sweep in sweeps:
        center = np.array([sweep.position.x, sweep.position.y, sweep.position.z])
        for face in FACES:
            r_c2w, flip = camera_to_interior(sweep, face)
            _, seen = project(probe, r_c2w, center, n)
            if seen.mean() < min_visible:
                continue
            name = f"{sweep.sweep_id}_{face}.jpg"
            jobs.append((sweep, face, flip, name, r_c2w, center))
            r_w2c = r_c2w.T
            t = -r_w2c @ center
            qx, qy, qz, qw = Rotation.from_matrix(r_w2c).as_quat()
            lines.append(f"{len(lines) + 1} {qw:.10f} {qx:.10f} {qy:.10f} {qz:.10f} {t[0]:.6f} {t[1]:.6f} {t[2]:.6f} 1 {name}\n\n")

    def write(job) -> tuple[str, float]:
        sweep, face, flip, name, r_c2w, center = job
        src = panos.skybox_face(model_id, sweep.sweep_id, face, RES)
        img = Image.open(src).convert("RGB")
        if flip:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if img.size != (n, n):
            raise ValueError(f"{src} is {img.size}, expected {n} px")
        img.save(out / "images" / name, quality=95, subsampling=0)
        img.resize((n // 2, n // 2), Image.LANCZOS).save(out / "images_2" / name, quality=95, subsampling=0)
        # Pose check: the cloud's colors come from these photos, so they should agree where seen.
        px, ok = project(points[probe_idx], r_c2w, center, n)
        if ok.sum() < 500:
            return name, float("nan")
        pix = np.asarray(img)[px[ok, 1].astype(int), px[ok, 0].astype(int)].astype(float).mean(axis=1)
        ref = colors[probe_idx][ok].astype(float).mean(axis=1)
        return name, float(np.corrcoef(pix, ref)[0, 1])

    with ThreadPoolExecutor(8) as pool:
        for name, corr in pool.map(write, jobs):
            checks[name] = round(corr, 3)

    sparse = out / "sparse" / "0"
    sparse.joinpath("cameras.txt").write_text(f"1 PINHOLE {n} {n} {n / 2} {n / 2} {n / 2} {n / 2}\n")
    sparse.joinpath("images.txt").write_text("".join(lines))
    with open(sparse / "points3D.txt", "w") as f:
        for i, (p, c) in enumerate(zip(points, colors), start=1):
            f.write(f"{i} {p[0]:.5f} {p[1]:.5f} {p[2]:.5f} {c[0]} {c[1]} {c[2]} 0\n")
    valid = [v for v in checks.values() if v == v]
    summary = {
        "property_id": property_id,
        "rooms": [r.room_id for r in rooms],
        "sweeps": len(sweeps),
        "images": len(jobs),
        "points": int(len(points)),
        "pose_check_median_corr": round(float(np.median(valid)), 3) if valid else None,
        "pose_check_min_corr": round(float(np.min(valid)), 3) if valid else None,
        "per_image": checks,
    }
    (out / "check.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    parser.add_argument("--room", action="append", help="room id (repeatable); its sweeps and their neighbors")
    parser.add_argument("--all", action="store_true", help="every room and sweep")
    parser.add_argument("--voxel", type=float, default=0.01, help="point thinning voxel in meters")
    parser.add_argument("--fuse", action="store_true", help="also write cloud.npz and volume.json for fuse_depth.py")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not args.room and not args.all:
        parser.error("give --room or --all")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out = args.out or OUT_ROOT / (f"{args.property_id}_all" if args.all else f"{args.property_id}_{args.room[0][:8]}")
    s = export(args.property_id, out, None if args.all else args.room, args.voxel, fuse=args.fuse)
    print(f"wrote {out}: {s['images']} images from {s['sweeps']} sweeps, {s['points']:,} points;"
          f" pose check corr median {s['pose_check_median_corr']}, min {s['pose_check_min_corr']}")


if __name__ == "__main__":
    main()
