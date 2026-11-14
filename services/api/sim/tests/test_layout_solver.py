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
    assert len(result.variants) == 3 and result.reason is None
    for v in result.variants:
        assert ls.violations(living, TWIN.openings, ITEMS, v.poses) == []  # inside, no overlap, doors and path clear
        assert "Gray three-seat sofa against a wall (any)" in v.met
    a, b, c = (v.poses for v in result.variants)
    assert min(ls._difference(a, b), ls._difference(a, c), ls._difference(b, c)) >= 0.6
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
