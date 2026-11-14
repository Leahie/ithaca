"""Simplified building model: straight solid walls with real openings, floor slabs and ceilings.

Built from the twin's floor plan (wall centerlines and thicknesses, windows, doors, stairwells), not
from the scan's surface, so furniture, curtains and scan noise never become geometry. The MatterPak
point cloud is only used to measure heights:
  - each room's floor: the lowest strong peak of point heights near the twin's floor_y (reflections
    in shiny floors scatter below it without forming a peak, and pulled several twin values down);
  - each room's ceiling: a flat height, or a few planes for vaulted ceilings, fitted to the top
    surface seen in the room below the next floor up.
Walls run from below the floor slab up to the ceiling above them (following a vault's slope);
exterior walls under an upper floor rise to that floor so the facade is continuous.

Interior frame (Y up, meters), the same frame as interior.glb.

    python -m services.api.twin.structure build onXQLXhfyQv
View: python -m http.server 8765  (from the repo root), then open
    http://127.0.0.1:8765/services/api/twin/static/structure.html?pid=onXQLXhfyQv
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh
from scipy import ndimage
from shapely import contains_xy
from shapely.geometry import MultiPolygon, Polygon, box
from shapely.ops import unary_union

from services.api.integrations.config import fixtures_dir
from services.api.twin.cloud import iter_points, load_cloud
from services.api.twin.mesh import RAW_DIR

log = logging.getLogger(__name__)

SLAB_M = 0.25            # floor slab thickness under every room
CELL_M = 0.15            # ceiling height grid
MIN_CEILING_RISE_M = 1.8
FLAT_SHARE = 0.85        # a ceiling is flat when one horizontal plane holds this share of its cells
PLANE_TOL_M = 0.05
JOIN_TOL_M = 0.06        # wall ends this close to another wall are joined to it


def glb_path(property_id: str) -> Path:
    return fixtures_dir() / "blobs" / property_id / "structure.glb"


def levels_path(model_id: str) -> Path:
    return RAW_DIR / model_id / "structure_levels.json"


def roof_path(model_id: str) -> Path:
    return RAW_DIR / model_id / "structure_roof.json"


def _linear(hex_rgb: str, alpha: float = 1.0) -> list[float]:
    """glTF base colors are linear; write them as sRGB hex."""
    c = [int(hex_rgb[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    return [v ** 2.2 for v in c] + [alpha]


MATERIALS = {
    "wall_interior": _linear("#eeeae3"),
    "wall_exterior": _linear("#a8705a"),
    "ceiling": _linear("#f7f5f1"),
    "glass": _linear("#9fc3d8", 0.35),
    "door": _linear("#7a5a3c"),
    "floor_hardwood": _linear("#b48a5e"),
    "floor_tile": _linear("#d8d3ca"),
    "floor_carpet": _linear("#a89f92"),
    "floor_other": _linear("#c9c2b6"),
    "roof": _linear("#5d5a57"),
    "fit_cabinet": _linear("#c19a6b"),
    "fit_counter": _linear("#f1efea"),
    "fit_steel": _linear("#b8bbbe"),
    "fit_black": _linear("#2b2c2e"),
    "fit_dark": _linear("#1c1d1f"),
}
METALLIC = {"fit_steel": (0.7, 0.35), "fit_black": (0.3, 0.4)}  # (metallic, roughness)


# --- measuring heights ------------------------------------------------------------------------

@dataclass
class Plane:
    """y = a * x + b * z + c (interior frame)."""

    a: float
    b: float
    c: float

    def at(self, x, z):
        return self.a * np.asarray(x) + self.b * np.asarray(z) + self.c

    @property
    def flat(self) -> bool:
        return self.a == 0.0 and self.b == 0.0


@dataclass
class RoomLevels:
    floor_y: float
    planes: list[Plane]
    facets: list[tuple[Polygon, int]]  # ceiling pieces: (plan polygon in (x, z), plane index)

    def ceiling_at(self, x: float, z: float) -> float:
        """Height of the ceiling piece at (or nearest to) (x, z)."""
        if len(self.planes) == 1:
            return float(self.planes[0].at(x, z))
        p = _pt((x, z))
        k = min(self.facets, key=lambda f: f[0].distance(p))[1]
        return float(self.planes[k].at(x, z))

    def to_json(self) -> dict:
        return {"floor_y": self.floor_y, "planes": [[p.a, p.b, p.c] for p in self.planes],
                "facets": [[list(poly.exterior.coords), k] for poly, k in self.facets]}

    @classmethod
    def from_json(cls, d: dict) -> "RoomLevels":
        return cls(d["floor_y"], [Plane(*p) for p in d["planes"]], [(Polygon(c), k) for c, k in d["facets"]])


def _strongest_peak(y: np.ndarray, lo: float, hi: float, bin_m: float = 0.01) -> float | None:
    if len(y) < 50:
        return None
    hist, edges = np.histogram(y, np.arange(lo, hi + bin_m, bin_m))
    hist = np.convolve(hist, np.ones(3) / 3, mode="same")
    return float(edges[int(np.argmax(hist))] + bin_m / 2)


def _lowest_layer(y: np.ndarray, bin_m: float = 0.03, share: float = 0.25) -> float:
    """Height of the lowest bin holding a fair share of a cell's points."""
    edges = np.arange(y.min(), y.max() + 2 * bin_m, bin_m)
    hist, _ = np.histogram(y, edges)
    dense = np.flatnonzero(hist >= max(3, share * hist.max()))
    if not len(dense):
        return float(np.median(y))
    i = int(dense[0])
    return float(np.median(y[(y >= edges[i]) & (y < edges[i + 1])]))


