"""Minimal stand-in for pycolmap on machines without a wheel (aarch64, e.g. the DGX Spark).

Reads a COLMAP *text* model (cameras.txt, images.txt, points3D.txt) and exposes the attributes that
gsplat's examples/datasets/colmap.py uses. Installed as the module `pycolmap`.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np


class _Rigid:
    def __init__(self, qvec, tvec):
        w, x, y, z = qvec
        r = np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
            [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
            [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
        ])
        self._m = np.concatenate([r, np.asarray(tvec, float).reshape(3, 1)], axis=1)

    def matrix(self):
        return self._m


class Camera(SimpleNamespace):
    def calibration_matrix(self):
        p = self.params
        if self.model_name == "SIMPLE_PINHOLE":
            fx = fy = p[0]; cx, cy = p[1], p[2]
        else:
            fx, fy, cx, cy = p[0], p[1], p[2], p[3]
        return np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1.0]])


class _Points:
    """points3D as columns; items() yields light objects (no tracks: the exports carry none)."""

    def __init__(self, path):
        data = np.loadtxt(path, comments="#", usecols=range(8), ndmin=2) if os.path.getsize(path) else np.zeros((0, 8))
        self.ids = data[:, 0].astype(np.int64)
        self.xyz = data[:, 1:4]
        self.rgb = data[:, 4:7].astype(np.uint8)
        self.err = data[:, 7]
        self._empty = SimpleNamespace(elements=[])

    def items(self):
        for i in range(len(self.ids)):
            yield int(self.ids[i]), SimpleNamespace(xyz=self.xyz[i], color=self.rgb[i], error=self.err[i], track=self._empty)


class Reconstruction:
    def __init__(self, path):
        self.cameras = {}
        for line in open(os.path.join(path, "cameras.txt")):
            if line.strip() and not line.startswith("#"):
                v = line.split()
                self.cameras[int(v[0])] = Camera(camera_id=int(v[0]), model_name=v[1], width=int(v[2]), height=int(v[3]),
                                                 params=np.array([float(x) for x in v[4:]]))
        self.images = {}
        lines = [l for l in open(os.path.join(path, "images.txt")) if not l.startswith("#")]
        for line in lines[::2]:
            v = line.split()
            if not v:
                continue
            self.images[int(v[0])] = SimpleNamespace(name=v[9], camera_id=int(v[8]),
                                                     cam_from_world=_Rigid([float(x) for x in v[1:5]], [float(x) for x in v[5:8]]))
        self.points3D = _Points(os.path.join(path, "points3D.txt"))

    def reg_image_ids(self):
        return sorted(self.images)
