"""A small kit for building furniture models by hand: boxes, cylinders, soft cloth, pleated
curtains and flat prints, each with its own colour or picture, put together into one GLB.

Models are built the way every model in the house is stored (objects/normalize.py): metres,
Y up, the front toward +Z, standing on y = 0 and centred over the origin.
"""

from __future__ import annotations

import io
import math
from typing import Sequence

import numpy as np
import trimesh
from PIL import Image
from trimesh.visual import TextureVisuals
from trimesh.visual.material import PBRMaterial

Color = tuple[int, int, int] | tuple[int, int, int, int]
MAX_TEXTURE = 512


def material(color: Color = (200, 200, 200), rough: float = 0.8, metal: float = 0.0, image: Image.Image | None = None,
             alpha: float | None = None, double: bool = False, glow: Color | None = None, cutout: bool = False) -> PBRMaterial:
    """A glTF material: a flat colour, or a picture (tinted by the colour)."""
    rgba = [*color[:3], color[3] if len(color) == 4 else 255]
    if alpha is not None:
        rgba[3] = round(alpha * 255)
    if image is not None:
        image = _small(image)
    mode = "MASK" if cutout else ("BLEND" if rgba[3] < 255 else "OPAQUE")
    return PBRMaterial(
        baseColorFactor=rgba, baseColorTexture=image, roughnessFactor=rough, metallicFactor=metal, doubleSided=double or mode != "OPAQUE",
        alphaMode=mode, alphaCutoff=0.5 if cutout else None, emissiveFactor=[c / 255 for c in glow[:3]] if glow else None,
    )


def _small(image: Image.Image) -> Image.Image:
    if max(image.size) > MAX_TEXTURE:
        image = image.copy()
        image.thumbnail((MAX_TEXTURE, MAX_TEXTURE), Image.LANCZOS)
    return image if image.mode in ("RGB", "RGBA") else image.convert("RGB")


def _rot(rx: float = 0.0, ry: float = 0.0, rz: float = 0.0) -> np.ndarray:
    m = np.eye(4)
    for angle, axis in ((rz, [0, 0, 1]), (rx, [1, 0, 0]), (ry, [0, 1, 0])):
        if angle:
            m = trimesh.transformations.rotation_matrix(math.radians(angle), axis) @ m
    return m


