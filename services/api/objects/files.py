"""Uploaded images and generated assets on local disk (data/files), served at /api/files/...

S3 storage exists in Stream C's compose setup; this moves there when the worker does.
"""

from __future__ import annotations

from pathlib import Path

from services.api.integrations.config import REPO_ROOT, env


def root() -> Path:
    path = Path(env("FILES_DIR") or REPO_ROOT / "data" / "files")
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(relative: str, data: bytes) -> str:
    """Write a file and return the URL the browser uses for it."""
    path = root() / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"/api/files/{relative}"


def find(relative: str) -> Path | None:
    path = (root() / relative).resolve()
    return path if path.is_file() and root().resolve() in path.parents else None
