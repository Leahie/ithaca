"""Defurnished interior: Matterport's defurnished panoramas painted onto a clean shell from the twin.

Matterport's "defurnished" view has furniture-free panoramas for every scan point, but its mesh is
not downloadable (the API hands out one ~800-triangle chunk of a streamed mesh, and the signed
link opens nothing beside it) and there is no defurnished point cloud. So the geometry here is
built from the twin instead, and only the photos come from Matterport.

Geometry (interior frame, Y up, meters, the same frame as interior.glb):
  - per room: floor, flat ceiling (at height_m) and walls, inset from the wall centerlines by half
    the room's wall thickness; interior doors are cut through, with jambs and a head joining the
    two rooms; stairwells (floor_openings) are cut from the floors above and ceilings below.
  - windows and exterior doors stay painted on the wall (they show the photo of the outside).
Texture: every surface is a planar chart in an atlas. Each texel blends the scan points in its
room that face it, favouring near and head-on views; a 2D line-of-sight test inside the room
keeps L-shaped rooms from painting around corners; views steeply down are avoided (the tripod
patch under each camera is blurred). Ceilings get their own atlas so a viewer can hide them.

Not modelled: built-in fixtures (kitchen island, stairs, vanities) are painted flat onto the floor
and walls, sloped ceilings are flat, spaces the twin has no room for are open.

Needs twin v7+ (walls, floor_openings, Sweep.rotation). This branch's fixture is older; take
stream C's:  git show origin/stream-c/twin-v0:fixtures/twin/onXQLXhfyQv.json > /tmp/twin_v7.json

    python -m services.api.twin.defurnished download onXQLXhfyQv
    python -m services.api.twin.defurnished build onXQLXhfyQv --twin /tmp/twin_v7.json

View: python -m http.server 8765  (from the repo root), then open
    http://127.0.0.1:8765/services/api/twin/static/defurnished.html?pid=onXQLXhfyQv
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import requests
import shapely
import trimesh
from PIL import Image
from scipy import ndimage
from scipy.spatial.transform import Rotation
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

from services.api.integrations.config import fixtures_dir
from services.api.integrations.matterport import MatterportClient, MatterportError
from services.api.twin.mesh import RAW_DIR

log = logging.getLogger(__name__)

DEFURNISHED_VIEW = "matterport.model.defurnished"
SKYBOX_RES = "2k"  # the largest the defurnished view offers (2048 px faces; "high" is 1024)
DOWNLOAD_THREADS = 12

PAD_PX = 3            # texels computed around each chart, so filtering never reads a neighbour
VIS_STRIDE = 6        # line of sight is tested on every 6th texel in each direction
TOP_K = 3             # scan points blended per texel at most
SHARPNESS = 6.0       # how quickly a worse view loses weight against the best one
MIN_COS = 0.15        # grazing views count as this head-on at worst
NADIR_COS = 0.8       # rays steeper than ~37 degrees below horizontal start to be penalised
NADIR_PENALTY = 6.0


def raw_dir(model_id: str) -> Path:
    return RAW_DIR / model_id / "defurnished"


def glb_path(property_id: str) -> Path:
    return fixtures_dir() / "blobs" / property_id / "interior_defurnished.glb"


# --- download --------------------------------------------------------------------------------

def _model(client: MatterportClient, model_id: str, selection: str) -> dict:
    """An uncached query (the answers hold signed links that expire)."""
    body = client._post({"query": "query($id: ID!){ model(id: $id){ %s } }" % selection, "variables": {"id": model_id}})
    model = (body.get("data") or {}).get("model")
    if not model:
        raise MatterportError(f"model {model_id} not found or not visible to this key")
    return model


def download(model_id: str, client: MatterportClient | None = None) -> Path:
    """The defurnished view's skyboxes (Matterport face order 0 up, 1-4 sides, 5 down) and a manifest."""
    client = client or MatterportClient()
    views = _model(client, model_id, "views{id type}")["views"] or []
    view_id = next((v["id"] for v in views if v["type"] == DEFURNISHED_VIEW), None)
    if view_id is None:
        raise MatterportError(f"model {model_id} has no defurnished view")
    panos = _model(client, view_id, 'panoLocations{id label position{x y z} rotation{x y z w} skybox(resolution: "%s"){children}}'
                   % SKYBOX_RES)["panoLocations"]
    out = raw_dir(model_id)
    faces_dir = out / "panos" / SKYBOX_RES
    faces_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(faces_dir / f"{p['id']}_skybox{i}.jpg", url) for p in panos for i, url in enumerate(p["skybox"]["children"])]

    def fetch(job):
        path, url = job
        if not path.exists():
            r = requests.get(url, timeout=120)
            r.raise_for_status()
            path.with_suffix(".part").write_bytes(r.content)
            path.with_suffix(".part").replace(path)
        return path.stat().st_size

    with ThreadPoolExecutor(DOWNLOAD_THREADS) as pool:
        total = sum(pool.map(fetch, jobs))
    manifest = {
        "model_id": model_id, "view_id": view_id, "view_type": DEFURNISHED_VIEW, "resolution": SKYBOX_RES,
        "frame": "position and rotation in the Matterport model frame (Z up); rotation is Matterport's own",
        "faces": "skybox0..5 in Matterport order: 0 up, 1-4 sides turning right, 5 down",
        "sweeps": [{"id": p["id"], "label": p["label"], "position": p["position"], "rotation": p["rotation"],
                    "files": [f"panos/{SKYBOX_RES}/{p['id']}_skybox{i}.jpg" for i in range(len(p["skybox"]["children"]))]}
                   for p in panos],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    log.info("%d defurnished faces (%.0f MB) in %s", len(jobs), total / 1e6, faces_dir)
    return out


# --- panorama sampling (as services/api/twin/panos.py on stream-c, verified against the point cloud)

def sample_matterport(faces: list[np.ndarray], local: np.ndarray) -> np.ndarray:
    """Bilinear colors (N, 3) of pano-frame directions (N, 3) from Matterport's six faces."""
    x, y, z = local.T
    ax = np.abs(local)
    dom = np.argmax(ax, axis=1)
    m = np.maximum(ax.max(axis=1), 1e-9)
    picks = (
        (1, (dom == 2) & (z < 0), x / m, -y / m),
        (2, (dom == 0) & (x > 0), z / m, -y / m),
        (3, (dom == 2) & (z > 0), -x / m, -y / m),
        (4, (dom == 0) & (x < 0), -z / m, -y / m),
        (0, (dom == 1) & (y > 0), x / m, -z / m),
        (5, (dom == 1) & (y < 0), x / m, z / m),
    )
    out = np.zeros((len(local), 3), np.float32)
    for f, mask, s, t in picks:
        if not mask.any():
            continue
        img = faces[f]
        n = img.shape[0]
        fx = np.clip((s[mask] + 1) / 2 * n - 0.5, 0, n - 1.001)
        fy = np.clip((t[mask] + 1) / 2 * n - 0.5, 0, n - 1.001)
        x0, y0 = fx.astype(int), fy.astype(int)
        wx, wy = (fx - x0)[:, None], (fy - y0)[:, None]
        out[mask] = (img[y0, x0] * (1 - wx) * (1 - wy) + img[y0, x0 + 1] * wx * (1 - wy)
                     + img[y0 + 1, x0] * (1 - wx) * wy + img[y0 + 1, x0 + 1] * wx * wy)
    return out


# --- charts ----------------------------------------------------------------------------------

@dataclass
class Chart:
    """A planar surface: points are origin + a * axis_a + b * axis_b for (a, b) in poly."""

    kind: str                 # floor | ceiling | wall | jamb
    origin: np.ndarray
    axis_a: np.ndarray
    axis_b: np.ndarray
    normal: np.ndarray        # the visible side
    poly: Polygon
    rooms: list[str]          # rooms whose scan points may paint it
    floor_id: str
    a0: float = 0.0
    b0: float = 0.0
    w: int = 0
    h: int = 0
    page: int = 0
    x: int = 0
    y: int = 0
    cands: list[int] = field(default_factory=list)
    weights: np.ndarray | None = None   # (h * w, len(cands))

    def size(self, d: float) -> None:
        a_min, b_min, a_max, b_max = self.poly.bounds
        self.a0 = a_min - PAD_PX / d
        self.b0 = b_min - PAD_PX / d
        self.w = int(math.ceil((a_max - a_min) * d)) + 2 * PAD_PX
        self.h = int(math.ceil((b_max - b_min) * d)) + 2 * PAD_PX

    def texel_points(self, d: float) -> np.ndarray:
        a = self.a0 + (np.arange(self.w) + 0.5) / d
        b = self.b0 + (np.arange(self.h) + 0.5) / d
        aa, bb = np.meshgrid(a, b)
        return (self.origin + aa.reshape(-1, 1) * self.axis_a + bb.reshape(-1, 1) * self.axis_b).astype(np.float32)


def _polys(g) -> list[Polygon]:
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon)]


