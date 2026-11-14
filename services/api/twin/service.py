"""In-process interface C -> B (WORKSTREAMS.md section 2.5). Agent tools call these, never
integrations/* directly. Only get_twin exists so far; it serves the twin that ingestion wrote.
"""

from __future__ import annotations

from pathlib import Path

from packages.schema.twin import PropertyTwin
from services.api.integrations.config import fixtures_dir


class TwinNotFound(LookupError):
    pass


def twin_path(property_id: str) -> Path:
    return fixtures_dir() / "twin" / f"{property_id}.json"


def get_twin(property_id: str) -> PropertyTwin:
    path = twin_path(property_id)
    if not path.exists():
        raise TwinNotFound(
            f"no twin for {property_id}; run: python -m services.api.twin.ingest {property_id}"
        )
    return PropertyTwin.model_validate_json(path.read_text(encoding="utf-8"))
