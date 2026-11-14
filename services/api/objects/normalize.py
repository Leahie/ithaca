"""Turn whatever GLB a model produced into a furniture asset that meets I-G15:
meters, Y up, front = +Z, origin at the bottom center of the bounding box, sized to dims_m.

An asset that can't be brought within 2 cm of its dimensions is rejected, not saved (B6).
"""

from __future__ import annotations

import io
import math

import numpy as np
import trimesh

from services.api.objects.classes import TYPICAL_SIZE_M

TOLERANCE_M = 0.02
MAX_STRETCH = 1.8  # how unevenly a model may be stretched to hit stated dimensions


class NormalizeError(ValueError):
    pass


def normalize_glb(glb: bytes, item_class: str, stated: tuple[float | None, float | None, float | None]) -> tuple[bytes, tuple[float, float, float]]:
    """Returns (normalized GLB, (w, d, h) in meters).

    stated = (w, d, h) read from the image, any of them None. With all three, the model is scaled
    to exactly those. Otherwise it keeps its proportions and is scaled so its largest side
    matches the typical size for its class.
    """
    try:
        scene = trimesh.load(io.BytesIO(glb), file_type="glb", force="scene")
    except Exception as e:
        raise NormalizeError(f"the 3D file could not be read: {e}") from e
    if scene.is_empty or scene.bounds is None:
        raise NormalizeError("the 3D file has no geometry")

    def extents() -> np.ndarray:
        return scene.bounds[1] - scene.bounds[0]  # (x, y, z); glTF is Y up

    if min(extents()) <= 1e-6:
        raise NormalizeError("the 3D model is flat or empty")

    typical = TYPICAL_SIZE_M.get(item_class, TYPICAL_SIZE_M["other"])
    w, d, h = (s if s and s > 0 else None for s in stated)
    want_w, want_d = (w or typical[0]), (d or typical[1])

    # Models can come out turned a quarter. Pick the turn whose footprint shape matches best.
    ex, _, ez = extents()
    if abs(math.log((ez / ex) / (want_w / want_d))) < abs(math.log((ex / ez) / (want_w / want_d))):
        scene.apply_transform(trimesh.transformations.rotation_matrix(math.pi / 2, [0, 1, 0]))
    ex, ey, ez = extents()

    if w and d and h:
        scale = np.array([w / ex, h / ey, d / ez])
        if scale.max() / scale.min() > MAX_STRETCH:
            raise NormalizeError(
                f"the 3D model's shape ({ex / ey:.2f} wide per unit height) doesn't match the stated size "
                f"{w:.2f} × {d:.2f} × {h:.2f} m; it would have to be stretched too far"
            )
    else:
        known = [(w, ex), (h, ey), (d, ez)]
        ratios = [target / size for target, size in known if target]
        uniform = float(np.mean(ratios)) if ratios else max(typical[0], typical[1]) / max(ex, ez)
        scale = np.array([uniform] * 3)
    scene.apply_transform(np.diag([*scale, 1.0]))

    lo, hi = scene.bounds
    scene.apply_translation([-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2])

    ex, ey, ez = extents()
    dims = (round(float(ex), 3), round(float(ez), 3), round(float(ey), 3))  # (w, d, h)
    lo = scene.bounds[0]
    wanted = (w or dims[0], d or dims[1], h or dims[2])
    if any(abs(a - b) > TOLERANCE_M for a, b in zip(dims, wanted)) or abs(lo[1]) > 1e-3:
        raise NormalizeError(f"could not size the model to {wanted}; got {dims}")
    return scene.export(file_type="glb"), dims
