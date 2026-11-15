"""Poly Haven models as one GLB each (https://polyhaven.com, CC0: no credit is owed, we give it anyway).

Poly Haven publishes a model as a glTF text file with its geometry (.bin) and its textures (.jpg)
beside it. The rest of the app passes a model around as one file, so `fetch` downloads the 1k
version (textures 1024 px a side: enough for a piece seen across a room, about 1 to 5 MB) and
packs the parts into one binary GLB. Materials are untouched: only where the buffers and
pictures are read from changes, from files next to the document to views into the one binary chunk.

A model is downloaded the first time someone adds the piece, and cached under
data/catalog/polyhaven/. Nothing here runs at import time and nothing downloaded is ever run.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote

import httpx

from services.api.objects.providers import catalog

API = "https://api.polyhaven.com"
HEADERS = {"User-Agent": "furniture-library-fetch/0.1"}
RESOLUTION = "1k"
WORKERS = 8  # downloads side by side: a model is up to a few dozen small files
_ID = re.compile(r"[A-Za-z0-9_\-]+")  # an id becomes part of a URL and of a file name


class PolyHavenError(RuntimeError):
    pass


def cache_dir() -> Path:
    path = catalog.catalog_dir().parent / "polyhaven"
    path.mkdir(parents=True, exist_ok=True)
    return path


def page_url(asset_id: str) -> str:
    return f"https://polyhaven.com/a/{asset_id}"


def listing(http: httpx.Client, asset_id: str, resolution: str = RESOLUTION) -> dict:
    """Where a model's glTF is, and the files it reads: {"url", "md5", "include": {relative path: {"url", "md5", "size"}}}."""
    files = http.get(f"{API}/files/{asset_id}").raise_for_status().json()
    try:
        return files["gltf"][resolution]["gltf"]
    except (KeyError, TypeError) as e:
        raise PolyHavenError(f"Poly Haven has no {resolution} glTF for {asset_id}") from e


def _get(http: httpx.Client, entry: dict) -> bytes:
    data = http.get(entry["url"]).raise_for_status().content
    # A download cut short would be cached for good: the listing's checksum catches it.
    if entry.get("md5") and hashlib.md5(data).hexdigest() != entry["md5"]:
        raise PolyHavenError(f"{entry['url']} arrived damaged")
    return data


def _mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def pack(doc: dict, parts: dict[str, bytes]) -> bytes:
    """A glTF document and the files it names (relative path -> bytes) as one binary GLB.

    Every buffer goes into the one binary chunk, and every bufferView is pointed at its new
    place there. Every picture follows as a bufferView of its own. Meshes, materials, textures
    and samplers are not touched, so the model looks as it did."""

    def read(uri: str) -> bytes:
        if uri.startswith("data:"):
            return base64.b64decode(uri.split(",", 1)[1])
        for name in (uri, unquote(uri)):
            if name in parts:
                return parts[name]
        raise PolyHavenError(f"the glTF names a file that was not downloaded: {uri}")

    blob = bytearray()
    starts: list[int] = []  # where each of the document's buffers begins in the one chunk
    for buffer in doc.get("buffers", []):
        blob += b"\x00" * (-len(blob) % 4)
        starts.append(len(blob))
        blob += read(buffer["uri"])[: buffer["byteLength"]]
    views = doc.setdefault("bufferViews", [])
    for view in views:
        view["byteOffset"] = view.get("byteOffset", 0) + starts[view.get("buffer", 0)]
        view["buffer"] = 0
    for image in doc.get("images", []):
        if "uri" not in image:
            continue
        data = read(image.pop("uri"))
        blob += b"\x00" * (-len(blob) % 4)
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        image["bufferView"], image["mimeType"] = len(views) - 1, _mime(data)
        blob += data
    return catalog._pack(doc, bytes(blob))


def download(asset_id: str, resolution: str = RESOLUTION) -> bytes:
    """One model from Poly Haven as a packed GLB. Not cached: `fetch` is the one to call."""
    if not _ID.fullmatch(asset_id):
        raise PolyHavenError(f"not a Poly Haven asset id: {asset_id!r}")
    with httpx.Client(timeout=120, headers=HEADERS, follow_redirects=True) as http:
        entry = listing(http, asset_id, resolution)
        doc = json.loads(_get(http, entry))
        include = entry.get("include") or {}
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            parts = dict(zip(include, pool.map(lambda e: _get(http, e), include.values())))
    return pack(doc, parts)


def fetch(asset_id: str) -> bytes:
    """One model as a GLB with 1024 px textures, at the size Poly Haven drew it (real size, in
    metres, Y up). Cached on disk: only the first use of a piece downloads anything."""
    if not _ID.fullmatch(asset_id):
        raise PolyHavenError(f"not a Poly Haven asset id: {asset_id!r}")
    small = cache_dir() / f"{asset_id}.{RESOLUTION}.glb"
    if small.exists():
        return small.read_bytes()
    # A 1k texture is already browser-sized; this only turns a stray PNG without transparency into a JPEG.
    glb = catalog.shrink_textures(download(asset_id))
    # Written beside and renamed, so a download that dies half-way never leaves a broken model in the
    # cache. The name is this thread's own: a room furnished with two of one chair fetches it twice at once.
    partial = small.with_name(f"{small.name}.{os.getpid()}.{threading.get_ident()}.part")
    partial.write_bytes(glb)
    partial.replace(small)
    return glb


if __name__ == "__main__":  # python -m services.api.library.polyhaven <asset id> ... : download these now
    import sys

    for asset in sys.argv[1:]:
        print(f"{asset:40} {len(fetch(asset)) / 1e6:5.1f} MB")