def _door(o: dict):
    """A door's ends in (x, z), its direction along the wall, and its bottom and top."""
    pts = np.array([[p["x"], p["y"], p["z"]] for p in o["polygon"]])
    xz = pts[:, [0, 2]]
    c = xz.mean(0)
    u = np.linalg.svd(xz - c)[2][0]
    s = (xz - c) @ u
    return c + s.min() * u, c + s.max() * u, u, float(pts[:, 1].min()), float(pts[:, 1].max())


def build_charts(twin: dict) -> list[Chart]:
    rooms = {r["room_id"]: r for r in twin["rooms"]}
    floors = {f["floor_id"]: f for f in twin["floors"]}
    wall_t: dict[str, list[float]] = {}
    door_t: dict[str, float] = {}
    for w in twin["walls"]:
        for rid in w["room_ids"]:
            wall_t.setdefault(rid, []).append(w["thickness_m"])
        for oid in w.get("opening_ids") or []:
            door_t[oid] = w["thickness_m"]
    half = {rid: float(np.median(v)) / 2 for rid, v in wall_t.items()}
    cut_doors = [o for o in twin["openings"]
                 if o["kind"] == "door" and o["room_id"] in rooms and o.get("other_room_id") in rooms]
    lowest = min(floors.values(), key=lambda f: f["index"])["floor_id"]
    holes = unary_union([Polygon(fo["polygon"]) for fo in twin.get("floor_openings") or []])

    up = np.array([0.0, 1.0, 0.0])
    x_axis, z_axis = np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])
    charts: list[Chart] = []
    for rid, r in rooms.items():
        base = Polygon(r["polygon"]).buffer(0)
        h = half.get(rid, 0.06)
        inset = base.buffer(-h, join_style="mitre")
        parts = _polys(inset)
        if not parts or inset.area < 0.5 * base.area:  # too thin to inset: keep the centerlines
            parts, h = _polys(base), 0.0
        fy, H = r["floor_y"], r["height_m"]
        for P in parts:
            floor_poly = P if r["floor_id"] == lowest else P.difference(holes)
            ceil_poly = P.difference(holes) if r["floor_id"] == lowest else P
            for g in _polys(floor_poly):
                charts.append(Chart("floor", np.array([0, fy, 0.0]), x_axis, z_axis, up, g, [rid], r["floor_id"]))
            for g in _polys(ceil_poly):
                charts.append(Chart("ceiling", np.array([0, fy + H, 0.0]), x_axis, z_axis, -up, g, [rid], r["floor_id"]))
            for ring in [P.exterior, *P.interiors]:
                coords = np.array(ring.coords)[:, :2]
                for a, b in zip(coords[:-1], coords[1:]):
                    L = float(np.linalg.norm(b - a))
                    if L < 0.02:
                        continue
                    u = (b - a) / L
                    n2 = np.array([-u[1], u[0]])
                    if not P.buffer(1e-6).contains(shapely.Point(*((a + b) / 2 + 0.01 * n2))):
                        n2 = -n2
                    face = box(0.0, fy, L, fy + H)
                    for o in cut_doors:
                        if rid not in (o["room_id"], o["other_room_id"]):
                            continue
                        e0, e1, du, y0, y1 = _door(o)
                        if abs(du[0] * u[1] - du[1] * u[0]) > 0.2 or abs(((e0 + e1) / 2 - a) @ n2) > h + 0.15:
                            continue
                        s0, s1 = sorted([(e0 - a) @ u, (e1 - a) @ u])
                        if s1 < -0.05 or s0 > L + 0.05:
                            continue
                        face = face.difference(box(s0, min(y0, fy - 0.01), s1, y1))
                    for g in _polys(face):
                        charts.append(Chart("wall", np.array([a[0], 0.0, a[1]]), np.array([u[0], 0.0, u[1]]), up,
                                            np.array([n2[0], 0.0, n2[1]]), g, [rid], r["floor_id"]))

    for o in cut_doors:  # jambs and head, through the wall's thickness
        e0, e1, du, y0, y1 = _door(o)
        t = door_t.get(o["opening_id"], 0.12) / 2 + 0.01
        across = np.array([-du[1], 0.0, du[0]])
        along = np.array([du[0], 0.0, du[1]])
        both = [o["room_id"], o["other_room_id"]]
        fid = rooms[o["room_id"]]["floor_id"]
        for e, inward in ((e0, along), (e1, -along)):
            charts.append(Chart("jamb", np.array([e[0], 0.0, e[1]]) - t * across, across, up, inward,
                                box(0, y0, 2 * t, y1), both, fid))
        width = float(np.linalg.norm(e1 - e0))
        charts.append(Chart("jamb", np.array([e0[0], y1, e0[1]]) - t * across, along, across, -up,
                            box(0, 0, width, 2 * t), both, fid))
    return charts


