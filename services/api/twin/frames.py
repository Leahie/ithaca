"""Coordinate frames.

model    Matterport Model API: right-handed, Z up, meters.
interior Showcase SDK frame (I-G9): right-handed, Y up, meters. interior = (x, z, -y) of model.
world    local ENU at the property's (lat, lon) (I-G10): X east, Y north, Z up.

Seen from above, interior -Z is "up the page", so a plan drawn as (x, -z) matches the model's
(x, y) plan. "CCW seen from above" is counter-clockwise in that drawing.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

Vec = tuple[float, float, float]

# model = INTERIOR_TO_MODEL @ interior
INTERIOR_TO_MODEL = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]])

_WGS84_A = 6378137.0
_WGS84_E2 = 6.69437999014e-3


def model_to_interior(p: Sequence[float]) -> Vec:
    x, y, z = p
    return (x, z, -y)


def interior_to_model(p: Sequence[float]) -> Vec:
    x, y, z = p
    return (x, -z, y)


def model_xy_to_interior_xz(x: float, y: float) -> tuple[float, float]:
    return (x, -y)


def signed_area_from_above(polygon_xz: Sequence[Sequence[float]]) -> float:
    """Shoelace area of an interior (x, z) polygon; positive when CCW seen from above."""
    pts = [(x, -z) for x, z in polygon_xz]
    return 0.5 * sum(
        x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])
    )


def ccw_from_above(polygon_xz: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return polygon_xz if signed_area_from_above(polygon_xz) >= 0 else polygon_xz[::-1]


def yaw_from_quaternion(w: float, x: float, y: float, z: float) -> float:
    """Rotation about +Z in radians, CCW seen from above."""
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def model_to_world_rotation(heading_rad: float) -> np.ndarray:
    c, s = math.cos(heading_rad), math.sin(heading_rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def interior_to_world_matrix(heading_rad: float, translation_en: Sequence[float]) -> np.ndarray:
    """4x4 matrix: world = R_z(heading) @ model + t, with model = INTERIOR_TO_MODEL @ interior."""
    m = np.eye(4)
    m[:3, :3] = model_to_world_rotation(heading_rad) @ INTERIOR_TO_MODEL
    m[0, 3], m[1, 3] = translation_en[0], translation_en[1]
    return m


def to_column_major(m: np.ndarray, digits: int = 9) -> list[float]:
    return [round(float(v), digits) + 0.0 for v in m.T.reshape(-1)]


def from_column_major(values: Sequence[float]) -> np.ndarray:
    return np.asarray(values, dtype=float).reshape(4, 4).T


def apply(m: np.ndarray, p: Sequence[float]) -> np.ndarray:
    return (m @ np.append(np.asarray(p, dtype=float), 1.0))[:3]


def invert_rigid(m: np.ndarray) -> np.ndarray:
    inv = np.eye(4)
    inv[:3, :3] = m[:3, :3].T
    inv[:3, 3] = -m[:3, :3].T @ m[:3, 3]
    return inv


def compass_azimuth_deg(east: float, north: float) -> float:
    """Degrees clockwise from true north, in [0, 360) (I-G11)."""
    return math.degrees(math.atan2(east, north)) % 360.0


def _radii(lat_deg: float) -> tuple[float, float]:
    s = math.sin(math.radians(lat_deg))
    d = 1.0 - _WGS84_E2 * s * s
    meridional = _WGS84_A * (1.0 - _WGS84_E2) / d**1.5
    prime_vertical = _WGS84_A / math.sqrt(d)
    return meridional, prime_vertical


def enu_to_latlon(east: float, north: float, lat0: float, lon0: float) -> tuple[float, float]:
    """Small-offset (< a few km) ENU -> lat/lon about (lat0, lon0)."""
    m, n = _radii(lat0)
    return (
        lat0 + math.degrees(north / m),
        lon0 + math.degrees(east / (n * math.cos(math.radians(lat0)))),
    )


def latlon_to_enu(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    m, n = _radii(lat0)
    return (
        math.radians(lon - lon0) * n * math.cos(math.radians(lat0)),
        math.radians(lat - lat0) * m,
    )
