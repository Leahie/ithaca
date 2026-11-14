"""Vexcel API 2.0 client (https://api.vexcelgroup.com/v2/), key sent as the X-API-KEY header.

- /property/extract: parcel and building attributes, including a building footprint. The footprint
  is simplified to north-south / east-west edges, so it is only a rough outline.
- /dsm/extract: Digital Surface Model as a float32 GeoTIFF in Web Mercator (EPSG:3857), heights in
  meters, -9999 = no data. About 7.5 cm pixels on the urban layer.

Coverage is Houston and LA.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests

from services.api.integrations.cache import FixtureMissing, IntegrationMode, RecordReplayCache
from services.api.integrations.config import IntegrationNotConfigured, env

BASE_URL = "https://api.vexcelgroup.com/v2"
WEB_MERCATOR_R = 6378137.0


class VexcelError(RuntimeError):
    pass


@dataclass
class Dsm:
    """Surface heights with each pixel center's latitude and longitude."""

    height: np.ndarray  # (H, W) meters, NaN = no data
    lat: np.ndarray
    lon: np.ndarray


class VexcelClient:
    def __init__(self, api_key: str | None = None, cache: RecordReplayCache | None = None, timeout_s: float = 120.0):
        self.api_key = env("VEXCEL_API_KEY") if api_key is None else api_key
        self.cache = cache or RecordReplayCache()
        self.timeout_s = timeout_s

    def property_extract(self, lat: float, lon: float) -> dict:
        params = {"wkt": f"POINT({lon} {lat})", "srid": 4326}
        return self.cache.call("vexcel", "property", params, lambda: self._get("/property/extract", params).json())

    def primary_structure(self, lat: float, lon: float) -> dict | None:
        for feature in self.property_extract(lat, lon).get("features") or []:
            structures = (feature.get("properties") or {}).get("structures") or []
            for s in structures:
                if s.get("primary-structure"):
                    return s
            if structures:
                return structures[0]
        return None

    def dsm(self, lat: float, lon: float, buffer_m: float = 30.0) -> Dsm:
        params = {"wkt": f"POINT({lon} {lat})", "srid": 4326, "buffer-meter": buffer_m}
        path = self.cache.path_for("vexcel", "dsm", params).with_suffix(".tif")
        if self.cache.mode is IntegrationMode.LIVE or not path.exists():
            if self.cache.mode is IntegrationMode.REPLAY:
                raise FixtureMissing(f"no fixture for vexcel/dsm at {path}; record it with INTEGRATION_MODE=record")
            data = self._get("/dsm/extract", params).content
            if self.cache.mode is IntegrationMode.RECORD:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            return read_dsm(data)
        return read_dsm(path.read_bytes())

    def _get(self, path: str, params: dict) -> requests.Response:
        if not self.api_key:
            raise IntegrationNotConfigured("Vexcel", "VEXCEL_API_KEY")
        r = requests.get(f"{BASE_URL}{path}", params=params, headers={"X-API-KEY": self.api_key}, timeout=self.timeout_s)
        if r.status_code in (401, 403):
            raise VexcelError(f"Vexcel rejected the key (HTTP {r.status_code}): {r.text[:200]}")
        if not r.ok:
            raise VexcelError(f"Vexcel {path} failed (HTTP {r.status_code}): {r.text[:200]}")
        return r


def read_dsm(data: bytes | Path) -> Dsm:
    import io

    import tifffile

    with tifffile.TiffFile(io.BytesIO(data) if isinstance(data, bytes) else data) as tf:
        page = tf.pages[0]
        height = page.asarray().astype(np.float64)
        sx, sy, _ = page.tags["ModelPixelScaleTag"].value
        ti, tj, _, mx0, my0, _ = page.tags["ModelTiepointTag"].value
        nodata = page.tags.get("GDAL_NODATA")
    height[height <= (float(nodata.value) + 1 if nodata else -9000)] = np.nan
    i, j = np.mgrid[0:height.shape[0], 0:height.shape[1]]
    mx = mx0 + (j + 0.5 - ti) * sx
    my = my0 - (i + 0.5 - tj) * sy
    lon = np.degrees(mx / WEB_MERCATOR_R)
    lat = np.degrees(2 * np.arctan(np.exp(my / WEB_MERCATOR_R)) - math.pi / 2)
    return Dsm(height, lat, lon)