# --- which scan points paint which texel ------------------------------------------------------

def assign(charts: list[Chart], twin: dict, d: float) -> None:
    rooms = {r["room_id"]: r for r in twin["rooms"]}
    sweeps = twin["sweeps"]
    eyes = np.array([[s["position"][k] for k in "xyz"] for s in sweeps], np.float32)
    by_room: dict[str, list[int]] = {}
    for i, s in enumerate(sweeps):
        by_room.setdefault(s["room_id"], []).append(i)
    regions = {rid: Polygon(r["polygon"]).buffer(0.12) for rid, r in rooms.items()}

    for ch in charts:
        cand = sorted({i for rid in ch.rooms for i in by_room.get(rid, [])})
        region = unary_union([regions[rid] for rid in ch.rooms])
        if not cand:  # a room with no scan point: those standing in it, else the nearest on its floor
            same = [i for i, s in enumerate(sweeps) if s["floor_id"] == ch.floor_id]
            cand = [i for i in same if region.buffer(0.4).contains(shapely.Point(eyes[i, 0], eyes[i, 2]))]
            if not cand:
                c = np.array(ch.poly.centroid.coords[0])
                ctr = ch.origin + c[0] * ch.axis_a + c[1] * ch.axis_b
                cand = sorted(same, key=lambda i: float(np.linalg.norm(eyes[i] - ctr)))[:3]
            region = None
        ch.cands = cand
        P = ch.texel_points(d)
        score = np.full((len(P), len(cand)), np.inf, np.float32)
        facing = np.full_like(score, np.inf)
        gi, gj = np.arange(0, ch.h, VIS_STRIDE), np.arange(0, ch.w, VIS_STRIDE)
        coarse = P[(gi[:, None] * ch.w + gj[None, :]).reshape(-1)]
        rows = np.minimum(np.arange(ch.h) // VIS_STRIDE, len(gi) - 1)
        cols = np.minimum(np.arange(ch.w) // VIS_STRIDE, len(gj) - 1)
        for k, i in enumerate(cand):
            v = eyes[i] - P
            dist = np.maximum(np.linalg.norm(v, axis=1), 1e-6)
            cos = (v @ ch.normal) / dist
            nadir = np.clip((v[:, 1] / dist - NADIR_COS) / (1 - NADIR_COS), 0, 1)  # 1 = straight below the camera
            sc = dist / np.maximum(cos, MIN_COS) * (1 + NADIR_PENALTY * nadir)
            front = cos > 0.02
            facing[:, k] = np.where(front, sc, np.inf)
            ok = front
            if region is not None:
                segs = shapely.linestrings(np.stack([np.broadcast_to(eyes[i, [0, 2]], (len(coarse), 2)), coarse[:, [0, 2]]], axis=1))
                seen = shapely.contains(region, segs).reshape(len(gi), len(gj))
                ok = front & seen[rows][:, cols].reshape(-1)
            score[:, k] = np.where(ok, sc, np.inf)
        # texels no scan point sees properly: facing only, then anything (nearest)
        none = ~np.isfinite(score).any(1)
        score[none] = facing[none]
        none = ~np.isfinite(score).any(1)
        for k, i in enumerate(cand):
            score[none, k] = np.linalg.norm(eyes[i] - P[none], axis=1)
        w = np.exp(-SHARPNESS * (score / score.min(1, keepdims=True) - 1.0))
        w[~np.isfinite(score)] = 0
        if len(cand) > TOP_K:
            w[w < -np.sort(-w, axis=1)[:, TOP_K - 1:TOP_K]] = 0
        ch.weights = (w / w.sum(1, keepdims=True)).astype(np.float32)


# --- atlas and mesh --------------------------------------------------------------------------

def pack(charts: list[Chart], page_px: int, first_page: int) -> int:
    """Shelf packing, tallest first; returns the number of pages used."""
    page, x, y, shelf = first_page, 0, 0, 0
    for c in sorted(charts, key=lambda c: -c.h):
        if c.w > page_px or c.h > page_px:
            raise ValueError(f"a {c.w}x{c.h} px surface does not fit a {page_px} px page; lower --px-per-m")
        if x + c.w > page_px:
            x, y, shelf = 0, y + shelf, 0
        if y + c.h > page_px:
            page, x, y, shelf = page + 1, 0, 0, 0
        c.page, c.x, c.y = page, x, y
        x += c.w
        shelf = max(shelf, c.h)
    return page - first_page + 1


def bake(charts: list[Chart], twin: dict, panos: Path, d: float, pages: int, page_px: int) -> list[np.ndarray]:
    manifest = json.loads((panos / "manifest.json").read_text(encoding="utf-8"))
    files = {s["id"]: s["files"] for s in manifest["sweeps"]}
    sweeps = twin["sweeps"]
    acc = [np.zeros((page_px, page_px, 3), np.float32) for _ in range(pages)]
    filled = [np.zeros((page_px, page_px), bool) for _ in range(pages)]
    uses: dict[int, list[tuple[Chart, int]]] = {}
    for ch in charts:
        for k, i in enumerate(ch.cands):
            uses.setdefault(i, []).append((ch, k))
    for i, chart_uses in sorted(uses.items()):
        s = sweeps[i]
        if s["sweep_id"] not in files:
            raise KeyError(f"sweep {s['sweep_id']} is not in {panos}/manifest.json; run download again")
        faces = [np.asarray(Image.open(panos / f).convert("RGB"), np.float32) for f in files[s["sweep_id"]]]
        eye = np.array([s["position"][k] for k in "xyz"], np.float32)
        R = Rotation.from_quat([s["rotation"][k] for k in "xyzw"]).as_matrix().astype(np.float32)  # pano -> interior
        for ch, k in chart_uses:
            m = ch.weights[:, k] > 1e-3
            if not m.any():
                continue
            colors = sample_matterport(faces, (ch.texel_points(d)[m] - eye) @ R)
            idx = np.flatnonzero(m)
            rr, cc = idx // ch.w + ch.y, idx % ch.w + ch.x
            acc[ch.page][rr, cc] += colors * ch.weights[m, k, None]
            filled[ch.page][rr, cc] = True
    out = []
    for a, f in zip(acc, filled):
        if f.any() and not f.all():  # bleed into unused atlas space so mipmaps don't pull in black
            _, (ri, ci) = ndimage.distance_transform_edt(~f, return_indices=True)
            a = a[ri, ci]
        out.append(np.clip(a, 0, 255).astype(np.uint8))
    return out


def _triangulate(ch: Chart, d: float, page_px: int):
    verts2, faces = trimesh.creation.triangulate_polygon(ch.poly.simplify(0.0005), engine="earcut")
    v3 = ch.origin + verts2[:, :1] * ch.axis_a + verts2[:, 1:2] * ch.axis_b
    tri = v3[faces]
    flip = (np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]) @ ch.normal) < 0
    faces[flip] = faces[flip][:, ::-1]
    px = (verts2[:, 0] - ch.a0) * d + ch.x
    py = (verts2[:, 1] - ch.b0) * d + ch.y
    return v3, faces, np.stack([px / page_px, 1.0 - py / page_px], axis=1)


