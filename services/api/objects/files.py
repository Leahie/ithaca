"""Uploaded images and generated assets on local disk (data/files), served at /api/files/...

S3 storage exists in Stream C's compose setup; this moves there when the worker does.
"""

from __future__ import annotations

import os
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


# --- reset everything (routers/reset.py) --------------------------------------------------------

# Every folder under the root that something writes to, and what is in it: photos and videos
# people uploaded, the pieces built from them (3D models, cut-outs, masks), stills taken from
# their videos, and cut-outs made with the command line tool (objects/cli.py). All of it is
# theirs. A new folder for people's files must be added here, or a reset leaves it behind.
USER_FOLDERS = ("uploads", "items", "frames", "segments")


def _user_folders(base: Path | None) -> list[Path]:
    base = base or root()
    # A link is never followed: a reset deletes inside the root and nowhere else.
    return [base / name for name in USER_FOLDERS if (base / name).is_dir() and not (base / name).is_symlink()]


def count(base: Path | None = None) -> int:
    """How many uploaded and generated files there are. `base`: another root, for tests."""
    return sum(len(names) for folder in _user_folders(base) for _, _, names in os.walk(folder))


def wipe(base: Path | None = None) -> tuple[int, list[str]]:
    """Delete every uploaded and generated file. The four folders stay, empty. Returns how many
    files went, and a line for each one that could not be deleted: one stubborn file does not
    save the rest. `base`: another root, for tests."""
    deleted, errors = 0, []
    for folder in _user_folders(base):
        for here, dirs, names in os.walk(folder, topdown=False):  # the deepest first, so emptied folders can go
            for name in names:
                try:
                    os.unlink(os.path.join(here, name))
                    deleted += 1
                except OSError as e:
                    errors.append(f"{os.path.join(here, name)}: {e.strerror or e}")
            for name in dirs:
                path = os.path.join(here, name)
                try:
                    os.unlink(path) if os.path.islink(path) else os.rmdir(path)
                except OSError:
                    pass  # not empty: a file in it could not be deleted, and that is reported above
    return deleted, errors
