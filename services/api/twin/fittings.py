"""Built-in fittings (fridge, range, hood, dishwasher, cabinets, island, sink) as simple solids.

Top-down, not mesh simplification: find each object in the sweep panoramas, lift its pixels to 3D
through the point cloud, fuse the sweeps, fit a box along the house's axes, then build a clean
parametric model of that size.

  1. detect: Grounding DINO (open-vocabulary boxes) + SAM (masks) on each sweep's side cube faces.
  2. lift:   the room's cloud points projected into the same face (nearest point per pixel), so a
             mask pixel names a 3D point. Cached per sweep: this is the slow part (CPU).
  3. fuse:   labeled points vote on a 5 cm voxel grid; connected voxels of one label are one object.
  4. build:  boxes snapped to the floor and the wall behind, replaced with parametric models.

    python -m services.api.twin.fittings detect onXQLXhfyQv --rooms Kitchen
    python -m services.api.twin.fittings build onXQLXhfyQv
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial.transform import Rotation

from services.api.integrations.config import fixtures_dir
from services.api.twin.cloud import iter_points
from services.api.twin.mesh import RAW_DIR
from services.api.twin.panos import skybox_face

log = logging.getLogger(__name__)

DETECTOR = "IDEA-Research/grounding-dino-tiny"
SEGMENTER = "facebook/sam-vit-base"
LABELS = ("refrigerator", "stove", "range hood", "dishwasher", "microwave", "kitchen island", "cabinet", "sink")
SIDE_FACES = ("px", "nx", "pz", "nz")
DETECT_PX = 1024
DEPTH_PX = 512
BOX_THRESHOLD = 0.28
VOXEL_M = 0.05


def work_dir(model_id: str) -> Path:
    return RAW_DIR / model_id / "fittings"


# --- 2. lifting -------------------------------------------------------------------------------

def sweep_rotation(sweep: dict) -> np.ndarray:
    """Pano frame -> interior frame. The twin stores this already converted (panos.interior_quaternion)."""
    return Rotation.from_quat([sweep["rotation"][k] for k in "xyzw"]).as_matrix()


def face_pixels(local: np.ndarray, face: str, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(row, col, valid) of pano-frame directions on a GL cube face; inverse of panos.gl_face_directions."""
    x, y, z = local.T
    with np.errstate(divide="ignore", invalid="ignore"):
        sc, tc, ok = {
            "px": (-z / x, -y / x, x > np.maximum(np.abs(y), np.abs(z))),
            "nx": (z / -x, -y / -x, -x > np.maximum(np.abs(y), np.abs(z))),
            "pz": (x / z, -y / z, z > np.maximum(np.abs(x), np.abs(y))),
            "nz": (-x / -z, -y / -z, -z > np.maximum(np.abs(x), np.abs(y))),
            "py": (x / y, z / y, y > np.maximum(np.abs(x), np.abs(z))),
            "ny": (x / -y, -z / -y, -y > np.maximum(np.abs(x), np.abs(z))),
        }[face]
    col = np.clip(((sc + 1) / 2 * n).astype(int), 0, n - 1)
    row = np.clip(((tc + 1) / 2 * n).astype(int), 0, n - 1)
    return row, col, ok


def visible_points(points: np.ndarray, eye: np.ndarray, rot: np.ndarray, face: str, n: int = DEPTH_PX) -> np.ndarray:
    """(n, n) index of the nearest visible point per face pixel, -1 where none.

    The cloud is sparse up close, so farther surfaces show through gaps; a pixel's point is kept
    only if it is within 15 cm of the nearest depth around it.
    """
    rel = points - eye
    local = rel @ rot  # interior -> pano frame (rot is pano -> interior)
    dist = np.linalg.norm(rel, axis=1)
    row, col, ok = face_pixels(local, face, n)
    idx = np.flatnonzero(ok)
    order = idx[np.argsort(-dist[idx])]  # far first, so the nearest point is written last
    owner = np.full((n, n), -1)
    owner[row[order], col[order]] = order
    depth = np.where(owner >= 0, dist[np.maximum(owner, 0)], np.inf)
    near = ndimage.minimum_filter(depth, size=5)
    owner[depth > near + 0.15] = -1
    return owner


# --- 1. detection -----------------------------------------------------------------------------