def _fit_planes(S: np.ndarray, rng: np.random.Generator, max_planes: int = 6, iters: int = 400,
                tol: float = PLANE_TOL_M, min_share: float = 0.12) -> list[tuple[Plane, np.ndarray]]:
    """Sequential RANSAC on (x, z, h) cell samples; returns planes with their inlier masks over S.

    For ceilings, pieces under min_share of the cells are shelves, soffits and lamps.
    """
    out: list[tuple[Plane, np.ndarray]] = []
    left = np.ones(len(S), bool)
    min_inliers = max(12, int(min_share * len(S)))
    while left.sum() >= min_inliers and len(out) < max_planes:
        X = S[left]
        best_n, best = 0, None
        for _ in range(iters):
            i = rng.choice(len(X), 3, replace=False)
            A = np.c_[X[i, 0], X[i, 1], np.ones(3)]
            if abs(np.linalg.det(A)) < 1e-6:
                continue
            coef = np.linalg.solve(A, X[i, 2])
            if np.hypot(coef[0], coef[1]) > 1.5:  # steeper than ~56 degrees is neither ceiling nor roof
                continue
            n = int((np.abs(X[:, :2] @ coef[:2] + coef[2] - X[:, 2]) < tol).sum())
            if n > best_n:
                best_n, best = n, coef
        if best is None or best_n < min_inliers:
            break
        res = np.abs(S[:, :2] @ best[:2] + best[2] - S[:, 2])
        inl = left & (res < tol)
        A = np.c_[S[inl, 0], S[inl, 1], np.ones(inl.sum())]
        coef = np.linalg.lstsq(A, S[inl, 2], rcond=None)[0]
        if np.hypot(coef[0], coef[1]) < 0.05:  # under ~3 degrees: level
            coef = np.array([0.0, 0.0, float(np.median(S[inl, 2]))])
        inl = left & (np.abs(S[:, :2] @ coef[:2] + coef[2] - S[:, 2]) < tol)
        out.append((Plane(*map(float, coef)), inl))
        left &= ~inl
    return out


def _polys(g) -> list[Polygon]:
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon) and p.area > 1e-4]


def _halfplane(p: Plane, q: Plane, keep_xz: tuple[float, float], size: float = 1e3) -> Polygon:
    """The side of the line where p and q meet that holds keep_xz."""
    da, db, dc = p.a - q.a, p.b - q.b, p.c - q.c
    n = np.array([da, db])
    k = np.linalg.norm(n)
    n /= k
    p0 = -dc / k * n
    t = np.array([-n[1], n[0]])
    side = 1.0 if (np.array(keep_xz) - p0) @ n >= 0 else -1.0
    a, b = p0 - size * t, p0 + size * t
    return Polygon([a, b, b + side * size * n, a + side * size * n])


def _cells_polygon(mask: np.ndarray, origin: np.ndarray, cell: float):
    ii, jj = np.nonzero(mask)
    return unary_union([box(*(origin + [i * cell, j * cell]), *(origin + [(i + 1) * cell, (j + 1) * cell]))
                        for i, j in zip(ii, jj)])


def _ceiling_facets(room_poly: Polygon, planes: list[Plane], labels: np.ndarray, origin: np.ndarray,
                    cell: float = CELL_M, simplify_m: float = 0.02) -> list[tuple[Polygon, int]]:
    """Plan pieces of each plane, with exact ridge, hip and valley lines between sloped planes."""
    # one piece per connected patch: a plane can show up on separate wings
    raw: list[tuple[Polygon, int]] = []
    for k in range(len(planes)):
        cells = _cells_polygon(labels == k, origin, cell)
        if not cells.is_empty:
            raw += [(g, k) for g in _polys(cells.intersection(room_poly)) if g.area > 4 * cell * cell]
    facets = []
    for n, (piece, k) in enumerate(raw):
        pk = planes[k]
        region = piece.buffer(0.4, join_style="mitre").intersection(room_poly)
        inside = piece.representative_point().coords[0]
        for m, (other, j) in enumerate(raw):
            if m == n or not piece.buffer(0.45).intersects(other):
                continue
            pj = planes[j]
            seam = piece.buffer(0.45).intersection(other.buffer(0.45)).representative_point()
            meet = abs(pk.at(seam.x, seam.y) - pj.at(seam.x, seam.y)) < 0.3
            if not meet or abs(pk.a - pj.a) + abs(pk.b - pj.b) < 0.02:  # a step: keep the scanned edge
                region = region.difference(other)
            else:
                # the meeting line only near the other piece: pieces that touch at a corner
                # must not cut each other end to end
                beyond = _halfplane(pj, pk, other.representative_point().coords[0])
                region = region.difference(beyond.intersection(other.buffer(0.8, join_style="mitre")))
        facets += [(g.simplify(simplify_m), k) for g in _polys(region.intersection(room_poly))]
    return facets


def measure_room(room: dict, points: np.ndarray, floor_low: float | None, cap_y: float | None,
                 rng: np.random.Generator) -> RoomLevels:
    """floor_low: the ceiling of the room below, which shows through the floor band; cap_y: the floor above."""
    poly = Polygon(room["polygon"]).buffer(0)
    inner = poly.buffer(-0.25)
    if inner.is_empty or inner.area < 0.3 * poly.area:
        inner = poly.buffer(-0.08)
    minx, minz, maxx, maxz = inner.bounds
    near = (points[:, 0] > minx) & (points[:, 0] < maxx) & (points[:, 2] > minz) & (points[:, 2] < maxz)
    P = points[near]
    P = P[contains_xy(inner, P[:, 0], P[:, 2])]

    # the floor is the strongest height peak near the twin's floor_y; reflections in shiny floors
    # only make a broad hump below it
    fy0 = room["floor_y"]
    lo = max(fy0 - 0.45, floor_low if floor_low is not None else -np.inf)
    band = P[(P[:, 1] > lo) & (P[:, 1] < fy0 + 0.45), 1]
    floor_y = _strongest_peak(band, lo, fy0 + 0.45)
    if floor_y is None:
        floor_y = fy0
    top = cap_y if cap_y is not None else floor_y + 7.0
    C = P[(P[:, 1] > floor_y + MIN_CEILING_RISE_M) & (P[:, 1] < top)]
    flat_default = Plane(0.0, 0.0, floor_y + room["height_m"])
    if len(C) < 200:
        return RoomLevels(floor_y, [flat_default], [(poly, 0)])

    pminx, pminz, pmaxx, pmaxz = poly.bounds
    origin = np.array([pminx, pminz])
    shape = np.ceil(np.array([pmaxx - pminx, pmaxz - pminz]) / CELL_M).astype(int) + 1
    ij = np.clip(np.floor((C[:, [0, 2]] - origin) / CELL_M).astype(int), 0, shape - 1)
    flat_idx = ij[:, 0] * shape[1] + ij[:, 1]
    order = np.argsort(flat_idx, kind="stable")
    fs, ys = flat_idx[order], C[order, 1]
    starts = np.r_[0, np.flatnonzero(np.diff(fs)) + 1]
    counts = np.diff(np.r_[starts, len(fs)])
    # the lowest dense layer in each cell: from inside, the ceiling hides everything above it, so
    # anything higher (the roof, scanned from the yard) is not the ceiling
    keep = counts >= 5
    tops = np.array([_lowest_layer(ys[s:s + c]) for s, c in zip(starts[keep], counts[keep])])
    cells = fs[starts[keep]]
    ci, cj = cells // shape[1], cells % shape[1]
    S = np.c_[origin[0] + (ci + 0.5) * CELL_M, origin[1] + (cj + 0.5) * CELL_M, tops]
    if len(S) < 20:
        return RoomLevels(floor_y, [flat_default], [(poly, 0)])

    fits = [(p, m) for p, m in _fit_planes(S, rng) if not (p.flat and p.c < floor_y + 2.0)]
    if not fits:
        return RoomLevels(floor_y, [Plane(0.0, 0.0, float(np.median(S[:, 2])))], [(poly, 0)])
    planes = [p for p, _ in fits]
    if planes[0].flat and fits[0][1].mean() >= FLAT_SHARE:
        return RoomLevels(floor_y, [planes[0]], [(poly, 0)])

    # every cell of the room gets the plane that fits it (or the nearest labelled cell's plane)
    gx = origin[0] + (np.arange(shape[0]) + 0.5) * CELL_M
    gz = origin[1] + (np.arange(shape[1]) + 0.5) * CELL_M
    labels = np.full(tuple(shape), -1)
    res = np.stack([np.abs(p.at(S[:, 0], S[:, 1]) - S[:, 2]) for p in planes], axis=1)
    lab = np.where(res.min(1) < 2 * PLANE_TOL_M, res.argmin(1), -1)
    labels[ci, cj] = lab
    inside = contains_xy(poly, *np.meshgrid(gx, gz, indexing="ij"))
    if (labels >= 0).any():
        _, (ri, rj) = ndimage.distance_transform_edt(labels < 0, return_indices=True)
        labels = np.where(labels >= 0, labels, labels[ri, rj])
    labels[~inside] = -1
    used = sorted(set(labels[labels >= 0].tolist()))
    remap = {k: n for n, k in enumerate(used)}
    planes = [planes[k] for k in used]
    labels = np.vectorize(lambda v: remap.get(v, -1))(labels)
    return RoomLevels(floor_y, planes, _ceiling_facets(poly, planes, labels, origin))


