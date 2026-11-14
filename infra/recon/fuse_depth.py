"""High-resolution geometry of one scan area: photo-guided depth per face, fused into a TSDF.

The MatterPak cloud is metric but only about 1 cm fine (a resampling of Matterport's fused
surface). The 4k photos are about 1 mm per pixel at 2 m. So each photo gets a dense depth map
that follows the photo's edges (Prompt Depth Anything, Lin et al. 2025: a monocular depth network
prompted with low-resolution metric depth). The network alone strays 1 to 4 cm from the cloud, so
its depth is then fitted to the cloud in small windows (a guided filter, He et al. 2013, with the
network's depth as the guide): distances come from the cloud, detail finer than a window from the
photo. The depth maps of all sweeps are averaged in a truncated signed distance volume (Curless &
Levoy 1996).

Runs on a dataset written by `python -m services.api.twin.gs_export <id> --room <room> --fuse`:

    python fuse_depth.py prompt <data>            # cloud -> low-res metric depth per photo (numpy only)
    python fuse_depth.py depth  <data> [--size N] # photo + prompt -> dense depth (needs torch, transformers)
    python fuse_depth.py align  <data>            # dense depth pinned back to the cloud, window by window
    python fuse_depth.py fuse   <data> [--voxel m]# depth maps -> fused.ply (needs torch, scikit-image)
    python fuse_depth.py all    <data>

Everything is in the interior frame (Y up, meters). Cameras are the dataset's: pinhole, 90 degree
field of view, OpenCV axes (x right, y down, z forward).
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
from PIL import Image

log = logging.getLogger("fuse_depth")

PROMPT_PX = 1024             # prompt depth is rendered at this face size
CLOUD_SPACING_M = 0.014      # splat footprint in meters: just over the cloud's ~1 cm grid, so surfaces are closed
MODEL_ID = "depth-anything/prompt-depth-anything-vitl-hf"


# ---------------------------------------------------------------- dataset

def read_views(data: Path) -> list[dict]:
    """[{name, r (3x3 world->camera), t (3,), center (3,)}] from the COLMAP text model."""
    views = []
    for line in (data / "sparse" / "0" / "images.txt").read_text().splitlines():
        parts = line.split()
        if len(parts) != 10 or line.startswith("#"):
            continue
        w, x, y, z = map(float, parts[1:5])
        r = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ])
        t = np.array(list(map(float, parts[5:8])))
        views.append({"name": parts[9], "r": r, "t": t, "center": -r.T @ t})
    return views


def stem(view: dict) -> str:
    return Path(view["name"]).stem


# ---------------------------------------------------------------- stage 1: prompt depth from the cloud

def splat_depth(points: np.ndarray, r: np.ndarray, t: np.ndarray, n: int,
                colors: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray | None]:
    """Z-buffered depth (n, n) of the cloud in a 90 degree pinhole face; 0 where nothing lands.

    Each point is drawn as a square the size its ~1 cm neighborhood has on screen, so a scanned
    surface hides what is behind it. With colors, also the (n, n, 3) color of the nearest point."""
    cam = points @ r.T.astype(np.float32) + t.astype(np.float32)
    z = cam[:, 2]
    f = n / 2
    front = z > 0.08
    lim = z[front] * 1.02  # a little outside the frustum, so footprints of edge points still land
    front[front] = (np.abs(cam[front, 0]) < lim) & (np.abs(cam[front, 1]) < lim)
    cam, z = cam[front], z[front]
    order = np.argsort(-z)  # far to near: with repeated pixels the last (nearest) write stays
    cam, z = cam[order], z[order]
    u = f * cam[:, 0] / z + f
    v = f * cam[:, 1] / z + f
    width = np.clip(np.ceil(CLOUD_SPACING_M * f / z), 1, 24).astype(np.int32)
    u0 = np.floor(u - width / 2 + 0.5).astype(np.int32)
    v0 = np.floor(v - width / 2 + 0.5).astype(np.int32)
    depth = np.full(n * n, np.inf, np.float32)
    src = np.full(n * n, -1, np.int64)
    for w in np.unique(width):
        sel = np.flatnonzero(width == w)
        for dy in range(w):
            for dx in range(w):
                px, py = u0[sel] + dx, v0[sel] + dy
                ok = (px >= 0) & (px < n) & (py >= 0) & (py < n)
                idx = py[ok] * n + px[ok]
                zz = z[sel[ok]]
                nearer = zz < depth[idx]
                idx, zz, who = idx[nearer], zz[nearer], sel[ok][nearer]
                depth[idx] = zz
                src[idx] = who
    hit = np.isfinite(depth)
    depth[~hit] = 0
    # The nearest point per pixel steps like a staircase on slanted surfaces and sits a little in
    # front of them. Replace it by the mean of the points of that same surface: those within 1% + 4 mm.
    total, count = np.zeros(n * n), np.zeros(n * n)
    for w in np.unique(width):
        sel = np.flatnonzero(width == w)
        for dy in range(w):
            for dx in range(w):
                px, py = u0[sel] + dx, v0[sel] + dy
                ok = (px >= 0) & (px < n) & (py >= 0) & (py < n)
                idx = py[ok] * n + px[ok]
                zz = z[sel[ok]]
                same = zz <= depth[idx] * 1.01 + 0.004
                total += np.bincount(idx[same], weights=zz[same], minlength=n * n)
                count += np.bincount(idx[same], minlength=n * n)
    depth[hit] = (total[hit] / count[hit]).astype(np.float32)
    image = None
    if colors is not None:
        image = np.zeros((n * n, 3), np.uint8)
        image[hit] = colors[front][order][src[hit]]
        image = image.reshape(n, n, 3)
    return depth.reshape(n, n), image


def stage_prompt(data: Path, check: bool = False) -> None:
    cloud = np.load(data / "cloud.npz")
    points, colors = cloud["points"], cloud["colors"]
    out = data / "prompt"
    out.mkdir(exist_ok=True)
    views = read_views(data)
    started = time.time()
    for i, view in enumerate(views):
        depth, image = splat_depth(points, view["r"], view["t"], PROMPT_PX, colors if check else None)
        np.save(out / f"{stem(view)}.npy", depth)
        if check and i % 6 == 0:
            photo = Image.open(data / "images_2" / view["name"]).resize((PROMPT_PX, PROMPT_PX), Image.LANCZOS)
            shade = np.where(depth > 0, 255 * (1 - np.clip(depth / 8, 0, 1)), 0).astype(np.uint8)
            sheet = np.concatenate([np.asarray(photo), image, np.repeat(shade[..., None], 3, axis=2)], axis=1)
            Image.fromarray(sheet).save(out / f"check_{stem(view)}.jpg", quality=85)
        log.info("prompt %d/%d %s: %.0f%% covered, %.0f s", i + 1, len(views), view["name"],
                 100 * (depth > 0).mean(), time.time() - started)


# ---------------------------------------------------------------- stage 2: photo-guided dense depth

def fill_holes(depth: np.ndarray) -> np.ndarray:
    """Every empty pixel takes its nearest filled neighbor (the network reads a 0 as a distance)."""
    from scipy import ndimage

    empty = depth <= 0
    if not empty.any():
        return depth
    _, (iy, ix) = ndimage.distance_transform_edt(empty, return_indices=True)
    return depth[iy, ix]


def stage_depth(data: Path, size: int, prompt_ratio: float, device: str) -> None:
    """depth/<view>.npy: float32 (size, size) z-depth in meters, the network's own (not yet aligned)."""
    import torch
    import torch.nn.functional as F
    from transformers import PromptDepthAnythingForDepthEstimation

    if size % 14:
        raise SystemExit("--size must be a multiple of 14 (the network's patch size)")
    model = PromptDepthAnythingForDepthEstimation.from_pretrained(MODEL_ID).to(device).eval()
    mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)
    out = data / "depth"
    out.mkdir(exist_ok=True)
    prompt_px = round(size / prompt_ratio)
    views = read_views(data)
    started = time.time()
    for i, view in enumerate(views):
        photo = Image.open(data / "images" / view["name"]).convert("RGB").resize((size, size), Image.LANCZOS)
        pixels = torch.from_numpy(np.array(photo)).to(device).permute(2, 0, 1)[None].float() / 255
        cloud_depth = np.load(data / "prompt" / f"{stem(view)}.npy").astype(np.float32)
        filled = torch.from_numpy(fill_holes(cloud_depth)).to(device)[None, None]
        # The network was trained with a prompt about 7.5 times smaller than the photo (phone lidar).
        prompt = F.interpolate(filled, size=(prompt_px, prompt_px), mode="area")
        with torch.inference_mode():
            depth = model(pixel_values=(pixels - mean) / std, prompt_depth=prompt).predicted_depth[0]
        np.save(out / f"{stem(view)}.npy", depth.float().cpu().numpy())
        log.info("depth %d/%d %s, %.0f s", i + 1, len(views), view["name"], time.time() - started)


