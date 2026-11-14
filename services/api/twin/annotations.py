"""Corrections kept apart from the Matterport inputs: openings from the web app
(PUT /properties/{pid}/openings) and room labels from the floor plan.

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


def rooms_path(property_id: str) -> Path:
    return fixtures_dir() / "twin" / "annotations" / f"{property_id}.rooms.json"


def apply_room_corrections(twin: PropertyTwin) -> PropertyTwin:
    """Room corrections from the published floor plan, where Matterport's rooms are wrong.

    `labels`: room_id -> label. `carve`: a piece cut out of the room `from`, either as a new room
    (`room_id`, `label`, `floor_material`) or, with a null label, as no room at all (a stairwell).
    Scan positions, openings and walls on the piece move with it. Existing room ids never change.
    """
    from shapely.geometry import Point, Polygon

    from services.api.twin import frames
    from services.api.twin import rooms as plan

    path = rooms_path(twin.property_id)
    if not path.exists():
        return twin
    spec = json.loads(path.read_text(encoding="utf-8"))
    labels = spec.get("labels", {})
    rooms = {r.room_id: (r.model_copy(update={"label": labels[r.room_id]}) if r.room_id in labels else r) for r in twin.rooms}
    sweeps, openings, walls = list(twin.sweeps), list(twin.openings), list(twin.walls)

    def outline(shape) -> list[tuple[float, float]]:
        pts = [(round(x, 3), round(z, 3)) for x, z in shape.exterior.coords[:-1]]
        return plan.start_at_min(frames.ccw_from_above(pts))

    for c in spec.get("carve", []):
        parent = rooms.get(c["from"])
        if parent is None or (c["room_id"] and c["room_id"] in rooms):
            continue
        piece = Polygon(c["polygon"])
        rest = Polygon(parent.polygon).buffer(0).difference(piece)
        rest = max(getattr(rest, "geoms", [rest]), key=lambda g: g.area)
        rooms[parent.room_id] = parent.model_copy(update={"polygon": outline(rest), "area_m2": round(rest.area, 2)})
        new_id = c["room_id"] if c.get("label") else None
        if new_id:
            rooms[new_id] = parent.model_copy(update={
                "room_id": new_id, "label": c["label"], "polygon": outline(piece), "area_m2": round(piece.area, 2),
                "floor_material": c.get("floor_material", parent.floor_material)})
        sweeps = [s.model_copy(update={"room_id": new_id}) if s.room_id == parent.room_id and piece.contains(Point(s.position.x, s.position.z)) else s
                  for s in sweeps]
        if not new_id:
            continue

        def moved(x: float, z: float) -> bool:  # on the piece's edge and no longer on the parent's
            p = Point(x, z)
            return piece.exterior.distance(p) < 0.15 and rest.exterior.distance(p) > 0.15

        def swap(o):
            x = sum(v.x for v in o.polygon) / len(o.polygon)
            z = sum(v.z for v in o.polygon) / len(o.polygon)
            if not moved(x, z):
                return o
            if o.room_id == parent.room_id:
                return o.model_copy(update={"room_id": new_id})
            return o.model_copy(update={"other_room_id": new_id}) if o.other_room_id == parent.room_id else o

        openings = [swap(o) for o in openings]
        walls = [w.model_copy(update={"room_ids": [new_id if r == parent.room_id else r for r in w.room_ids]})
                 if parent.room_id in w.room_ids and moved((w.ends[0][0] + w.ends[1][0]) / 2, (w.ends[0][1] + w.ends[1][1]) / 2) else w
                 for w in walls]
    order = {f.floor_id: f.index for f in twin.floors}
    return twin.model_copy(update={"rooms": sorted(rooms.values(), key=lambda r: (order.get(r.floor_id, 0), r.room_id)),
                                   "sweeps": sweeps, "openings": openings, "walls": walls})


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