def measure(twin: dict, model_id: str) -> dict[str, RoomLevels]:
    points, _ = load_cloud(model_id)
    floors = sorted(twin["floors"], key=lambda f: f["index"])
    rooms_by_floor = {f["floor_id"]: [r for r in twin["rooms"] if r["floor_id"] == f["floor_id"]] for f in floors}
    rng = np.random.default_rng(0)
    out: dict[str, RoomLevels] = {}
    for n, f in enumerate(floors):
        above = rooms_by_floor[floors[n + 1]["floor_id"]] if n + 1 < len(floors) else []
        below = rooms_by_floor[floors[n - 1]["floor_id"]] if n > 0 else []
        for r in rooms_by_floor[f["floor_id"]]:
            poly = Polygon(r["polygon"]).buffer(0)
            over = [a for a in above if Polygon(a["polygon"]).buffer(0).intersection(poly).area > 0.2 * poly.area]
            cap = min(a["floor_y"] for a in over) - 0.1 if over else None
            c = poly.representative_point()
            under = [out[b["room_id"]].ceiling_at(c.x, c.y) for b in below
                     if b["room_id"] in out and Polygon(b["polygon"]).buffer(0).intersects(poly)]
            floor_low = max(under) + 0.08 if under else None
            lv = measure_room(r, points, floor_low, cap, rng)
            out[r["room_id"]] = lv
            log.info("%s %-12s floor %.2f (twin %.2f)  ceiling %s", f["label"], r["label"], lv.floor_y, r["floor_y"],
                     f"{lv.planes[0].c:.2f}" if len(lv.planes) == 1 else f"{len(lv.planes)} planes")
    return out


# --- roof from the Vexcel surface model -------------------------------------------------------

ROOF_CELL_M = 0.15
ROOF_TOL_M = 0.12        # DSM noise on shingles
ROOF_MIN_RISE_M = 2.0    # above the ground floor: lower is fences, cars and shrubs
ROOF_THICKNESS_M = 0.2


@dataclass
class Roof:
    planes: list[Plane]
    facets: list[tuple[Polygon, int]]
    # DSM -> interior: turned by -turn_deg about the plan centre, then shifted by -shift_xz
    shift_xz: tuple[float, float]
    offset_m: float                 # DSM height - interior y
    turn_deg: float = 0.0

    def at(self, x: float, z: float) -> float | None:
        p = _pt((x, z))
        near = [(poly.distance(p), k) for poly, k in self.facets]
        d, k = min(near, default=(np.inf, -1))
        return float(self.planes[k].at(x, z)) if d < 0.3 else None

    def to_json(self) -> dict:
        return {"planes": [[p.a, p.b, p.c] for p in self.planes], "shift_xz": list(self.shift_xz),
                "offset_m": self.offset_m, "turn_deg": self.turn_deg,
                "facets": [[list(poly.exterior.coords), [list(r.coords) for r in poly.interiors], k] for poly, k in self.facets]}

    @classmethod
    def from_json(cls, d: dict) -> "Roof":
        return cls([Plane(*p) for p in d["planes"]], [(Polygon(e, h), k) for e, h, k in d["facets"]],
                   tuple(d["shift_xz"]), d["offset_m"], d.get("turn_deg", 0.0))


def _dsm_interior(twin: dict, client) -> np.ndarray:
    """(N, 3) Vexcel DSM samples as interior (x, DSM height, z)."""
    from services.api.twin import frames

    dsm = client.dsm(twin["lat"], twin["lon"], 30.0)
    ok = np.isfinite(dsm.height)
    lat, lon = dsm.lat[ok], dsm.lon[ok]
    m, n = frames._radii(twin["lat"])
    east = np.radians(lon - twin["lon"]) * n * np.cos(np.radians(twin["lat"]))
    north = np.radians(lat - twin["lat"]) * m
    inv = frames.invert_rigid(frames.from_column_major(twin["interior_to_world"]))
    p = inv @ np.stack([east, north, np.zeros_like(east), np.ones_like(east)])
    return np.c_[p[0], dsm.height[ok], p[2]]