# ---------------------------------------------------------------- stage 2b: pin the depth to the cloud

ALIGN_RADIUS_PX = 12         # window half-size at PROMPT_PX: about 10 cm wide at 2 m
ALIGN_EPS_M2 = 0.002 ** 2    # depth relief below ~2 mm in a window is not trusted to carry scale


def align_to_cloud(pred, cloud):
    """pred (S, S) network depth, cloud (P, P) rendered cloud depth (0 = empty), both torch.

    In each window, cloud ~ a * pred + b is fitted over the pixels where the two already roughly
    agree (so cloud edges that are a few pixels off the photo's, and things the cloud missed, do
    not pull the fit), and a, b are averaged over the windows covering a pixel. Returns the
    corrected depth (S, S), where it is backed by the cloud (S, S) bool, and before/after stats."""
    import torch
    import torch.nn.functional as F

    def box(x):
        k = 2 * ALIGN_RADIUS_PX + 1
        return F.avg_pool2d(x[None, None], k, 1, ALIGN_RADIUS_PX)[0, 0]

    size, px = pred.shape[0], cloud.shape[0]
    p = F.interpolate(pred[None, None], size=(px, px), mode="area")[0, 0].double()
    c = cloud.double()
    seen = c > 0
    q = p
    for tol_abs, tol_rel in ((0.06, 0.04), (0.02, 0.01)):
        m = (seen & ((c - q).abs() < torch.clamp(tol_rel * c, min=tol_abs))).double()
        n = box(m)
        backed = (n > 0.15).double()
        n = n.clamp(min=1e-9)
        mean_p, mean_c = box(m * p) / n, box(m * c) / n
        a = ((box(m * p * c) / n - mean_p * mean_c) / (box(m * p * p) / n - mean_p * mean_p + ALIGN_EPS_M2)).clamp(0, 2)
        b = mean_c - a * mean_p
        k = box(backed)
        have = k > 0
        k = k.clamp(min=1e-9)
        a_bar = torch.where(have, box(a * backed) / k, torch.ones_like(a))
        b_bar = torch.where(have, box(b * backed) / k, torch.zeros_like(b))
        q = a_bar * p + b_bar
    inlier = m > 0
    stats = {
        "raw_median_mm": 1000 * float((p - c).abs()[seen].median()),
        "aligned_median_mm": 1000 * float((q - c).abs()[inlier].median()),
        "aligned_p90_mm": 1000 * float(torch.quantile((q - c).abs()[inlier], 0.9)),
        "fitted": float(inlier.sum() / seen.sum().clamp(min=1)),
    }

    def up(x):
        return F.interpolate(x[None, None].float(), size=(size, size), mode="bilinear", align_corners=False)[0, 0]

    return up(a_bar) * pred + up(b_bar), up(have.float()) > 0.999, stats


