"""Layout solver invariants (B2), on real rooms of the fixture house."""

from __future__ import annotations

import json
from pathlib import Path

from services.api.sim import layout_solver as ls
from services.api.twin.service import get_twin

TWIN = get_twin("onXQLXhfyQv")
ITEMS = [
    ls.Item(i["item_id"], i["label"], i["item_class"], i["dims_m"]["w"], i["dims_m"]["d"], i["dims_m"]["h"])
    for i in json.loads((Path(__file__).resolve().parents[4] / "fixtures/inventory/demo.json").read_text())
]
SOFA, TABLE, CHAIR = (i.item_id for i in ITEMS)
WISHES = [
    ls.Constraint(kind="against_wall", item=SOFA),
    ls.Constraint(kind="in_front_of", item=TABLE, target=SOFA),
    ls.Constraint(kind="near", item=CHAIR, target=TABLE),
]


def room(room_id: str):
    return next(r for r in TWIN.rooms if r.room_id == room_id)


def test_every_variant_is_valid_and_variants_differ():
    living = room("tt6gm2a0463x9wmnx7rn6gugc")
    result = ls.solve(living, TWIN.openings, ITEMS, WISHES, n=3)
    assert len(result.variants) >= 2  # this room has few usable walls; fewer than 3 comes with a reason
    assert (len(result.variants) == 3) == (result.reason is None)
    for v in result.variants:
        assert ls.violations(living, TWIN.openings, ITEMS, v.poses) == []  # inside, no overlap, doors and path clear
        assert "Gray three-seat sofa against a wall (any)" in v.met
    poses = [v.poses for v in result.variants]
    assert all(ls._difference(a, b) >= 0.6 for i, a in enumerate(poses) for b in poses[i + 1 :])
    assert [v.score for v in result.variants] == sorted((v.score for v in result.variants), reverse=True)


def test_violations_catches_a_bad_layout():
    living = room("tt6gm2a0463x9wmnx7rn6gugc")
    good = ls.solve(living, TWIN.openings, ITEMS, WISHES, n=1).variants[0].poses
    stacked = {**good, TABLE: good[SOFA]}  # table inside the sofa
    outside = {**good, CHAIR: ls.Pose(999, 999, 0)}
    assert any("overlap" in v for v in ls.violations(living, TWIN.openings, ITEMS, stacked))
    assert any("inside the room" in v for v in ls.violations(living, TWIN.openings, ITEMS, outside))


def test_returns_fewer_with_a_reason_when_the_room_is_too_small():
    closet = room("g1gkrkdygg4zs5xge8bxfuc1d")  # 2.9 m²
    result = ls.solve(closet, TWIN.openings, ITEMS, WISHES, n=3)
    assert result.variants == [] and "no arrangement fits" in result.reason


def test_walkways_between_doors_stay_clear_and_a_drop_lands_on_a_valid_spot():
    living = room("tt6gm2a0463x9wmnx7rn6gugc")
    scene = ls._scene(living, TWIN.openings, TWIN.walls)
    for v in ls.solve(living, TWIN.openings, ITEMS, WISHES, n=3, walls=TWIN.walls).variants:
        shapes = {i.item_id: ls.footprint(i, v.poses[i.item_id]) for i in ITEMS}
        assert ls._in_walkways(scene, ITEMS, shapes) < 0.05  # nothing parked where people walk

    sofa = [ITEMS[0]]
    door = scene.door_points[0]  # the worst place to drop a sofa: right in a doorway
    pose = ls.nearest_valid_pose(living, TWIN.openings, sofa, {}, SOFA, door.x, door.y, walls=TWIN.walls)
    assert pose is not None and ls.violations(living, TWIN.openings, sofa, {SOFA: pose}, walls=TWIN.walls) == []
    assert 0.3 < ((pose.x - door.x) ** 2 + (pose.z - door.y) ** 2) ** 0.5 <= 2.0  # moved off the door, but stayed close


def test_glass_to_the_floor_scan_routes_and_floating_pieces():
    """The three things a person notices first: furniture in front of a glass door, on the path
    people walk, or adrift in the middle of the room."""
    from services.api.agent import views

    living = room("tt6gm2a0463x9wmnx7rn6gugc")
    living = living.model_copy(update={"floor_y": views.floor_height(TWIN, living.room_id)})
    scene = ls._scene(living, TWIN.openings, TWIN.walls, TWIN.sweeps)
    glass = next(o for o in TWIN.openings if o.room_id == living.room_id and o.kind == "window")
    assert glass.polygon[0].y - living.floor_y < ls.GLASS_TO_FLOOR_M  # the scan calls this floor-to-ceiling glass a window
    assert scene.window_zones == [] and len(scene.door_zones) == 4  # so it is kept clear like a door
    assert scene.routes is not None and scene.routes.length > 3  # walking routes came from the scan

    result = ls.solve(living, TWIN.openings, ITEMS, WISHES, n=3, walls=TWIN.walls, sweeps=TWIN.sweeps)
    assert result.variants
    for v in result.variants:
        shapes = {i.item_id: ls.footprint(i, v.poses[i.item_id]) for i in ITEMS}
        assert not any(s.intersects(scene.routes) for s in shapes.values())  # nothing on a route
        assert not any(s.intersection(z).area > 1e-4 for s in shapes.values() for z in scene.door_zones)  # glass door clear
        assert max(ls._floating(scene, ITEMS, shapes).values()) == 0  # every piece on a wall or in a group that is

    # a big open-plan room: the scan shows ways through that the door list doesn't have
    big = room("cdz3fkt38kae7tapstpt0eaeb")
    assert len(ls._scene(big, TWIN.openings, TWIN.walls, TWIN.sweeps).door_points) > len(ls._scene(big, TWIN.openings, TWIN.walls).door_points)


def test_large_pieces_stand_with_their_back_on_solid_wall_even_when_asked_onto_a_window_wall():
    from services.api.agent import views

    living = room("tt6gm2a0463x9wmnx7rn6gugc")
    living = living.model_copy(update={"floor_y": views.floor_height(TWIN, living.room_id)})
    scene = ls._scene(living, TWIN.openings, TWIN.walls, TWIN.sweeps)
    onto_glass = [ls.Constraint(kind="against_wall", item=SOFA, target="window"), ls.Constraint(kind="in_front_of", item=TABLE, target=SOFA)]
    result = ls.solve(living, TWIN.openings, ITEMS[:2], onto_glass, n=3, walls=TWIN.walls, sweeps=TWIN.sweeps)
    assert result.variants
    for v in result.variants:
        sofa = ls.footprint(ITEMS[0], v.poses[SOFA])
        assert ls._off_wall(scene, ITEMS[0], sofa) == 0  # whole back on wall with no window, door or glass behind it
        assert any("against a wall (window)" in u for u in v.unmet)  # and the designer is told its wish was not met

    sideways = ls.Pose(v.poses[SOFA].x, v.poses[SOFA].z, v.poses[SOFA].yaw + 90)  # an end touching the wall is not "on the wall"
    assert ls._off_wall(scene, ITEMS[0], ls.footprint(ITEMS[0], sideways)) > 0.5