def _ground_offset(D: np.ndarray, model_id: str, plan, prior: float | None) -> float | None:
    """DSM height minus interior y, from open ground the outdoor sweeps scanned.

    Roof points do not work: from the yard the scanner sees eaves and patio covers from below,
    the DSM from above. Ground cells under a cover give a second, higher cluster; the mode near the
    Vexcel ground elevation is the ground.
    """
    from scipy.spatial import cKDTree

    house = plan.buffer(1.0)
    x0, z0, x1, z1 = plan.buffer(15).bounds
    low = [np.empty((0, 3), np.float32)]
    for c in iter_points(model_id):
        c = c[(c[:, 1] > -1.5) & (c[:, 1] < 1.0) & (c[:, 0] > x0) & (c[:, 0] < x1) & (c[:, 2] > z0) & (c[:, 2] < z1)]
        low.append(c[~contains_xy(house, c[:, 0], c[:, 2])])
    g = np.concatenate(low)
    if len(g) < 1000:
        return None
    # one ground height per 0.5 m cell: the strongest peak, where one surface dominates
    key = np.floor(g[:, [0, 2]] / 0.5).astype(np.int64)
    _, inv, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    order = np.argsort(inv.ravel(), kind="stable")
    cells = []
    for idx in np.split(order, np.cumsum(counts)[:-1]):
        if len(idx) < 200:
            continue
        h, e = np.histogram(g[idx, 1], bins=np.arange(-1.5, 1.0, 0.02))
        if h.max() >= 0.3 * len(idx):  # not shrubs or furniture
            cells.append((g[idx[0], 0], e[h.argmax()] + 0.01, g[idx[0], 2]))
    if len(cells) < 10:
        return None
    G = np.array(cells)
    _, nn = cKDTree(D[:, [0, 2]]).query(G[:, [0, 2]], k=36)
    local = D[nn, 1]
    flat = local.max(1) - local.min(1) < 0.15
    dd = np.median(local[flat], axis=1) - G[flat, 1]
    if prior is not None:
        dd = dd[np.abs(dd - prior) < 1.0]
    if len(dd) < 5:
        return None
    h, e = np.histogram(dd, bins=np.arange(dd.min() - 0.05, dd.max() + 0.1, 0.05))
    peak = e[h.argmax()] + 0.025
    offset = float(np.median(dd[np.abs(dd - peak) < 0.15]))
    log.info("roof: DSM height offset %.2f m from %d open ground cells", offset, len(dd))
    return offset


def _rotate(xz: np.ndarray, deg: float, center: np.ndarray) -> np.ndarray:
    t = np.radians(deg)
    r = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    return (xz - center) @ r.T + center


def _roof_turn(D: np.ndarray, plan, ground: float) -> float:
    """Degrees the DSM is turned against the twin, from roof slope directions.

    Roof faces slope square to the walls, which run along the interior axes, so slope directions
    folded into 90 degrees should sit at 0. The twin's georeferenced heading can be off by several
    degrees; this measures it on the roof itself.
    """
    from scipy.spatial import cKDTree

    step = 0.15
    x0, z0, x1, z1 = plan.buffer(1.0).bounds
    gx, gz = np.meshgrid(np.arange(x0, x1, step), np.arange(z0, z1, step), indexing="ij")
    dist, idx = cKDTree(D[:, [0, 2]]).query(np.c_[gx.ravel(), gz.ravel()])
    H = np.where(dist < 0.15, D[idx, 1], np.nan).reshape(gx.shape)
    H = np.where(H > ground + ROOF_MIN_RISE_M, H, np.nan)
    H = ndimage.median_filter(np.nan_to_num(H, nan=-99.0), size=5)
    dhx, dhz = np.gradient(H, step)
    slope = np.hypot(dhx, dhz)
    ok = (H > -50) & (slope > 0.15) & (slope < 1.2) & contains_xy(plan.buffer(0.5), gx, gz)
    if ok.sum() < 200:
        return 0.0
    phi = np.arctan2(dhz[ok], dhx[ok])
    turn = float(np.degrees(np.angle(np.sum(np.exp(4j * phi))) / 4))
    log.info("roof: DSM turned %.1f deg against the twin's walls (from %d sloped cells)", turn, ok.sum())
    return turn


def _plan_shift(D: np.ndarray, top_plan, eave_y: float) -> tuple[float, float]:
    """Plan shift that centres the DSM's top roof on the top floor's outline (eaves overhang evenly)."""
    from scipy.spatial import cKDTree

    step = 0.1
    x0, z0, x1, z1 = top_plan.buffer(4).bounds
    gx, gz = np.meshgrid(np.arange(x0, x1, step), np.arange(z0, z1, step), indexing="ij")
    dist, idx = cKDTree(D[:, [0, 2]]).query(np.c_[gx.ravel(), gz.ravel()])
    high = (np.where(dist < 0.12, D[idx, 1], -np.inf) > eave_y).reshape(gx.shape)
    best = (0.0, (0.0, 0.0))
    reach = int(round(1.0 / step))
    for b in (0.0, 0.2, 0.4, 0.6, 0.8):
        area = contains_xy(top_plan.buffer(b, join_style="mitre"), gx, gz)
        for i in range(-reach, reach + 1):
            for j in range(-reach, reach + 1):
                moved = np.roll(area, (i, j), axis=(0, 1))
                iou = (moved & high).sum() / max(1, (moved | high).sum())
                if iou > best[0]:
                    best = (iou, (round(i * step, 2), round(j * step, 2)))
    log.info("roof: plan shift %s m (outline overlap %.2f)", best[1], best[0])
    return best[1] if best[0] > 0.5 else (0.0, 0.0)


