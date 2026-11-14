"""Room acoustics against hand calculations (C5)."""

import json

import numpy as np
import pytest

from packages.schema.twin import (
    Floor,
    FloorOpening,
    Opening,
    PropertyTwin,
    Room,
    TwinAssets,
    TwinQuality,
    TwinSources,
    Vec3,
    Wall,
)
from services.api.integrations.config import fixtures_dir
from services.api.sim import noise
from services.api.twin import frames

TABLE = noise.load_acoustics(json.loads((fixtures_dir() / "tables" / "acoustics.json").read_text(encoding="utf-8")))
H = 2.5


def _room(room_id, x0, x1, floor="f0", y=0.0, material="hardwood", label="Office"):
    return Room(room_id=room_id, floor_id=floor, label=label, polygon=[(x0, 4), (x1, 4), (x1, 0), (x0, 0)],
                floor_y=y, height_m=H, floor_material=material, area_m2=(x1 - x0) * 4)


def _wall(wall_id, a, b, rooms, exterior, openings=(), floor="f0"):
    return Wall(wall_id=wall_id, floor_id=floor, ends=(a, b), thickness_m=0.16 if exterior else 0.12,
                room_ids=list(rooms), exterior=exterior, opening_ids=list(openings),
                assembly="exterior_brick_veneer_frame" if exterior else "interior_stud_gypsum")


def house(leaf=True, material="hardwood", upstairs=False, stair=False) -> PropertyTwin:
    """Rooms a (x 0-4) and b (x 4-8), 4 m deep, sharing a stud wall at x = 4 with one door."""
    door = Opening(opening_id="d", room_id="a", other_room_id="b", kind="door", facade_azimuth_deg=90,
                   glazing="none", exterior=False, leaf=leaf,
                   polygon=[Vec3(x=4, y=0, z=1.6), Vec3(x=4, y=0, z=2.4), Vec3(x=4, y=2.0, z=2.4), Vec3(x=4, y=2.0, z=1.6)])
    rooms = [_room("a", 0, 4, material=material), _room("b", 4, 8, material=material)]
    walls = [
        _wall("ab", (4, 0), (4, 4), ["a", "b"], False, ["d"]),
        _wall("a_n", (0, 0), (4, 0), ["a"], True), _wall("a_s", (0, 4), (4, 4), ["a"], True),
        _wall("a_w", (0, 0), (0, 4), ["a"], True),
        _wall("b_n", (4, 0), (8, 0), ["b"], True), _wall("b_s", (4, 4), (8, 4), ["b"], True),
        _wall("b_e", (8, 0), (8, 4), ["b"], True),
    ]
    floors = [Floor(floor_id="f0", index=0, elevation_m=0.0)]
    floor_openings = []
    if upstairs:
        floors.append(Floor(floor_id="f1", index=1, elevation_m=2.8))
        rooms.append(_room("c", 0, 4, floor="f1", y=2.8))
    if stair:
        floor_openings.append(FloorOpening(floor_opening_id="s", floor_id="f0", polygon=[(1, 1), (2, 1), (2, 3), (1, 3)],
                                           area_m2=2.0, room_ids=["a"], via_opening_ids=["p"], other_floor_room_ids=["c"]))
        door_p = Opening(opening_id="p", room_id="a", kind="door", facade_azimuth_deg=0, glazing="none",
                         exterior=False, leaf=False,
                         polygon=[Vec3(x=1, y=0, z=0), Vec3(x=2, y=0, z=0), Vec3(x=2, y=2, z=0), Vec3(x=1, y=2, z=0)])
        return _twin(rooms, [door, door_p], walls, floors, floor_openings)
    return _twin(rooms, [door], walls, floors, floor_openings)


def _twin(rooms, openings, walls, floors, floor_openings):
    return PropertyTwin(
        property_id="two", version=1, sources=TwinSources(matterport_model_id="two"), address="two",
        lat=29.7, lon=-95.5, timezone="America/Chicago",
        interior_to_world=frames.to_column_major(frames.interior_to_world_matrix(0.0, (0.0, 0.0))),
        floors=floors, rooms=rooms, openings=openings, walls=walls, floor_openings=floor_openings,
        sweeps=[], assets=TwinAssets(),
        quality=TwinQuality(alignment="gps_only", openings="annotated", context="none", traffic="none", walls="annotated"),
    )


def field(twin, room="a", x=1.0, z=2.0, doors="closed", preset="tv", listener=None, **kw):
    net = noise.network(twin, TABLE, doors=doors, **kw)
    return noise.sound_field(twin, net, TABLE, TABLE.presets[preset], room, x, z, listener)