def _jpeg(img: np.ndarray, quality: int = 88) -> Image.Image:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf)  # format == "JPEG", so the glTF exporter keeps it as JPEG


def build(twin: dict, panos: Path, out: Path, px_per_m: float = 80, page_px: int = 4096) -> dict:
    missing = [k for k in ("walls", "floor_openings") if k not in twin] + \
              (["sweeps[].rotation"] if twin["sweeps"] and "rotation" not in twin["sweeps"][0] else [])
    if missing:
        raise ValueError(f"twin v{twin.get('version')} lacks {', '.join(missing)}; use twin v7+ (see module docstring)")
    d = px_per_m
    charts = build_charts(twin)
    for c in charts:
        c.size(d)
    assign(charts, twin, d)
    groups = {"shell": [c for c in charts if c.kind != "ceiling"], "ceiling": [c for c in charts if c.kind == "ceiling"]}
    pages: dict[str, list[int]] = {}
    total = 0
    for name, cs in groups.items():
        n = pack(cs, page_px, total)
        pages[name] = list(range(total, total + n))
        total += n
    images = bake(charts, twin, panos, d, total, page_px)

    scene = trimesh.Scene()
    for name, cs in groups.items():
        for p in pages[name]:
            vs, fs, uvs, off = [], [], [], 0
            for v, f, uv in (_triangulate(c, d, page_px) for c in cs if c.page == p):
                vs.append(v)
                fs.append(f + off)
                uvs.append(uv)
                off += len(v)
            material = trimesh.visual.material.PBRMaterial(baseColorTexture=_jpeg(images[p]), metallicFactor=0.0,
                                                           roughnessFactor=1.0, name=f"{name}_{p}")
            mesh = trimesh.Trimesh(np.concatenate(vs), np.concatenate(fs), process=False,
                                   visual=trimesh.visual.TextureVisuals(uv=np.concatenate(uvs), material=material))
            scene.add_geometry(mesh, geom_name=f"{name}_{p}", node_name=f"{name}_{p}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(scene.export(file_type="glb"))
    return {
        "surfaces": {k: sum(c.kind == k for c in charts) for k in ("floor", "ceiling", "wall", "jamb")},
        "triangles": int(sum(len(g.faces) for g in scene.geometry.values())),
        "pages": pages,
        "bytes": out.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    dl = sub.add_parser("download", help="the defurnished view's panoramas, into data/matterport/<model>/defurnished")
    dl.add_argument("model_id")
    bd = sub.add_parser("build", help="fixtures/blobs/<property>/interior_defurnished.glb")
    bd.add_argument("property_id")
    bd.add_argument("--twin", type=Path, help="twin v7+ JSON (default: fixtures/twin/<property_id>.json)")
    bd.add_argument("--matterport-model-id", help="defaults to the twin's sources.matterport_model_id")
    bd.add_argument("--px-per-m", type=float, default=80, help="texture density (80 = 1.25 cm texels)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.cmd == "download":
        print(f"wrote {download(args.model_id)}")
        return
    twin_path = args.twin or fixtures_dir() / "twin" / f"{args.property_id}.json"
    twin = json.loads(twin_path.read_text(encoding="utf-8"))
    model_id = args.matterport_model_id or twin["sources"]["matterport_model_id"]
    panos = raw_dir(model_id)
    if not (panos / "manifest.json").exists():
        raise SystemExit(f"no defurnished panoramas in {panos}; run: python -m services.api.twin.defurnished download {model_id}")
    t0 = time.time()
    out = glb_path(args.property_id)
    stats = build(twin, panos, out, args.px_per_m)
    print(f"wrote {out} ({stats['bytes'] / 1e6:.1f} MB) in {time.time() - t0:.0f} s")
    print(f"  {stats['triangles']} triangles, surfaces {stats['surfaces']}, atlas pages {stats['pages']}")


if __name__ == "__main__":
    main()
