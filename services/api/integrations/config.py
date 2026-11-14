"""Environment config shared by the integration clients."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[3]

load_dotenv(REPO_ROOT / ".env", override=False)


class IntegrationNotConfigured(RuntimeError):
    """A key or local dataset is missing. The feature is off; startup continues (I-G21)."""

    def __init__(self, service: str, needs: str):
        super().__init__(f"{service} is not configured: set {needs}")
        self.service = service


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip().strip('"').strip("'")


def fixtures_dir() -> Path:
    return Path(env("FIXTURES_DIR") or REPO_ROOT / "fixtures")


def costar_kit_dir() -> Path:
    path = Path(env("COSTAR_KIT_DIR", "data/costar-hackathon-kit"))
    return path if path.is_absolute() else REPO_ROOT / path