def levels(rooms):
    return {r.room_id: np.array(r.bands_db) for r in rooms}


def test_presets_sum_to_their_a_weighted_level():
    for p in TABLE.presets.values():
        assert noise.a_sum(np.array(p.bands_db), TABLE.a_weighting) == pytest.approx(p.level_dba, abs=0.05)
    loud = noise.scaled_preset(TABLE, "tv", 80)
    assert noise.a_sum(np.array(loud.bands_db), TABLE.a_weighting) == pytest.approx(80, abs=0.05)


def test_source_room_reverberant_level_is_lw_plus_10log_4_over_a():
    twin = house()
    net = noise.network(twin, TABLE)
    _, rooms, _ = field(twin)
    lp1 = np.array(TABLE.presets["tv"].bands_db)
    a = net.absorption[0] + net.coupling[0].sum(axis=0) + net.outside[0]
    expected = lp1 + 11 + 10 * np.log10(4 / a)
    # sound coming back from room b adds a little on top
    assert np.allclose(levels(rooms)["a"], expected, atol=0.3)


def test_partition_follows_the_textbook_transmission_formula():
    """L2 = L1 - R + 10 log(S / A2), with R the composite of wall and closed door."""
    twin = house()
    net = noise.network(twin, TABLE)
    _, rooms, _ = field(twin)
    lv = levels(rooms)
    s = 4 * H
    door = 0.8 * 2.0
    tau = ((s - door) * TABLE.tau("interior_stud_gypsum") + door * TABLE.tau("door_interior_hollow")) / s
    a2 = net.absorption[1] + net.outside[1] + net.coupling[1, 0]
    expected = lv["a"] + 10 * np.log10(tau) + 10 * np.log10(s / a2)
    assert np.allclose(lv["b"], expected, atol=0.2)


def test_reciprocity():
    twin = house()
    _, from_a, _ = field(twin, "a", 2, 2)
    _, from_b, _ = field(twin, "b", 6, 2)
    assert np.allclose(levels(from_a)["b"], levels(from_b)["a"], atol=0.1)


def test_opening_the_door_lets_sound_through_but_passages_are_always_open():
    closed = levels(field(house(), doors="closed")[1])["b"]
    opened = levels(field(house(), doors="open")[1])["b"]
    assert (opened - closed)[4] > 10  # 1 kHz: a hollow door blocks ~18 dB, the stud wall ~40
    passage_closed = levels(field(house(leaf=False), doors="closed")[1])["b"]
    assert np.allclose(passage_closed, opened, atol=0.01)


def test_carpet_shortens_reverb_and_lowers_the_level():
    net_wood = noise.network(house(material="tile"), TABLE)
    net_carpet = noise.network(house(material="tile"), TABLE, add_carpet=True)
    assert (net_carpet.rt60()[0, 3:6] < net_wood.rt60()[0, 3:6] * 0.7).all()
    wood = levels(field(house(material="tile"))[1])["a"]
    carpet = levels(field(house(material="tile"), add_carpet=True)[1])["a"]
    assert (carpet[3:] < wood[3:] - 2).all()


def test_direct_sound_falls_6_db_per_doubling_near_the_source():
    twin = house()
    _, rooms, near = field(twin, listener=("a", 1.5, 2.0))
    _, _, far = field(twin, listener=("a", 2.0, 2.0))
    lw = np.array(TABLE.presets["tv"].bands_db) + 11
    diffuse = 10 ** (levels(rooms)["a"] / 10)
    for heard in (near, far):
        r = heard.distance_m
        expected = 10 * np.log10(diffuse + 10 ** (lw / 10) / (4 * np.pi * r * r))
        assert np.allclose(heard.bands_db, expected, atol=0.11)
    assert near.direct_fraction > far.direct_fraction
    _, _, other = field(twin, listener=("b", 6.0, 2.0))
    assert other.distance_m is None and other.direct_fraction == 0.0
    assert other.bands_db == levels(rooms)["b"].round(1).tolist()


def test_floor_ceiling_only_vs_open_stairwell():
    floor_only = levels(field(house(upstairs=True))[1])["c"]
    with_stair = levels(field(house(upstairs=True, stair=True))[1])["c"]
    assert (with_stair - floor_only)[4] > 20


def test_gains_are_relative_to_the_source_at_1_m():
    _, rooms, _ = field(house())
    lp1 = np.array(TABLE.presets["tv"].bands_db)
    for r in rooms:
        assert np.allclose(np.array(r.gain_db), np.array(r.bands_db) - lp1, atol=0.11)