def measure_roof(twin: dict, model_id: str, levels: dict[str, RoomLevels], client=None) -> Roof:
    from scipy.spatial import cKDTree

    from services.api.integrations.vexcel import VexcelClient

    client = client or VexcelClient()
    D = _dsm_interior(twin, client)
    rooms = twin["rooms"]
    floors = sorted(twin["floors"], key=lambda f: f["index"])
    plan = unary_union([Polygon(r["polygon"]).buffer(0) for r in rooms])
    structure = client.primary_structure(twin["lat"], twin["lon"]) or {}

    ground = min(lv.floor_y for lv in levels.values())
    prior = structure.get("ground-elevation.value")
    offset = _ground_offset(D, model_id, plan, prior - ground if prior is not None else None)
    if offset is None:
        offset = float(prior) - ground if prior is not None else float(np.nanpercentile(D[:, 1], 5))
        log.warning("roof: no open ground in the scan; DSM height offset %.2f m from Vexcel", offset)
    D = np.c_[D[:, 0], D[:, 1] - offset, D[:, 2]]

    center = np.array(plan.centroid.coords[0])
    turn = _roof_turn(D, plan, ground)
    D = np.c_[_rotate(D[:, [0, 2]], -turn, center), D[:, 1]][:, [0, 2, 1]]

    top_rooms = [r for r in rooms if r["floor_id"] == floors[-1]["floor_id"] and r["room_id"] in levels]
    top_plan = unary_union([Polygon(r["polygon"]).buffer(0) for r in top_rooms])
    eave_y = float(np.median([levels[r["room_id"]].ceiling_at(*Polygon(r["polygon"]).representative_point().coords[0])
                              for r in top_rooms])) - 0.2
    shift = _plan_shift(D, top_plan, eave_y)
    D = np.c_[D[:, 0] - shift[0], D[:, 1], D[:, 2] - shift[1]]

    # the DSM resampled on a grid along the house's own axes
    region = plan.buffer(1.5)
    if structure and structure.get("footprint.wkt"):
        region = region.union(_footprint_interior(twin, structure["footprint.wkt"]).buffer(0.5))
    x0, z0, x1, z1 = region.bounds
    origin = np.array([x0, z0])
    shape = np.ceil(np.array([x1 - x0, z1 - z0]) / ROOF_CELL_M).astype(int) + 1
    gx = origin[0] + (np.arange(shape[0]) + 0.5) * ROOF_CELL_M
    gz = origin[1] + (np.arange(shape[1]) + 0.5) * ROOF_CELL_M
    GX, GZ = np.meshgrid(gx, gz, indexing="ij")
    dist, idx = cKDTree(D[:, [0, 2]]).query(np.c_[GX.ravel(), GZ.ravel()])
    H = np.where(dist < 0.15, D[idx, 1], np.nan).reshape(shape)
    ground = min(lv.floor_y for lv in levels.values())
    mask = (H > ground + ROOF_MIN_RISE_M) & contains_xy(region, GX, GZ)
    lab, n = ndimage.label(mask)
    touching = set(lab[contains_xy(plan, GX, GZ) & mask].tolist()) - {0}
    mask = np.isin(lab, list(touching))
    mask = ndimage.binary_closing(mask, iterations=2) & np.isfinite(H) | mask

    planes, labels = _roof_segments(GX, GZ, np.where(mask, H, np.nan))
    # trees and the chimney fit no plane: what is left is the roof
    keep = labels >= 0
    lab, n = ndimage.label(keep)
    sizes = ndimage.sum(keep, lab, range(1, n + 1))
    keep &= np.isin(lab, 1 + np.flatnonzero(sizes * ROOF_CELL_M ** 2 >= 2.0))
    labels[~keep] = -1
    # slivers of a plane along a ridge would cut the faces beside them on a false ridge line:
    # drop them and give their cells to the nearest real face
    for k in range(len(planes)):
        lab, n = ndimage.label(labels == k)
        sizes = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
        labels[np.isin(lab, 1 + np.flatnonzero(sizes * ROOF_CELL_M ** 2 < 1.5))] = -1
    # straight eaves: smooth the DSM's ragged roof edge before tracing it
    keep = ndimage.binary_closing(ndimage.binary_opening(keep, iterations=2), iterations=2)
    keep = ndimage.binary_fill_holes(keep)  # the roof runs on under the chimney
    if (labels >= 0).any():
        _, (ri, rj) = ndimage.distance_transform_edt(labels < 0, return_indices=True)
        labels = np.where(keep, labels[ri, rj], -1)
    outline = _cells_polygon(keep, origin, ROOF_CELL_M).buffer(0.1).buffer(-0.1).simplify(0.15)
    facets = []
    for g in _polys(outline):
        facets += _ceiling_facets(g, planes, labels, origin, ROOF_CELL_M, simplify_m=0.12)
    facets = _tile(outline, facets)
    log.info("roof: %d planes, %d facets, %.0f m2", len(planes), len(facets), outline.area)
    return Roof(planes, facets, shift, offset, turn)


def _tile(outline, facets: list[tuple[Polygon, int]]) -> list[tuple[Polygon, int]]:
    """Facets that cover the outline once: overlaps go to the larger facet, gaps to the neighbour
    sharing the longest edge, and touching facets on one plane become one."""
    taken = Polygon()
    pieces: list[tuple[Polygon, int]] = []
    for poly, k in sorted(facets, key=lambda f: -f[0].area):
        pieces += [(g, k) for g in _polys(poly.difference(taken)) if g.area > 0.02]
        taken = taken.union(poly)
    for gap in _polys(outline.difference(taken)):
        if gap.area < 0.02:
            continue
        edge = gap.boundary
        shared = [(edge.intersection(p.buffer(0.05)).length, k) for p, k in pieces]
        length, k = max(shared, default=(0.0, -1))
        if length > 0:
            pieces.append((gap, k))
    out = []
    for k in sorted({k for _, k in pieces}):
        merged = unary_union([p for p, j in pieces if j == k]).buffer(0.01).buffer(-0.01)
        out += [(g.simplify(0.05), k) for g in _polys(merged) if g.area > 0.05]
    return out


