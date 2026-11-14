"""Ingestion against the recorded fixtures (replay mode, no network)."""

import glob
import json

import numpy as np
import pytest

from packages.schema.twin import PropertyTwin
from services.api.integrations.cache import RecordReplayCache
from services.api.integrations.config import fixtures_dir
from services.api.twin import frames
from services.api.twin.ingest import build_twin, georef_residual_m
from services.api.twin.service import get_twin, twin_path

PROPERTY_ID = "onXQLXhfyQv"


@pytest.fixture(scope="module")
def twin() -> PropertyTwin:
    return build_twin(PROPERTY_ID, cache=RecordReplayCache(mode="replay"))


def test_rebuild_is_byte_identical_to_committed_twin(twin):
    committed = twin_path(PROPERTY_ID).read_bytes().decode("utf-8")
    stored = get_twin(PROPERTY_ID)
    rebuilt = twin.model_copy(update={"version": stored.version}).model_dump_json(indent=2) + "\n"
    assert rebuilt == committed


def test_ids_reference_each_other(twin):
    floor_ids = {f.floor_id for f in twin.floors}
    room_ids = [r.room_id for r in twin.rooms]
    assert len(room_ids) == len(set(room_ids))
    assert all(r.floor_id in floor_ids for r in twin.rooms)
    assert all(s.floor_id in floor_ids and (s.room_id is None or s.room_id in room_ids) for s in twin.sweeps)
    assert all(o.room_id in room_ids and (o.other_room_id is None or o.other_room_id in room_ids)
               for o in twin.openings)


def test_sweep_links_are_symmetric_and_reach_every_sweep(twin):
    from services.api.twin.nav import components

    graph = {s.sweep_id: s.neighbors for s in twin.sweeps}
    assert all(s in graph[n] and n != s for s, ns in graph.items() for n in ns)
    assert len(components(graph)) == 1


def test_room_polygons_are_ccw_from_above(twin):
    for room in twin.rooms:
        assert len(room.polygon) >= 3
        assert frames.signed_area_from_above(room.polygon) > 0, room.room_id


def test_sweeps_sit_inside_their_rooms(twin):
    from services.api.twin.rooms import point_in_polygon

    rooms = {r.room_id: r for r in twin.rooms}
    inside = [point_in_polygon((s.position.x, s.position.z), rooms[s.room_id].polygon)
              for s in twin.sweeps if s.room_id]
    assert sum(inside) / len(inside) > 0.9


def test_georeference_matches_matterport_conversion(twin):
    path = glob.glob(str(fixtures_dir() / "integrations" / "matterport" / f"twin_inputs_{PROPERTY_ID}-*.json"))[0]
    g = json.load(open(path, encoding="utf-8"))["response"]["data"]["model"]["geocoordinates"]
    world = frames.from_column_major(twin.interior_to_world)
    heading = np.arctan2(world[1, 0], world[0, 0])
    assert twin.quality.alignment == "georef"
    assert georef_residual_m(g, heading, (world[0, 3], world[1, 3]), twin.lat, twin.lon) < 0.5


def test_interior_world_round_trip_on_a_sweep(twin):
    world = frames.from_column_major(twin.interior_to_world)
    p = twin.sweeps[0].position
    back = frames.apply(frames.invert_rigid(world), frames.apply(world, (p.x, p.y, p.z)))
    assert np.allclose(back, (p.x, p.y, p.z))
