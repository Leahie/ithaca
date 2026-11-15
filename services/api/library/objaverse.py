"""Objaverse models as one GLB each (https://huggingface.co/datasets/allenai/objaverse).

Objaverse 1.0 is about 800,000 models people published on Sketchfab under Creative Commons
licences, kept on Hugging Face as binary glTF files in 160 folders: glbs/000-000/<uid>.glb to
glbs/000-159/<uid>.glb. A line of the furniture index names only the uid, so `fetch` asks Hugging
Face which of the 160 paths exists. That is one small request, and its answer also says how big
the file is before any of it is downloaded.

The models are what their makers uploaded: any scale, textures up to 8192 px, a few of them far
too detailed. `fetch` shrinks the textures to 1024 px and refuses what a browser showing a whole
house cannot carry: a file over 60 MB or a mesh over 400,000 faces. Scale and standing on the
floor are objects/normalize.py's work, as for every other model. Which way a model faces is not
known: the index marks these pieces "auto": false, and people turn them by hand.

A model is downloaded the first time someone adds the piece, and cached under
data/catalog/objaverse/. Nothing here runs at import time and nothing downloaded is ever run.
"""

from __future__ import annotations

import logging
import os
import re
import struct
import threading
from pathlib import Path

import httpx

from services.api.objects.providers import catalog

log = logging.getLogger(__name__)

PATHS_INFO = "https://huggingface.co/api/datasets/allenai/objaverse/paths-info/main"
FILES = "https://huggingface.co/datasets/allenai/objaverse/resolve/main"
HEADERS = {"User-Agent": "furniture-library-fetch/0.1"}
FOLDERS = 160
MAX_BYTES = 60_000_000  # the file as Objaverse keeps it, textures at full size
MAX_FACES = 400_000  # a piece heavier than this slows a browser showing a whole house
_UID = re.compile(r"[0-9a-f]{32}")  # a uid becomes part of a URL and of a file name


class ObjaverseError(RuntimeError):
    """The model cannot be used. The message says why, in words a person adding the piece can read."""


class TooHeavy(ObjaverseError):
    """Over 60 MB or over 400,000 faces. That is true of the file for good, so `fetch` remembers it."""


def cache_dir() -> Path:
    path = catalog.catalog_dir().parent / "objaverse"
    path.mkdir(parents=True, exist_ok=True)
    return path


def locate(http: httpx.Client, uid: str) -> tuple[str, int]:
    """Where Objaverse keeps a model and how many bytes it is: ("glbs/000-023/<uid>.glb", size)."""
    paths = [f"glbs/000-{n:03d}/{uid}.glb" for n in range(FOLDERS)]
    found = http.post(PATHS_INFO, json={"paths": paths}).raise_for_status().json()  # only the paths that exist come back
    if not found:
        raise ObjaverseError(f"Objaverse has no model {uid}")
    return found[0]["path"], int(found[0].get("size") or 0)


def faces(glb: bytes) -> int:
    """How many triangles a GLB draws, read from its document alone (no geometry is decoded).
    A mesh that several nodes use is counted once for each."""
    doc, _ = catalog._unpack(glb)
    accessors = doc.get("accessors", [])

    def triangles(mesh: dict) -> int:
        total = 0
        for part in mesh.get("primitives", []):
            index = part.get("indices", part.get("attributes", {}).get("POSITION"))
            count = accessors[index]["count"] if index is not None else 0
            mode = part.get("mode", 4)
            total += count // 3 if mode == 4 else max(count - 2, 0) if mode in (5, 6) else 0  # triangles; strips and fans
        return total

    per_mesh = [triangles(mesh) for mesh in doc.get("meshes", [])]
    used = [node["mesh"] for node in doc.get("nodes", []) if "mesh" in node]
    return sum(per_mesh[k] for k in used) if used else sum(per_mesh)


def download(uid: str) -> bytes:
    """One model from Objaverse as it is kept there. Not cached: `fetch` is the one to call."""
    if not _UID.fullmatch(uid):
        raise ObjaverseError(f"not an Objaverse uid: {uid!r}")
    with httpx.Client(timeout=300, headers=HEADERS, follow_redirects=True) as http:
        path, size = locate(http, uid)
        if size > MAX_BYTES:
            raise TooHeavy(f"this model is too heavy to use: its file is {size / 1e6:.0f} MB, and the most a piece may be is {MAX_BYTES / 1e6:.0f} MB")
        raw = http.get(f"{FILES}/{path}").raise_for_status().content
    if raw[:4] != b"glTF":
        raise ObjaverseError("this model's file is not a 3D model the app can read")
    if len(raw) > MAX_BYTES:
        raise TooHeavy(f"this model is too heavy to use: its file is {len(raw) / 1e6:.0f} MB, and the most a piece may be is {MAX_BYTES / 1e6:.0f} MB")
    try:
        count = faces(raw)
    except (KeyError, IndexError, TypeError, ValueError, struct.error) as e:  # a file that only looks like a GLB
        raise ObjaverseError("this model's file is not a 3D model the app can read") from e
    if count > MAX_FACES:
        raise TooHeavy(f"this model is too detailed to use: it has {count:,} faces, and the most a piece may have is {MAX_FACES:,}")
    return raw


def fetch(uid: str) -> bytes:
    """One model as a GLB with textures of 1024 px at most, at whatever scale its maker drew it.
    Cached on disk: only the first use of a piece downloads anything. Raises ObjaverseError, with
    the reason, for a model that is not there or is too heavy (over 60 MB, or over 400,000 faces)."""
    if not _UID.fullmatch(uid):
        raise ObjaverseError(f"not an Objaverse uid: {uid!r}")
    small = cache_dir() / f"{uid}.1k.glb"
    if small.exists():
        return small.read_bytes()
    refused = cache_dir() / f"{uid}.refused"  # why it was turned away: a second try must not download 60 MB again
    if refused.exists():
        raise TooHeavy(refused.read_text(encoding="utf-8"))
    try:
        raw = download(uid)
    except TooHeavy as e:
        refused.write_text(str(e), encoding="utf-8")
        raise
    try:
        glb = catalog.shrink_textures(raw)
    except Exception as e:  # a picture format Pillow cannot read: the model is still good, only heavier
        log.warning("objaverse: textures of %s kept at full size (%s)", uid, e)
        glb = raw
    # Written beside and renamed, so a download that dies half-way never leaves a broken model in
    # the cache. Two of the same piece added at once each write their own file.
    partial = small.with_name(f"{small.name}.{os.getpid()}.{threading.get_ident()}.part")
    partial.write_bytes(glb)
    partial.replace(small)
    return glb


if __name__ == "__main__":  # python -m services.api.library.objaverse <uid> ... : download these now
    import sys

    for model in sys.argv[1:]:
        try:
            print(f"{model}  {len(fetch(model)) / 1e6:5.1f} MB")
        except (ObjaverseError, httpx.HTTPError) as e:
            print(f"{model}  REFUSED: {e}")
