"""Opening annotations from the web app (PUT /properties/{pid}/openings).

Saved apart from the Matterport inputs so re-ingestion keeps them: ingest uses the annotated list
in place of the detected openings and marks `quality.openings = "annotated"`.
"""

from __future__ import annotations

import json
from pathlib import Path

from packages.schema.twin import Opening, PropertyTwin
from services.api.integrations.config import fixtures_dir


class InvalidAnnotation(ValueError):
    pass


def annotations_path(property_id: str) -> Path:
    return fixtures_dir() / "twin" / "annotations" / f"{property_id}.openings.json"


def load_openings(property_id: str) -> list[Opening] | None:
    path = annotations_path(property_id)
    if not path.exists():
        return None
    return [Opening.model_validate(o) for o in json.loads(path.read_text(encoding="utf-8"))]


def validate(twin: PropertyTwin, openings: list[Opening]) -> None:
    room_ids = {r.room_id for r in twin.rooms}
    seen = set()
    for o in openings:
        if o.opening_id in seen:
            raise InvalidAnnotation(f"duplicate opening_id {o.opening_id}")
        seen.add(o.opening_id)
        if o.room_id not in room_ids:
            raise InvalidAnnotation(f"opening {o.opening_id}: unknown room_id {o.room_id}")
        if o.other_room_id is not None and (o.other_room_id not in room_ids or o.other_room_id == o.room_id):
            raise InvalidAnnotation(f"opening {o.opening_id}: bad other_room_id {o.other_room_id}")
        if len(o.polygon) != 4:
            raise InvalidAnnotation(f"opening {o.opening_id}: polygon needs 4 corners "
                                    "(bottom-start, bottom-end, top-end, top-start)")
        if not 0 <= o.facade_azimuth_deg < 360:
            raise InvalidAnnotation(f"opening {o.opening_id}: facade_azimuth_deg must be in [0, 360)")


def save_openings(property_id: str, openings: list[Opening]) -> PropertyTwin:
    """Validate, store the annotation file, and rewrite the twin (the version bumps if it changed)."""
    from services.api.twin.ingest import write_twin
    from services.api.twin.service import get_twin, twin_path

    twin = get_twin(property_id)
    validate(twin, openings)
    ordered = sorted(openings, key=lambda o: o.opening_id)
    path = annotations_path(property_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([o.model_dump(mode="json") for o in ordered], indent=1) + "\n",
                    encoding="utf-8", newline="\n")
    updated = twin.model_copy(update={
        "openings": ordered,
        "quality": twin.quality.model_copy(update={"openings": "annotated"}),
    })
    return write_twin(updated, twin_path(property_id))