def _roof_segments(GX: np.ndarray, GZ: np.ndarray, H: np.ndarray) -> tuple[list[Plane], np.ndarray]:
    """Planar roof segments from local slopes; labels -1 = no plane (trees, edges)."""
    ok = np.isfinite(H)
    w = ok.astype(float)
    x, z, h = GX - GX[0, 0], GZ - GZ[0, 0], np.where(ok, H, 0.0)
    k = 7  # about 1 m
    m = {n: ndimage.uniform_filter(v * w, k, mode="constant")
         for n, v in dict(one=np.ones_like(x), x=x, z=z, h=h, xx=x * x, xz=x * z, zz=z * z, xh=x * h, zh=z * h, hh=h * h).items()}
    A = np.stack([np.stack([m["xx"], m["xz"], m["x"]], -1), np.stack([m["xz"], m["zz"], m["z"]], -1),
                  np.stack([m["x"], m["z"], m["one"]], -1)], -2)
    rhs = np.stack([m["xh"], m["zh"], m["h"]], -1)
    full = ok & (m["one"] > 0.6)
    coef = np.full(H.shape + (3,), np.nan)
    good = full & (np.abs(np.linalg.det(np.where(full[..., None, None], A, np.eye(3)))) > 1e-9)
    coef[good] = np.linalg.solve(A[good], rhs[good][..., None])[..., 0]
    a, b, c = coef[..., 0], coef[..., 1], coef[..., 2]
    mse = (m["hh"] - 2 * (a * m["xh"] + b * m["zh"] + c * m["h"]) + a * a * m["xx"] + 2 * a * b * m["xz"]
           + b * b * m["zz"] + 2 * a * c * m["x"] + 2 * b * c * m["z"] + c * c * m["one"]) / np.maximum(m["one"], 1e-9)
    planar = good & (np.sqrt(np.maximum(mse, 0)) < 0.05) & (np.hypot(a, b) < 1.5)

    # Group cells by their own slope, not by chaining neighbours: across a shallow ridge the local
    # slope changes in small steps and chaining would merge both faces into one flat plane.
    step = 0.02
    edges = np.arange(-1.5, 1.5 + step, step)
    hist, _, _ = np.histogram2d(a[planar], b[planar], bins=[edges, edges])
    hist = ndimage.gaussian_filter(hist, 1.5)
    peaks = np.argwhere((hist == ndimage.maximum_filter(hist, size=9)) & (hist * ROOF_CELL_M ** 2 >= 0.5))
    slopes = edges[peaks] + step / 2
    comp = np.full(H.shape, -1)
    if len(slopes):
        ab = np.c_[a[planar], b[planar]]
        d = np.linalg.norm(ab[:, None, :] - slopes[None], axis=2)
        group = np.where(d.min(1) < 0.12, d.argmin(1), -1)
        g = np.full(H.shape, -1)
        g[planar] = group
        n_comp = 0
        for k in range(len(slopes)):
            lab, n = ndimage.label(g == k)
            comp[lab > 0] = lab[lab > 0] + n_comp - 1
            n_comp += n
    ids, counts = np.unique(comp[comp >= 0], return_counts=True)
    seeds = ids[counts * ROOF_CELL_M ** 2 >= 2.5]

    planes: list[Plane] = []
    for s in seeds[np.argsort(-counts[np.isin(ids, seeds)])]:
        sel = comp == s
        X = np.c_[GX[sel], GZ[sel], np.ones(sel.sum())]
        fit = np.linalg.lstsq(X, H[sel], rcond=None)[0]
        p = Plane(float(fit[0]), float(fit[1]), float(fit[2]))
        # one plane per roof face: segments split by a vent or a valley join back up
        if not any(abs(p.a - q.a) < 0.05 and abs(p.b - q.b) < 0.05
                   and abs(p.at(GX[sel], GZ[sel]) - q.at(GX[sel], GZ[sel])).mean() < 0.1 for q in planes):
            planes.append(p)

    # every roof cell goes to the best-fitting plane among the segments near it
    labels = np.full(H.shape, -1)
    best = np.full(H.shape, np.inf)
    for n, p in enumerate(planes):
        near = (np.abs(p.at(GX, GZ) - H) < 0.06) & planar
        near = ndimage.binary_dilation(near, iterations=10)  # 1.5 m
        res = np.where(near & ok, np.abs(p.at(GX, GZ) - H), np.inf)
        better = res < best
        best[better], labels[better] = res[better], n
    labels[best > 0.3] = -1
    return planes, labels


def _footprint_interior(twin: dict, footprint_wkt: str) -> Polygon:
    from shapely import wkt as shapely_wkt

    from services.api.twin import frames

    inv = frames.invert_rigid(frames.from_column_major(twin["interior_to_world"]))
    pts = []
    for lon, lat in shapely_wkt.loads(footprint_wkt).exterior.coords:
        e, n = frames.latlon_to_enu(lat, lon, twin["lat"], twin["lon"])
        p = inv @ np.array([e, n, 0.0, 1.0])
        pts.append((p[0], p[2]))
    return Polygon(pts).buffer(0)


# --- geometry ---------------------------------------------------------------------------------

def _wall_frame(a: np.ndarray, u: np.ndarray) -> np.ndarray:
    """4x4 taking extrude_polygon's (s, y, depth) to the interior frame."""
    n = np.array([-u[1], u[0]])  # u x up, in (x, z)
    m = np.eye(4)
    m[:3, 0] = [u[0], 0.0, u[1]]
    m[:3, 1] = [0.0, 1.0, 0.0]
    m[:3, 2] = [n[0], 0.0, n[1]]
    m[:3, 3] = [a[0], 0.0, a[1]]
    return m


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    d = b - a
    t = np.clip((p - a) @ d / max(d @ d, 1e-12), 0, 1)
    return float(np.linalg.norm(a + t * d - p))


def _opening_box(o: dict, a: np.ndarray, u: np.ndarray) -> tuple[float, float, float, float]:
    pts = np.array([[p["x"], p["y"], p["z"]] for p in o["polygon"]])
    s = (pts[:, [0, 2]] - a) @ u
    return float(s.min()), float(pts[:, 1].min()), float(s.max()), float(pts[:, 1].max())