def stage_align(data: Path, device: str) -> None:
    import torch

    out = data / "aligned"
    out.mkdir(exist_ok=True)
    views = read_views(data)
    report = {}
    for i, view in enumerate(views):
        pred = torch.from_numpy(np.load(data / "depth" / f"{stem(view)}.npy")).to(device)
        cloud = torch.from_numpy(np.load(data / "prompt" / f"{stem(view)}.npy").astype(np.float32)).to(device)
        depth, seen, stats = align_to_cloud(pred, cloud)
        np.save(out / f"{stem(view)}.npy", depth.cpu().numpy())
        np.save(out / f"{stem(view)}_seen.npy", seen.cpu().numpy())
        report[view["name"]] = {k: round(v, 3) for k, v in stats.items()}
        log.info("align %d/%d %s: |depth - cloud| median %.1f -> %.1f mm (p90 %.1f), %.0f%% of the cloud fitted",
                 i + 1, len(views), view["name"], stats["raw_median_mm"], stats["aligned_median_mm"],
                 stats["aligned_p90_mm"], 100 * stats["fitted"])
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")


# ---------------------------------------------------------------- stage 3: TSDF fusion

def pixel_weights(depth, seen, dilate_px: int = 3):
    """Per-pixel fusion weight (n, n): cos(view angle) / distance^2, and 0 at depth edges, at
    grazing angles and where the cloud did not back the depth."""
    import torch
    import torch.nn.functional as F

    n = depth.shape[0]
    f = n / 2
    c = (torch.arange(n, device=depth.device, dtype=torch.float32) + 0.5 - f) / f
    x, y = c[None, :] * depth, c[:, None] * depth
    p = torch.stack([x, y, depth], dim=0)
    du = F.pad(p[:, :, 2:] - p[:, :, :-2], (1, 1, 0, 0))
    dv = F.pad(p[:, 2:, :] - p[:, :-2, :], (0, 0, 1, 1))
    normal = torch.cross(du, dv, dim=0)
    cos = (normal * p).sum(0).abs() / (normal.norm(dim=0) * p.norm(dim=0) + 1e-12)
    bad = (cos < 0.1) | ~seen  # a depth step looks like a surface seen edge-on
    bad = F.max_pool2d(bad[None, None].float(), 2 * dilate_px + 1, 1, dilate_px)[0, 0] > 0
    weight = cos / depth.clamp(min=0.5) ** 2
    weight[bad] = 0
    return weight