class Model:
    """Parts added one at a time; `glb()` writes them out as one file."""

    def __init__(self) -> None:
        self.parts: list[trimesh.Trimesh] = []

    def add(self, mesh: trimesh.Trimesh, mat: PBRMaterial, uv: np.ndarray | None = None, at: Sequence[float] = (0, 0, 0),
            rot: Sequence[float] = (0, 0, 0)) -> trimesh.Trimesh:
        mesh.visual = TextureVisuals(uv=uv, material=mat)
        m = _rot(*rot)
        m[:3, 3] = at
        mesh.apply_transform(m)
        self.parts.append(mesh)
        return mesh

    # --- shapes -----------------------------------------------------------------------------

    def box(self, w: float, h: float, d: float, at=(0, 0, 0), mat: PBRMaterial | None = None, rot=(0, 0, 0), color: Color = (200, 200, 200),
            rough: float = 0.8, metal: float = 0.0) -> trimesh.Trimesh:
        """A box centred at `at`. With a picture in `mat`, the picture covers every face once."""
        mesh, uv = _uv_box(w, h, d)
        return self.add(mesh, mat or material(color, rough, metal), uv, at, rot)

    def cyl(self, r: float, h: float, at=(0, 0, 0), mat: PBRMaterial | None = None, rot=(0, 0, 0), color: Color = (200, 200, 200),
            sections: int = 24, r_top: float | None = None, rough: float = 0.6, metal: float = 0.0) -> trimesh.Trimesh:
        """An upright cylinder (or a cone with `r_top`) centred at `at`."""
        if r_top is None:
            mesh = trimesh.creation.cylinder(radius=r, height=h, sections=sections)
        else:
            mesh = _frustum(r, r_top, h, sections)
            mesh.apply_translation([0, 0, -h / 2])
        mesh.apply_transform(_rot(rx=-90))
        uv = _cyl_uv(mesh) if mat is not None and mat.baseColorTexture is not None else None
        return self.add(mesh, mat or material(color, rough, metal), uv, at, rot)

    def rod(self, a: Sequence[float], b: Sequence[float], r: float, mat: PBRMaterial | None = None, color: Color = (30, 30, 30),
            sections: int = 8, rough: float = 0.5, metal: float = 0.0) -> trimesh.Trimesh | None:
        """A thin cylinder from point a to point b."""
        a, b = np.asarray(a, float), np.asarray(b, float)
        if np.linalg.norm(b - a) < 1e-5:
            return None
        mesh = trimesh.creation.cylinder(radius=r, segment=[a, b], sections=sections)
        return self.add(mesh, mat or material(color, rough, metal))

    def ball(self, rx: float, ry: float, rz: float, at=(0, 0, 0), mat: PBRMaterial | None = None, color: Color = (200, 200, 200),
             squareness: float = 1.0, rot=(0, 0, 0), rough: float = 0.8, subdivisions: int = 3) -> trimesh.Trimesh:
        """An ellipsoid; `squareness` above 1 squares it off toward a box (a pillow, a mouse)."""
        mesh = trimesh.creation.icosphere(subdivisions=subdivisions)
        v = mesh.vertices
        if squareness != 1.0:
            v[:] = np.sign(v) * np.abs(v) ** (1 / squareness)
            v /= np.max(np.abs(v), axis=0)
        v *= [rx, ry, rz]
        uv = None
        if mat is not None and mat.baseColorTexture is not None:
            uv = np.stack([(v[:, 0] / (2 * rx)) + 0.5, 0.5 - (v[:, 2] / (2 * rz))], axis=1)
        return self.add(mesh, mat or material(color, rough), uv, at, rot)

    def rounded(self, w: float, h: float, d: float, radius: float, at=(0, 0, 0), mat: PBRMaterial | None = None, color: Color = (230, 230, 230),
                rot=(0, 0, 0), rough: float = 0.5) -> trimesh.Trimesh:
        """A box rounded at its four edges seen from the front, `d` deep."""
        from shapely.geometry import box as sbox

        outline = sbox(-w / 2 + radius, -h / 2 + radius, w / 2 - radius, h / 2 - radius).buffer(radius, quad_segs=6)
        mesh = trimesh.creation.extrude_polygon(outline, d)
        mesh.apply_translation([0, 0, -d / 2])
        return self.add(mesh, mat or material(color, rough), None, at, rot)

    def panel(self, w: float, h: float, at=(0, 0, 0), mat: PBRMaterial | None = None, rot=(0, 0, 0), color: Color = (255, 255, 255),
              flip_u: bool = False) -> trimesh.Trimesh:
        """A flat rectangle facing +Z, the picture right way up."""
        v = np.array([[-w / 2, -h / 2, 0], [w / 2, -h / 2, 0], [w / 2, h / 2, 0], [-w / 2, h / 2, 0]])
        uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
        if flip_u:
            uv[:, 0] = 1 - uv[:, 0]
        mesh = trimesh.Trimesh(v, [[0, 1, 2], [0, 2, 3]], process=False)
        return self.add(mesh, mat or material(color), uv, at, rot)

    def cloth(self, w: float, d: float, h: float, at=(0, 0, 0), mat: PBRMaterial | None = None, color: Color = (230, 230, 230), seed: int = 0,
              lumps: int = 5, drape: float = 0.0, rot=(0, 0, 0), n: int = 28) -> trimesh.Trimesh:
        """Something soft lying in a heap: a duvet, a shirt, a bag of bedding. Its top is a bumpy
        surface `h` high at most, falling to the floor at the edges; `drape` lets the edges hang lower."""
        rng = np.random.default_rng(seed)
        xs, zs = np.linspace(-1, 1, n), np.linspace(-1, 1, n)
        gx, gz = np.meshgrid(xs, zs)
        height = np.zeros_like(gx)
        for _ in range(lumps):
            cx, cz, s = rng.uniform(-0.6, 0.6), rng.uniform(-0.6, 0.6), rng.uniform(0.25, 0.6)
            height += rng.uniform(0.5, 1.0) * np.exp(-((gx - cx) ** 2 + (gz - cz) ** 2) / (2 * s * s))
        height += 0.08 * np.sin(gx * 9 + rng.uniform(0, 6)) * np.sin(gz * 7 + rng.uniform(0, 6))  # wrinkles
        edge = np.clip((1 - np.maximum(np.abs(gx), np.abs(gz))) * 4, 0, 1) ** 0.6
        height = np.clip(height, 0.15, None) * edge
        height = height / height.max() * h
        if drape:
            height -= drape * (1 - edge)
        top = np.stack([gx * w / 2, height, gz * d / 2], axis=-1).reshape(-1, 3)
        uv_top = np.stack([(gx + 1) / 2, 1 - (gz + 1) / 2], axis=-1).reshape(-1, 2)
        faces = []
        for i in range(n - 1):
            for j in range(n - 1):
                a, b, c, e = i * n + j, i * n + j + 1, (i + 1) * n + j + 1, (i + 1) * n + j
                faces += [[a, e, c], [a, c, b]]
        mesh = trimesh.Trimesh(top, faces, process=False)
        m = mat or material(color, 0.95)
        m.doubleSided = True
        return self.add(mesh, m, uv_top, at, rot)

    def curtain(self, w: float, h: float, at=(0, 0, 0), mat: PBRMaterial | None = None, color: Color = (230, 220, 200), folds: int = 9,
                depth: float = 0.06, rot=(0, 0, 0), sway: float = 0.0) -> trimesh.Trimesh:
        """A hanging curtain: pleated across its width, a little fuller toward the floor."""
        nx, ny = folds * 8, 12
        xs, ys = np.linspace(-w / 2, w / 2, nx), np.linspace(0, h, ny)
        gx, gy = np.meshgrid(xs, ys)
        fullness = 0.75 + 0.25 * (1 - gy / h)
        gz = depth / 2 * np.sin(gx / w * folds * 2 * math.pi) * fullness + sway * (1 - gy / h) ** 2 * np.sin(gx / w * math.pi)
        verts = np.stack([gx, gy - h / 2, gz], axis=-1).reshape(-1, 3)
        uv = np.stack([(gx + w / 2) / w, gy / h], axis=-1).reshape(-1, 2)
        faces = []
        for i in range(ny - 1):
            for j in range(nx - 1):
                a, b, c, e = i * nx + j, i * nx + j + 1, (i + 1) * nx + j + 1, (i + 1) * nx + j
                faces += [[a, b, c], [a, c, e]]
        mesh = trimesh.Trimesh(verts, faces, process=False)
        m = mat or material(color, 0.95)
        m.doubleSided = True
        return self.add(mesh, m, uv, at, rot)

    # --- output -----------------------------------------------------------------------------

    def bounds(self) -> np.ndarray:
        return trimesh.util.concatenate([p.copy() for p in self.parts]).bounds if self.parts else np.zeros((2, 3))

    def glb(self) -> tuple[bytes, tuple[float, float, float]]:
        """The model as GLB, moved to stand on y = 0 centred over the origin, and its (w, d, h)."""
        lo, hi = np.min([p.bounds[0] for p in self.parts], axis=0), np.max([p.bounds[1] for p in self.parts], axis=0)
        shift = [-(lo[0] + hi[0]) / 2, -lo[1], -(lo[2] + hi[2]) / 2]
        scene = trimesh.Scene()
        for k, part in enumerate(self.parts):
            part = part.copy()
            part.apply_translation(shift)
            scene.add_geometry(part, node_name=f"part{k}", geom_name=f"part{k}")
        size = hi - lo
        return scene.export(file_type="glb"), (round(float(size[0]), 3), round(float(size[2]), 3), round(float(size[1]), 3))