def build_walls(twin: dict, levels: dict[str, RoomLevels], roof: Roof | None = None) -> dict[str, list[trimesh.Trimesh]]:
    rooms = {r["room_id"]: r for r in twin["rooms"]}
    openings = {o["opening_id"]: o for o in twin["openings"]}
    floors = sorted(twin["floors"], key=lambda f: f["index"])
    upper = {f["floor_id"]: (floors[n + 1]["floor_id"] if n + 1 < len(floors) else None) for n, f in enumerate(floors)}
    grown = {rid: Polygon(r["polygon"]).buffer(0.05) for rid, r in rooms.items()}
    out: dict[str, list[trimesh.Trimesh]] = {}

    for f in floors:
        fid = f["floor_id"]
        walls = [w for w in twin["walls"] if w["floor_id"] == fid]
        plan = unary_union([Polygon(r["polygon"]).buffer(0) for r in rooms.values() if r["floor_id"] == fid]).buffer(0.02)
        above = [r for r in rooms.values() if r["floor_id"] == upper[fid]]
        above_plan = [(Polygon(r["polygon"]).buffer(0.3), r["room_id"]) for r in above]
        pieces: list[trimesh.Trimesh] = []
        ext_pieces: list[trimesh.Trimesh] = []
        glass: list[trimesh.Trimesh] = []
        doors: list[trimesh.Trimesh] = []

        for w in walls:
            a, b = np.array(w["ends"][0], float), np.array(w["ends"][1], float)
            L = float(np.linalg.norm(b - a))
            if L < 0.02:
                continue
            u = (b - a) / L
            t = w["thickness_m"]
            ext = []
            for end in (a, b):
                others = [o["thickness_m"] / 2 for o in walls if o is not w
                          and _seg_dist(end, np.array(o["ends"][0], float), np.array(o["ends"][1], float)) < JOIN_TOL_M]
                ext.append(max(others, default=0.0))
            s0, s1 = -ext[0], L + ext[1]

            own = [levels[r] for r in w["room_ids"] if r in levels]
            if not own:
                continue
            bottom = min(lv.floor_y for lv in own) - SLAB_M
            n2 = np.array([-u[1], u[0]])
            ss = np.linspace(s0, s1, max(2, int(np.ceil((s1 - s0) / 0.25)) + 1))
            tops = []
            for s in ss:
                p = a + s * u
                h = []
                for rid in w["room_ids"]:
                    if rid not in levels:
                        continue
                    side = 1.0 if grown[rid].contains(_pt(p + (t / 2 + 0.05) * n2)) else -1.0
                    q = p + side * (t / 2 + 0.05) * n2
                    h.append(levels[rid].ceiling_at(q[0], q[1]))
                tops.append(max(h))
            tops = np.array(tops)
            if w["exterior"]:
                mid = _pt(a + L / 2 * u)
                over = [levels[rid].floor_y for poly, rid in above_plan if rid in levels and poly.contains(mid)]
                if over:
                    tops = np.maximum(tops, max(over))
                elif roof is not None:
                    # up to the underside of the roof, closing gable ends and attic space
                    under = [roof.at(*(a + s * u)) for s in ss]
                    tops = np.array([max(tp, r - ROOF_THICKNESS_M) if r is not None else tp for tp, r in zip(tops, under)])
            if roof is not None:
                # and never through it: the twin's rooms and the DSM disagree in places
                under = [roof.at(*(a + s * u)) for s in ss]
                tops = np.array([max(min(tp, r - ROOF_THICKNESS_M), bottom + 0.3) if r is not None else tp
                                 for tp, r in zip(tops, under)])
            profile = Polygon([(s0, bottom), (s1, bottom), *zip(ss[::-1], tops[::-1])]).buffer(0)
            profile = profile.simplify(0.005)

            low = min(lv.floor_y for lv in own)
            for oid in w.get("opening_ids") or []:
                o = openings.get(oid)
                if o is None:
                    continue
                o0, y0, o1, y1 = _opening_box(o, a, u)
                if o["kind"] != "window" and y0 - low < 0.3:
                    y0 = low - 0.005  # door polygons sit on the twin's floor_y, which can be off
                profile = profile.difference(box(o0, y0, o1, y1))
                frame = _wall_frame(a, u)
                if o["kind"] == "window":
                    glass.append(_slab(frame, o0, y0, o1, y1, 0.02))
                elif o.get("exterior") and o.get("leaf") is not False:
                    doors.append(_slab(frame, o0, y0, o1, y1, 0.05))

            frame = _wall_frame(a, u) @ trimesh.transformations.translation_matrix([0, 0, -t / 2])
            for g in _polys(profile):
                m = trimesh.creation.extrude_polygon(g, t)
                m.apply_transform(frame)
                if not w["exterior"]:
                    pieces.append(m)
                    continue
                # the outer face only: not jambs, sills or heads, and not the face toward a room
                fn = m.face_normals
                probe = m.triangles_center + 0.05 * fn
                along = np.abs(fn[:, [0, 2]] @ u) > 0.5
                outer = (np.abs(fn[:, 1]) < 0.5) & ~along & ~contains_xy(plan, probe[:, 0], probe[:, 2])
                if outer.any():
                    ext_pieces.append(m.submesh([np.flatnonzero(outer)], append=True))
                if (~outer).any():
                    pieces.append(m.submesh([np.flatnonzero(~outer)], append=True))

        key = f"f{f['index']}"
        if ext_pieces:
            out[f"{key}_walls_exterior"] = [trimesh.util.concatenate(ext_pieces)]
        if pieces:
            out[f"{key}_walls_interior"] = [trimesh.util.concatenate(pieces)]
        if glass:
            out[f"{key}_glass"] = glass
        if doors:
            out[f"{key}_doors"] = doors
    return out


def _pt(p):
    from shapely.geometry import Point

    return Point(float(p[0]), float(p[1]))


def _slab(frame: np.ndarray, s0: float, y0: float, s1: float, y1: float, depth: float) -> trimesh.Trimesh:
    m = trimesh.creation.box(extents=[s1 - s0, y1 - y0, depth])
    m.apply_translation([(s0 + s1) / 2, (y0 + y1) / 2, 0.0])
    m.apply_transform(frame)
    return m


def _flat_prism(poly: Polygon, y0: float, y1: float) -> trimesh.Trimesh:
    """poly in (x, z) extruded from y0 up to y1."""
    flipped = Polygon([(x, -z) for x, z in poly.exterior.coords], [[(x, -z) for x, z in r.coords] for r in poly.interiors])
    m = trimesh.creation.extrude_polygon(flipped, y1 - y0)
    # extrude_polygon builds in (x, y) with height on +z; (x, -z_interior, y) -> (x, y, z_interior)
    m.apply_transform(np.array([[1, 0, 0, 0], [0, 0, 1, y0], [0, -1, 0, 0], [0, 0, 0, 1]], float))
    return m


def build_floors_and_ceilings(twin: dict, levels: dict[str, RoomLevels]) -> dict[str, list[trimesh.Trimesh]]:
    floors = sorted(twin["floors"], key=lambda f: f["index"])
    index = {f["floor_id"]: f["index"] for f in floors}
    lowest = floors[0]["floor_id"]
    holes = unary_union([Polygon(fo["polygon"]).buffer(0) for fo in twin.get("floor_openings") or []])
    half: dict[str, list[float]] = {}
    for w in twin["walls"]:
        for rid in w["room_ids"]:
            half.setdefault(rid, []).append(w["thickness_m"] / 2)
    out: dict[str, list[trimesh.Trimesh]] = {}
    for r in twin["rooms"]:
        lv = levels.get(r["room_id"])
        if lv is None:
            continue
        key = f"f{index[r['floor_id']]}"
        poly = Polygon(r["polygon"]).buffer(0)
        slab = poly.buffer(float(np.median(half.get(r["room_id"], [0.06]))), join_style="mitre")
        if r["floor_id"] != lowest:
            slab = slab.difference(holes)
        mat = r.get("floor_material") if r.get("floor_material") in ("hardwood", "tile", "carpet") else "other"
        for g in _polys(slab):
            out.setdefault(f"{key}_floor_{mat}", []).append(_flat_prism(g, lv.floor_y - SLAB_M, lv.floor_y))

        cut = holes if r["floor_id"] == lowest else Polygon()
        for facet, k in lv.facets:
            plane = lv.planes[k]
            for g in _polys(facet.intersection(poly).difference(cut)):
                v2, f = trimesh.creation.triangulate_polygon(g, engine="earcut")
                v3 = np.c_[v2[:, 0], plane.at(v2[:, 0], v2[:, 1]), v2[:, 1]]
                m = trimesh.Trimesh(v3, f, process=False)
                if (m.face_normals[:, 1] > 0).mean() > 0.5:  # face down into the room
                    m.invert()
                out.setdefault(f"{key}_ceiling", []).append(m)
    return out


