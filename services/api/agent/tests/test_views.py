"""Best view of a room and walking routes, on the real twin."""

from __future__ import annotations

import math

from shapely.geometry import Point, Polygon

from services.api.agent import views
from services.api.twin.service import get_twin

PROPERTY = "onXQLXhfyQv"
EAST_BEDROOM, ITS_BATHROOM, KITCHEN = "peina19mpqedsku6a3huu38md", "tyz3uyd99x00wa88mxhmugrxa", "xy2x8mpaaqknp7q2c3nnbw1ma"


def test_best_view_stands_in_the_room_and_faces_into_it():
    twin = get_twin(PROPERTY)
    sweeps = {s.sweep_id: s for s in twin.sweeps}
    for room in twin.rooms:
        view = views.best_view(twin, room.room_id)
        if view is None:
            assert not any(s.room_id == room.room_id for s in twin.sweeps)
            continue
        at = sweeps[view.sweep_id]
        assert at.room_id == room.room_id
        assert Polygon(room.polygon).buffer(0.05).contains(Point(view.look_at.x, view.look_at.z))  # never a wall
        assert math.dist((at.position.x, at.position.z), (view.look_at.x, view.look_at.z)) > 0.3


def test_route_follows_the_scan_and_only_reports_stairs_when_it_climbs():
    twin = get_twin(PROPERTY)
    sweeps = {s.sweep_id: s for s in twin.sweeps}

    next_door = views.route(twin, EAST_BEDROOM, ITS_BATHROOM)
    assert next_door.room_ids == [EAST_BEDROOM, ITS_BATHROOM] and next_door.distance_m < 5 and next_door.floor_changes == 0

    downstairs = views.route(twin, EAST_BEDROOM, KITCHEN)
    assert downstairs.floor_changes == 1 and downstairs.room_ids[0] == EAST_BEDROOM and downstairs.room_ids[-1] == KITCHEN
    for a, b in zip(downstairs.sweep_ids, downstairs.sweep_ids[1:]):
        assert b in sweeps[a].neighbors  # every step is one the scan recorded as a clear line

    # Two rooms on the same floor: a level walk through rooms of that floor only. (The first version
    # sent this one up the stairs, because the bottom steps are labelled as the upstairs room.)
    office, bedroomish = "3m54yff1z7crxaywd8if9rb0d", "tt6gm2a0463x9wmnx7rn6gugc"
    level = views.route(twin, office, bedroomish)
    floor = next(r.floor_id for r in twin.rooms if r.room_id == office)
    assert level.floor_changes == 0 and level.distance_m < 20
    assert all(next(r.floor_id for r in twin.rooms if r.room_id == rid) == floor for rid in level.room_ids)