class Detector:
    def __init__(self):
        import torch
        from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor, SamModel, SamProcessor

        torch.set_num_threads(max(1, torch.get_num_threads()))
        self.torch = torch
        self.dino_proc = AutoProcessor.from_pretrained(DETECTOR)
        self.dino = AutoModelForZeroShotObjectDetection.from_pretrained(DETECTOR).eval()
        self.sam_proc = SamProcessor.from_pretrained(SEGMENTER)
        self.sam = SamModel.from_pretrained(SEGMENTER).eval()

    def __call__(self, image: Image.Image) -> list[dict]:
        torch = self.torch
        prompt = ". ".join(LABELS) + "."
        inputs = self.dino_proc(images=image, text=prompt, return_tensors="pt")
        with torch.no_grad():
            out = self.dino(**inputs)
        res = self.dino_proc.post_process_grounded_object_detection(
            out, inputs.input_ids, threshold=BOX_THRESHOLD, text_threshold=0.25, target_sizes=[image.size[::-1]])[0]
        labels = res.get("text_labels", res["labels"])
        dets = []
        for box, score, label in zip(res["boxes"].tolist(), res["scores"].tolist(), labels):
            label = _canonical(label)
            x0, y0, x1, y1 = box
            if label is None or (x1 - x0) * (y1 - y0) > 0.6 * image.size[0] * image.size[1]:
                continue
            dets.append({"label": label, "score": round(float(score), 3), "box": [round(v, 1) for v in box]})
        if not dets:
            return []
        inputs = self.sam_proc(image, input_boxes=[[d["box"] for d in dets]], return_tensors="pt")
        with torch.no_grad():
            out = self.sam(**inputs, multimask_output=False)
        masks = self.sam_proc.image_processor.post_process_masks(
            out.pred_masks, inputs["original_sizes"], inputs["reshaped_input_sizes"])[0][:, 0].numpy()
        for d, m in zip(dets, masks):
            d["mask"] = m
        return dets


def _canonical(label: str) -> str | None:
    label = label.strip().lower()
    for name in LABELS:
        if name in label:
            return name
    return {"oven": "stove", "range": "stove", "fridge": "refrigerator", "island": "kitchen island"}.get(label)


def detect(twin: dict, model_id: str, room_labels: list[str], refresh: bool = False,
           only: list[str] | None = None) -> None:
    rooms = [r for r in twin["rooms"] if r["label"] in room_labels]
    room_ids = {r["room_id"] for r in rooms}
    sweeps = [s for s in twin["sweeps"] if s.get("room_id") in room_ids and (not only or s["sweep_id"] in only)]
    out = work_dir(model_id)
    out.mkdir(parents=True, exist_ok=True)
    todo = [s for s in sweeps if refresh or not (out / f"{s['sweep_id']}.npz").exists()]
    log.info("fittings: %d sweeps in %s, %d to detect", len(sweeps), ", ".join(room_labels), len(todo))
    if not todo:
        return

    eyes = np.array([[s["position"][k] for k in "xyz"] for s in todo])
    lo, hi = eyes.min(0) - [7.0, 2.0, 7.0], eyes.max(0) + [7.0, 2.5, 7.0]
    chunks = []
    for c in iter_points(model_id):
        chunks.append(c[np.all((c >= lo) & (c <= hi), axis=1)].copy())
    points = np.concatenate(chunks).astype(np.float64)
    log.info("fittings: %d cloud points around the sweeps", len(points))

    detector = Detector()
    for s in todo:
        t0 = time.time()
        eye = np.array([s["position"][k] for k in "xyz"])
        rot = sweep_rotation(s)
        keep = np.linalg.norm(points - eye, axis=1) < 7.0
        local_points = points[keep]
        labels, scores, xyz, colors, det_ids, det_meta = [], [], [], [], [], []
        for face in SIDE_FACES:
            image = Image.open(skybox_face(model_id, s["sweep_id"], face, "2k")).convert("RGB").resize((DETECT_PX, DETECT_PX))
            owner = visible_points(local_points, eye, rot, face)
            pixels = np.asarray(image)
            for d in detector(image):
                small = np.asarray(Image.fromarray(d["mask"]).resize((DEPTH_PX, DEPTH_PX), Image.NEAREST))
                own = owner[small & (owner >= 0)]
                if len(own) < 30:
                    continue
                k = len(det_meta)
                det_meta.append({"label": d["label"], "score": d["score"], "face": face, "box": d["box"],
                                 "color": [int(v) for v in np.median(pixels[d["mask"]], axis=0)]})
                xyz.append(local_points[own])
                labels += [d["label"]] * len(own)
                scores.append(np.full(len(own), d["score"]))
                det_ids.append(np.full(len(own), k))
        np.savez_compressed(
            out / f"{s['sweep_id']}.npz",
            xyz=np.concatenate(xyz).astype(np.float32) if xyz else np.empty((0, 3), np.float32),
            score=np.concatenate(scores).astype(np.float32) if scores else np.empty(0, np.float32),
            det=np.concatenate(det_ids).astype(np.int32) if det_ids else np.empty(0, np.int32),
            meta=json.dumps({"sweep_id": s["sweep_id"], "detections": det_meta}),
        )
        log.info("fittings: sweep %s: %d detections in %.0f s", s["sweep_id"], len(det_meta), time.time() - t0)