def stage_fuse(data: Path, voxel: float, trunc_voxels: float, device: str, name: str) -> None:
    import torch
    import torch.nn.functional as F

    volume = json.loads((data / "volume.json").read_text())
    lo, hi = np.array(volume["lo"]), np.array(volume["hi"])
    dims = np.ceil((hi - lo) / voxel).astype(int)
    trunc = trunc_voxels * voxel
    log.info("volume %s voxels of %.1f mm (%.0f M), truncation %.1f mm", dims, voxel * 1000,
             dims.prod() / 1e6, trunc * 1000)
    total = torch.zeros(tuple(dims), dtype=torch.float32, device=device)   # sum of weight * tsdf
    weight = torch.zeros(tuple(dims), dtype=torch.float32, device=device)
    ys = torch.tensor(lo[1], device=device) + (torch.arange(dims[1], device=device) + 0.5) * voxel
    zs = torch.tensor(lo[2], device=device) + (torch.arange(dims[2], device=device) + 0.5) * voxel
    slab = max(1, int(6_000_000 // (dims[1] * dims[2])))
    views = read_views(data)
    started = time.time()
    for i, view in enumerate(views):
        depth = torch.from_numpy(np.load(data / "aligned" / f"{stem(view)}.npy")).to(device)
        seen = torch.from_numpy(np.load(data / "aligned" / f"{stem(view)}_seen.npy")).to(device)
        w_map = pixel_weights(depth, seen)
        layers = torch.stack([depth, w_map, (w_map > 0).float()])[None]
        n = depth.shape[0]
        r = torch.tensor(view["r"], dtype=torch.float32, device=device)
        t = torch.tensor(view["t"], dtype=torch.float32, device=device)
        for x0 in range(0, dims[0], slab):
            x1 = min(dims[0], x0 + slab)
            xs = torch.tensor(lo[0], device=device) + (torch.arange(x0, x1, device=device) + 0.5) * voxel
            grid = torch.stack(torch.meshgrid(xs.float(), ys.float(), zs.float(), indexing="ij"), dim=-1)
            cam = grid.reshape(-1, 3) @ r.T + t
            z = cam[:, 2]
            front = z > 0.1
            uv = cam[:, :2] / z.clamp(min=0.1)[:, None]  # in [-1, 1] across the 90 degree face
            front &= (uv.abs() < 1).all(dim=1)
            if not front.any():
                continue
            got = F.grid_sample(layers, uv[front][None, :, None, :], mode="bilinear", align_corners=False)[0, :, :, 0]
            sdf = got[0] - z[front]
            ok = (got[2] > 0.999) & (sdf > -trunc)
            w = torch.where(ok, got[1], torch.zeros_like(sdf))
            idx = torch.nonzero(front)[:, 0]
            total[x0:x1].view(-1).index_add_(0, idx, w * (sdf / trunc).clamp(max=1))
            weight[x0:x1].view(-1).index_add_(0, idx, w)
        log.info("fuse %d/%d %s, %.0f s", i + 1, len(views), view["name"], time.time() - started)

    observed = weight > 0
    total[observed] /= weight[observed]
    tsdf = total.cpu().numpy()
    mask = observed.cpu().numpy()
    del total, weight, observed
    vertices, faces, normals = extract(tsdf, mask, voxel, lo)
    log.info("mesh: %d vertices, %d triangles, %.0f s", len(vertices), len(faces), time.time() - started)
    write_ply(data / f"{name}.ply", vertices, faces, normals)
    (data / f"{name}.json").write_text(json.dumps({
        "voxel_m": voxel, "truncation_m": trunc, "dims": dims.tolist(), "views": len(views),
        "vertices": int(len(vertices)), "triangles": int(len(faces))}, indent=1) + "\n")


def extract(tsdf: np.ndarray, mask: np.ndarray, voxel: float, lo: np.ndarray, block: int = 256,
            min_component_faces: int = 2000):
    """Marching cubes in blocks (the whole grid at once does not fit), seams welded, specks dropped."""
    from scipy import sparse
    from skimage import measure

    all_v, all_f, all_n, offset = [], [], [], 0
    for x0 in range(0, tsdf.shape[0] - 1, block):
        for y0 in range(0, tsdf.shape[1] - 1, block):
            for z0 in range(0, tsdf.shape[2] - 1, block):
                sl = (slice(x0, x0 + block + 1), slice(y0, y0 + block + 1), slice(z0, z0 + block + 1))
                t, m = tsdf[sl], mask[sl]
                if not m.any() or t[m].min() >= 0 or t[m].max() <= 0:
                    continue
                try:
                    v, f, nrm, _ = measure.marching_cubes(t, level=0.0, mask=m)
                except (ValueError, RuntimeError):
                    continue
                all_v.append(v + (x0, y0, z0))
                all_f.append(f + offset)
                all_n.append(nrm)
                offset += len(v)
    v = np.concatenate(all_v)
    f = np.concatenate(all_f)
    nrm = np.concatenate(all_n)
    # Weld the block seams: the same grid-edge crossing is made once per block that touches it.
    key = np.round(v * 64).astype(np.int64)
    key = (key[:, 0] << 42) | (key[:, 1] << 21) | key[:, 2]
    _, first, inverse = np.unique(key, return_index=True, return_inverse=True)
    v, nrm, f = v[first], nrm[first], inverse[f]
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    edges = np.concatenate([f[:, [0, 1]], f[:, [1, 2]]])
    graph = sparse.coo_matrix((np.ones(len(edges), bool), (edges[:, 0], edges[:, 1])), shape=(len(v), len(v)))
    _, label = sparse.csgraph.connected_components(graph, directed=False)
    faces_per = np.bincount(label[f[:, 0]], minlength=label.max() + 1)
    f = f[faces_per[label[f[:, 0]]] >= min_component_faces]
    used = np.unique(f)
    remap = np.full(len(v), -1, np.int64)
    remap[used] = np.arange(len(used))
    v, nrm, f = v[used], nrm[used], remap[f]
    # skimage's normals point up the gradient (toward free space, tsdf > 0): outward, as wanted.
    return (lo + (v + 0.5) * voxel).astype(np.float32), f.astype(np.int32), nrm.astype(np.float32)


def write_ply(path: Path, vertices: np.ndarray, faces: np.ndarray, normals: np.ndarray) -> None:
    vert = np.empty(len(vertices), dtype=[("p", "<f4", 3), ("n", "<f4", 3)])
    vert["p"], vert["n"] = vertices, normals
    face = np.empty(len(faces), dtype=[("k", "u1"), ("i", "<i4", 3)])
    face["k"], face["i"] = 3, faces
    header = ("ply\nformat binary_little_endian 1.0\n"
              f"element vertex {len(vertices)}\nproperty float x\nproperty float y\nproperty float z\n"
              "property float nx\nproperty float ny\nproperty float nz\n"
              f"element face {len(faces)}\nproperty list uchar int vertex_indices\nend_header\n")
    with open(path, "wb") as out:
        out.write(header.encode())
        vert.tofile(out)
        face.tofile(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=("prompt", "depth", "align", "fuse", "all"))
    parser.add_argument("data", type=Path)
    parser.add_argument("--check", action="store_true", help="prompt: also write photo | cloud | depth sheets")
    parser.add_argument("--size", type=int, default=2016, help="depth: face size the network runs at (multiple of 14)")
    parser.add_argument("--prompt-ratio", type=float, default=7.5, help="depth: photo size / prompt size")
    parser.add_argument("--voxel", type=float, default=0.005, help="fuse: voxel size in meters")
    parser.add_argument("--trunc", type=float, default=4, help="fuse: truncation distance in voxels")
    parser.add_argument("--name", default="fused", help="fuse: output name (<data>/<name>.ply)")
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.stage in ("prompt", "all"):
        stage_prompt(args.data, args.check)
    if args.stage in ("depth", "all"):
        stage_depth(args.data, args.size, args.prompt_ratio, args.device)
    if args.stage in ("align", "all"):
        stage_align(args.data, args.device)
    if args.stage in ("fuse", "all"):
        stage_fuse(args.data, args.voxel, args.trunc, args.device, args.name)


if __name__ == "__main__":
    main()