def _uv_box(w: float, h: float, d: float) -> tuple[trimesh.Trimesh, np.ndarray]:
    """A box with four corners per face, so a picture can lie on each face."""
    x, y, z = w / 2, h / 2, d / 2
    faces_v = [
        [[-x, -y, z], [x, -y, z], [x, y, z], [-x, y, z]],  # front +Z
        [[x, -y, -z], [-x, -y, -z], [-x, y, -z], [x, y, -z]],  # back
        [[x, -y, z], [x, -y, -z], [x, y, -z], [x, y, z]],  # right
        [[-x, -y, -z], [-x, -y, z], [-x, y, z], [-x, y, -z]],  # left
        [[-x, y, z], [x, y, z], [x, y, -z], [-x, y, -z]],  # top
        [[-x, -y, -z], [x, -y, -z], [x, -y, z], [-x, -y, z]],  # bottom
    ]
    verts, faces, uv = [], [], []
    for k, quad in enumerate(faces_v):
        verts += quad
        faces += [[4 * k, 4 * k + 1, 4 * k + 2], [4 * k, 4 * k + 2, 4 * k + 3]]
        uv += [[0, 0], [1, 0], [1, 1], [0, 1]]
    return trimesh.Trimesh(np.array(verts), np.array(faces), process=False), np.array(uv, float)


def _frustum(r0: float, r1: float, h: float, sections: int) -> trimesh.Trimesh:
    """A cut cone along +Z from radius r0 at z = 0 to r1 at z = h, closed at both ends."""
    a = np.linspace(0, 2 * math.pi, sections, endpoint=False)
    ring0 = np.stack([r0 * np.cos(a), r0 * np.sin(a), np.zeros_like(a)], 1)
    ring1 = np.stack([r1 * np.cos(a), r1 * np.sin(a), np.full_like(a, h)], 1)
    verts = np.vstack([ring0, ring1, [[0, 0, 0], [0, 0, h]]])
    c0, c1 = 2 * sections, 2 * sections + 1
    faces = []
    for i in range(sections):
        j = (i + 1) % sections
        faces += [[i, j, sections + j], [i, sections + j, sections + i], [c0, j, i], [c1, sections + i, sections + j]]
    return trimesh.Trimesh(verts, faces, process=True)


def _cyl_uv(mesh: trimesh.Trimesh) -> np.ndarray:
    v = mesh.vertices
    u = (np.arctan2(v[:, 0], v[:, 2]) / (2 * math.pi)) + 0.5
    lo, hi = v[:, 1].min(), v[:, 1].max()
    return np.stack([u, (v[:, 1] - lo) / max(hi - lo, 1e-6)], 1)


def jpeg(image: Image.Image, quality: int = 85) -> bytes:
    out = io.BytesIO()
    image.convert("RGB").save(out, "JPEG", quality=quality)
    return out.getvalue()