def build_roof(roof: Roof) -> list[trimesh.Trimesh]:
    """Each roof facet as a solid panel ROOF_THICKNESS_M thick, its top on the DSM plane."""
    out = []
    for facet, k in roof.facets:
        plane = roof.planes[k]
        for g in _polys(facet):
            v2, f = trimesh.creation.triangulate_polygon(g, engine="earcut")
            top = plane.at(v2[:, 0], v2[:, 1])
            n = len(v2)
            verts = np.r_[np.c_[v2[:, 0], top, v2[:, 1]], np.c_[v2[:, 0], top - ROOF_THICKNESS_M, v2[:, 1]]]
            faces = [f, f[:, ::-1] + n]
            for ring in [g.exterior, *g.interiors]:
                c = np.array(ring.coords)[:-1]
                idx = [int(np.argmin(np.sum((v2 - p) ** 2, axis=1))) for p in c]
                for i0, i1 in zip(idx, idx[1:] + idx[:1]):
                    faces.append(np.array([[i0, i1, i1 + n], [i0, i1 + n, i0 + n]]))
            m = trimesh.Trimesh(verts, np.vstack(faces), process=True)
            trimesh.repair.fix_normals(m)
            out.append(m)
    risers = _roof_risers(roof)
    if risers is not None:
        out.append(risers)
    return out


def _roof_risers(roof: Roof) -> trimesh.Trimesh | None:
    """Vertical faces where a facet meets a higher one: the wall between a low roof and a high one."""
    verts, faces = [], []
    for poly, k in roof.facets:
        ring = np.array(poly.exterior.coords)
        if not poly.exterior.is_ccw:
            ring = ring[::-1]
        for p0, p1 in zip(ring[:-1], ring[1:]):
            d = p1 - p0
            L = float(np.hypot(*d))
            if L < 0.05:
                continue
            out_n = np.array([d[1], -d[0]]) / L  # outward for a counter-clockwise ring
            mid = (p0 + p1) / 2 + 0.1 * out_n
            nb = [j for q, j in roof.facets if j != k and q.contains(_pt(mid))]
            if not nb:
                continue
            here, there = roof.planes[k], roof.planes[nb[0]]
            h0, h1 = here.at(*p0), here.at(*p1)
            g0, g1 = there.at(*p0), there.at(*p1)
            if min(g0 - h0, g1 - h1) < 0.1:  # only from the lower side, and only for a real step
                continue
            n = len(verts)
            verts += [(p0[0], h0 - ROOF_THICKNESS_M, p0[1]), (p1[0], h1 - ROOF_THICKNESS_M, p1[1]),
                      (p1[0], g1, p1[1]), (p0[0], g0, p0[1])]
            faces += [(n, n + 1, n + 2), (n, n + 2, n + 3)]
    if not faces:
        return None
    return trimesh.Trimesh(np.array(verts, float), np.array(faces), process=False)


def build(twin: dict, levels: dict[str, RoomLevels], out: Path, roof: Roof | None = None) -> dict:
    parts = build_walls(twin, levels, roof)
    for k, v in build_floors_and_ceilings(twin, levels).items():
        parts.setdefault(k, []).extend(v)
    if roof is not None and roof.facets:
        parts["roof"] = build_roof(roof)
    from services.api.twin import fittings

    found = fittings.load_fittings(twin["sources"]["matterport_model_id"])
    for mat, meshes in fittings.build_parts(twin, found, levels).items():
        parts[f"f0_fit_{mat}"] = meshes
    scene = trimesh.Scene()
    for name, meshes in sorted(parts.items()):
        mesh = trimesh.util.concatenate(meshes)
        kind = name.split("_", 1)[-1]
        mat_name = {"walls_interior": "wall_interior", "walls_exterior": "wall_exterior", "doors": "door"}.get(kind, kind)
        color = MATERIALS.get(mat_name, MATERIALS["floor_other"])
        metallic, roughness = METALLIC.get(mat_name, (0.0, 0.9))
        material = trimesh.visual.material.PBRMaterial(
            name=mat_name, baseColorFactor=color, metallicFactor=metallic, roughnessFactor=roughness,
            alphaMode="BLEND" if color[3] < 1 else "OPAQUE", doubleSided=kind in ("ceiling", "glass", "roof"))
        mesh.visual = trimesh.visual.TextureVisuals(material=material)
        scene.add_geometry(mesh, geom_name=name, node_name=name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(scene.export(file_type="glb"))
    return {"nodes": {k: int(sum(len(m.faces) for m in v)) for k, v in sorted(parts.items())}, "bytes": out.stat().st_size}


def load_levels(twin: dict, model_id: str, refresh: bool = False) -> dict[str, RoomLevels]:
    path = levels_path(model_id)
    if path.exists() and not refresh:
        return {k: RoomLevels.from_json(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
    levels = measure(twin, model_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v.to_json() for k, v in levels.items()}), encoding="utf-8")
    return levels


def load_roof(twin: dict, model_id: str, levels: dict[str, RoomLevels], refresh: bool = False) -> Roof | None:
    """The roof from the Vexcel DSM, or None when Vexcel is not configured."""
    from services.api.integrations.cache import FixtureMissing
    from services.api.integrations.config import IntegrationNotConfigured

    path = roof_path(model_id)
    if path.exists() and not refresh:
        return Roof.from_json(json.loads(path.read_text(encoding="utf-8")))
    try:
        roof = measure_roof(twin, model_id, levels)
    except (IntegrationNotConfigured, FixtureMissing) as e:
        log.warning("roof: skipped (%s)", e)
        return None
    path.write_text(json.dumps(roof.to_json()), encoding="utf-8")
    return roof


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    bd = sub.add_parser("build", help="fixtures/blobs/<property>/structure.glb")
    bd.add_argument("property_id")
    bd.add_argument("--remeasure", action="store_true", help="measure floor and ceiling heights from the cloud again")
    bd.add_argument("--refit-roof", action="store_true", help="fit the roof to the Vexcel DSM again")
    bd.add_argument("--no-roof", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    twin = json.loads((fixtures_dir() / "twin" / f"{args.property_id}.json").read_text(encoding="utf-8"))
    t0 = time.time()
    model_id = twin["sources"]["matterport_model_id"]
    levels = load_levels(twin, model_id, args.remeasure)
    roof = None if args.no_roof else load_roof(twin, model_id, levels, args.refit_roof or args.remeasure)
    out = glb_path(args.property_id)
    stats = build(twin, levels, out, roof)
    print(f"wrote {out} ({stats['bytes'] / 1e6:.1f} MB) in {time.time() - t0:.0f} s")
    for k, v in stats["nodes"].items():
        print(f"  {k}: {v} triangles")


if __name__ == "__main__":
    main()