def load_detections(model_id: str) -> list[dict]:
    """Every detection with its lifted points, across the detected sweeps."""
    out = []
    for path in sorted(work_dir(model_id).glob("*.npz")):
        z = np.load(path)
        meta = json.loads(str(z["meta"]))
        for k, d in enumerate(meta["detections"]):
            sel = z["det"] == k
            out.append({**d, "sweep_id": meta["sweep_id"], "xyz": z["xyz"][sel].astype(np.float64)})
    return out


# --- 3. fusion --------------------------------------------------------------------------------

@dataclass
class Fitting:
    label: str
    lo: np.ndarray       # (x, y, z) box corner, interior frame
    hi: np.ndarray
    color: tuple[int, int, int]
    voxels: int
    sweeps: int


MIN_VOXELS = {"refrigerator": 60, "stove": 20, "range hood": 20, "dishwasher": 20, "microwave": 10,
              "kitchen island": 60, "cabinet": 30, "sink": 8}


def fuse(dets: list[dict]) -> list[Fitting]:
    """Labeled points vote per voxel; each label's connected voxels are one object."""
    allp = np.concatenate([d["xyz"] for d in dets])
    origin = allp.min(0) - VOXEL_M
    shape = tuple(np.ceil((allp.max(0) - origin) / VOXEL_M).astype(int) + 2)
    votes = np.zeros((len(LABELS), *shape), np.float32)
    for d in dets:
        li = LABELS.index(d["label"])
        ijk = np.unique(((d["xyz"] - origin) / VOXEL_M).astype(int), axis=0)
        votes[li, ijk[:, 0], ijk[:, 1], ijk[:, 2]] += d["score"]
    best = votes.argmax(0)
    total = votes.max(0)
    out = []
    for li, label in enumerate(LABELS):
        mask = (best == li) & (total >= 0.6)  # about two detections, or one confident one
        mask = ndimage.binary_closing(mask, iterations=2) & (total > 0) | mask
        lab, n = ndimage.label(mask, structure=np.ones((3, 3, 3)))
        for k in range(1, n + 1):
            vox = np.argwhere(lab == k)
            if len(vox) < MIN_VOXELS[label]:
                continue
            pts = origin + (vox + 0.5) * VOXEL_M
            lo, hi = np.percentile(pts, 2, axis=0) - VOXEL_M / 2, np.percentile(pts, 98, axis=0) + VOXEL_M / 2
            inside = [d for d in dets if d["label"] == label and _overlap(d["xyz"], lo, hi) > 0.5]
            color = tuple(int(v) for v in np.median([d["color"] for d in inside], axis=0)) if inside else (128, 128, 128)
            out.append(Fitting(label, lo, hi, color, len(vox), len({d["sweep_id"] for d in inside})))
    return out


def _overlap(xyz: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    return float(np.all((xyz >= lo) & (xyz <= hi), axis=1).mean()) if len(xyz) else 0.0


# --- 4. parametric models ---------------------------------------------------------------------

COUNTER_M = 0.03
TOE_KICK_M = (0.10, 0.07)   # height, setback
STANDARD_DEPTH_M = {"base": 0.61, "upper": 0.33, "tall": 0.61, "refrigerator": 0.76, "stove": 0.66,
                    "dishwasher": 0.61, "microwave": 0.40}
SNAP_GAP_M = 0.9


def _wall_faces(twin: dict, floor_id: str) -> list[tuple[int, float, float, float]]:
    """Axis-aligned wall faces as (axis, coordinate, span_lo, span_hi): axis 0 = face at constant x."""
    faces = []
    for w in twin["walls"]:
        if w["floor_id"] != floor_id:
            continue
        (x0, z0), (x1, z1) = w["ends"]
        h = w["thickness_m"] / 2
        if abs(x1 - x0) < 0.05:
            faces += [(0, x0 - h, min(z0, z1), max(z0, z1)), (0, x0 + h, min(z0, z1), max(z0, z1))]
        elif abs(z1 - z0) < 0.05:
            faces += [(2, z0 - h, min(x0, x1), max(x0, x1)), (2, z0 + h, min(x0, x1), max(x0, x1))]
    return faces


def snap_to_wall(lo: np.ndarray, hi: np.ndarray, faces) -> tuple[int, int] | None:
    """Extend the box back to the nearest wall face behind it; returns (axis, side) of the back."""
    best = None
    for axis, coord, s0, s1 in faces:
        other = 2 if axis == 0 else 0
        span = min(hi[other], s1) - max(lo[other], s0)
        if span < 0.5 * (hi[other] - lo[other]):
            continue
        for side in (1, -1):
            gap = (coord - hi[axis]) if side > 0 else (lo[axis] - coord)
            if -0.15 < gap < SNAP_GAP_M and (best is None or gap < best[0]):
                best = (gap, axis, side, coord)
    if best is None:
        return None
    _, axis, side, coord = best
    if side > 0:
        hi[axis] = coord
    else:
        lo[axis] = coord
    return axis, side


def _box(lo, hi) -> "trimesh.Trimesh":
    import trimesh

    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    m = trimesh.creation.box(extents=np.maximum(hi - lo, 1e-3))
    m.apply_translation((lo + hi) / 2)
    return m


def _front(lo, hi, back) -> tuple[int, int, float]:
    """(axis, outward sign, coordinate) of the face opposite the back."""
    axis, side = back if back else (2, -1)
    return axis, -side, (lo[axis] if side > 0 else hi[axis])


def _inset(lo, hi, back, depth: float):
    """The box cut to `depth` from its back."""
    lo, hi = lo.copy(), hi.copy()
    axis, side = back
    if side > 0:
        lo[axis] = max(lo[axis], hi[axis] - depth)
    else:
        hi[axis] = min(hi[axis], lo[axis] + depth)
    return lo, hi


def model(f: Fitting, back, floor_y: float, ceiling_y: float) -> dict[str, list]:
    """Parametric parts of one fitting, keyed by material."""
    lo, hi = f.lo.copy(), f.hi.copy()
    parts: dict[str, list] = {}
    add = lambda mat, m: parts.setdefault(mat, []).append(m)  # noqa: E731
    color = "steel" if max(f.color) - min(f.color) < 30 and np.mean(f.color) > 90 else "black" if np.mean(f.color) < 90 else "wood"

    if f.label in ("cabinet", "kitchen island"):
        kind = "upper" if lo[1] > floor_y + 1.1 else "tall" if hi[1] > floor_y + 1.6 else "base"
        if kind == "upper":
            if ceiling_y - hi[1] < 0.3:
                hi[1] = ceiling_y
            if back:
                lo, hi = _inset(lo, hi, back, STANDARD_DEPTH_M["upper"]) if (hi - lo)[back[0]] > 0.45 else (lo, hi)
            add("cabinet", _box(lo, hi))
            return parts
        lo[1] = floor_y
        if kind == "tall":
            add("cabinet", _box(lo, hi))
            return parts
        top = float(np.clip(hi[1], floor_y + 0.86, floor_y + 0.97))
        body_lo, body_hi = lo.copy(), hi.copy()
        body_lo[1], body_hi[1] = floor_y + TOE_KICK_M[0], top - COUNTER_M
        kick_lo, kick_hi = lo.copy(), hi.copy()
        kick_hi[1] = floor_y + TOE_KICK_M[0]
        counter_lo, counter_hi = lo.copy(), hi.copy()
        counter_lo[1], counter_hi[1] = top - COUNTER_M, top
        axes = [back[0]] if back else [0, 2]
        for axis in axes:
            sides = [-back[1]] if back else [1, -1]
            for s in sides:
                if s > 0:
                    kick_hi[axis] -= TOE_KICK_M[1]
                    counter_hi[axis] += 0.025
                else:
                    kick_lo[axis] += TOE_KICK_M[1]
                    counter_lo[axis] -= 0.025
        add("cabinet", _box(body_lo, body_hi))
        add("dark", _box(kick_lo, kick_hi))
        add("counter", _box(counter_lo, counter_hi))
        return parts

    if f.label == "sink":
        top = lo[1] if hi[1] - lo[1] < 0.15 else hi[1]
        add("steel", _box([lo[0], top - 0.18, lo[2]], [hi[0], top + 0.002, hi[2]]))
        return parts

    if f.label == "range hood":
        w_lo, w_hi = lo.copy(), hi.copy()
        if back:
            w_lo, w_hi = _inset(w_lo, w_hi, back, 0.5)
        canopy = _box([w_lo[0], w_lo[1], w_lo[2]], [w_hi[0], w_lo[1] + 0.12, w_hi[2]])
        chimney_lo, chimney_hi = (w_lo + w_hi) / 2 - 0.15, (w_lo + w_hi) / 2 + 0.15
        if back:
            a, s = back
            chimney_lo[a], chimney_hi[a] = ((w_hi[a] - 0.3, w_hi[a]) if s > 0 else (w_lo[a], w_lo[a] + 0.3))
        chimney_lo[1], chimney_hi[1] = w_lo[1] + 0.12, ceiling_y
        tapered = _frustum(w_lo, [w_hi[0], w_lo[1] + 0.12 + 0.3, w_hi[2]], chimney_lo, chimney_hi)
        add(color if color != "wood" else "black", canopy)
        add(color if color != "wood" else "black", tapered)
        add(color if color != "wood" else "black", _box(chimney_lo, chimney_hi))
        return parts

    if f.label == "microwave" and lo[1] > floor_y + 0.5:
        add(color if color != "wood" else "black", _box(lo, hi))
        return parts

    # floor-standing appliances
    lo[1] = floor_y
    if f.label == "refrigerator":
        hi[1] = max(hi[1], floor_y + 1.7)
    elif f.label in ("stove", "dishwasher", "microwave"):
        hi[1] = floor_y + 0.91 if f.label == "stove" else floor_y + 0.88
    if back and f.label in STANDARD_DEPTH_M:
        lo, hi = _inset(lo, hi, back, max(STANDARD_DEPTH_M[f.label], 0.3))
    mat = "black" if color == "black" else "steel"
    add(mat, _box(lo, hi))
    axis, out, face = _front(lo, hi, back)
    other = 2 if axis == 0 else 0
    width = hi[other] - lo[other]

    def on_front(a0, a1, y0, y1, proud, mat_name):
        p_lo, p_hi = lo.copy(), hi.copy()
        p_lo[other], p_hi[other] = lo[other] + a0 * width, lo[other] + a1 * width
        p_lo[1], p_hi[1] = y0, y1
        p_lo[axis], p_hi[axis] = (face, face + proud) if out > 0 else (face - proud, face)
        add(mat_name, _box(p_lo, p_hi))

    if f.label == "refrigerator":
        top = hi[1]
        on_front(0.497, 0.503, floor_y + 0.75, top - 0.02, 0.004, "dark")         # french door seam
        on_front(0.0, 1.0, floor_y + 0.72, floor_y + 0.75, 0.004, "dark")        # freezer drawer gap
        on_front(0.44, 0.46, floor_y + 0.95, top - 0.25, 0.05, "steel")           # handles
        on_front(0.54, 0.56, floor_y + 0.95, top - 0.25, 0.05, "steel")
        on_front(0.25, 0.75, floor_y + 0.55, floor_y + 0.58, 0.05, "steel")
    elif f.label == "stove":
        add("dark", _box([lo[0], hi[1] - 0.02, lo[2]], [hi[0], hi[1] + 0.01, hi[2]]))
        on_front(0.12, 0.88, floor_y + 0.3, floor_y + 0.62, 0.004, "dark")          # oven window
        on_front(0.1, 0.9, floor_y + 0.68, floor_y + 0.7, 0.04, "steel")            # handle
    elif f.label == "dishwasher":
        on_front(0.1, 0.9, hi[1] - 0.1, hi[1] - 0.085, 0.03, "steel")
    return parts


def _frustum(b_lo, b_hi, t_lo, t_hi) -> "trimesh.Trimesh":
    """Tapered solid from the rectangle at b_lo[1] (x/z extent of b) up to the rectangle of t at b_hi[1]."""
    import trimesh

    y0, y1 = b_lo[1], b_hi[1]
    bottom = [(b_lo[0], y0, b_lo[2]), (b_hi[0], y0, b_lo[2]), (b_hi[0], y0, b_hi[2]), (b_lo[0], y0, b_hi[2])]
    top = [(t_lo[0], y1, t_lo[2]), (t_hi[0], y1, t_lo[2]), (t_hi[0], y1, t_hi[2]), (t_lo[0], y1, t_hi[2])]
    return trimesh.convex.convex_hull(np.array(bottom + top, float))


def build_parts(twin: dict, fittings: list[Fitting], levels: dict) -> dict[str, list]:
    """All fittings' meshes keyed by material, in the interior frame."""
    from shapely import contains_xy
    from shapely.geometry import Polygon

    floor0 = sorted(twin["floors"], key=lambda f: f["index"])[0]["floor_id"]
    faces = _wall_faces(twin, floor0)
    rooms = [r for r in twin["rooms"] if r["floor_id"] == floor0]
    parts: dict[str, list] = {}
    for f in fittings:
        cx, cz = (f.lo[0] + f.hi[0]) / 2, (f.lo[2] + f.hi[2]) / 2
        room = next((r for r in rooms if contains_xy(Polygon(r["polygon"]), cx, cz)), None)
        lv = levels.get(room["room_id"]) if room else None
        floor_y = lv.floor_y if lv else 0.0
        ceiling_y = lv.ceiling_at(cx, cz) if lv else floor_y + 2.6
        back = None if f.label in ("kitchen island", "sink") else snap_to_wall(f.lo, f.hi, faces)
        for mat, meshes in model(f, back, floor_y, ceiling_y).items():
            parts.setdefault(mat, []).extend(meshes)
    return parts


def fittings_path(model_id: str) -> Path:
    return work_dir(model_id) / "fittings.json"


def save_fittings(model_id: str, fittings: list[Fitting]) -> None:
    fittings_path(model_id).write_text(json.dumps([
        {"label": f.label, "lo": [round(float(v), 3) for v in f.lo], "hi": [round(float(v), 3) for v in f.hi],
         "color": list(f.color), "voxels": f.voxels, "sweeps": f.sweeps} for f in fittings], indent=1), encoding="utf-8")


def load_fittings(model_id: str) -> list[Fitting]:
    path = fittings_path(model_id)
    if not path.exists():
        return []
    return [Fitting(d["label"], np.array(d["lo"]), np.array(d["hi"]), tuple(d["color"]), d["voxels"], d["sweeps"])
            for d in json.loads(path.read_text(encoding="utf-8"))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    dt = sub.add_parser("detect")
    dt.add_argument("property_id")
    dt.add_argument("--rooms", nargs="+", default=["Kitchen"])
    dt.add_argument("--refresh", action="store_true")
    dt.add_argument("--sweeps", nargs="*", help="only these sweep ids")
    fu = sub.add_parser("fuse", help="detections -> fittings.json (then rebuild the structure)")
    fu.add_argument("property_id")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    twin = json.loads((fixtures_dir() / "twin" / f"{args.property_id}.json").read_text(encoding="utf-8"))
    model_id = twin["sources"]["matterport_model_id"]
    if args.cmd == "detect":
        detect(twin, model_id, args.rooms, args.refresh, args.sweeps)
    elif args.cmd == "fuse":
        fittings = fuse(load_detections(model_id))
        save_fittings(model_id, fittings)
        for f in sorted(fittings, key=lambda f: (f.label, f.lo[0])):
            size = f.hi - f.lo
            print(f"  {f.label:15s} at ({f.lo[0]:.2f}, {f.lo[1]:.2f}, {f.lo[2]:.2f})  size {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} m"
                  f"  {f.voxels} voxels, {f.sweeps} sweeps, color {f.color}")


if __name__ == "__main__":
    main()
