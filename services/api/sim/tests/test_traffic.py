"""Outdoor noise against references and hand calculations (C5)."""

import dataclasses
import json

import numpy as np
import pytest
from shapely.geometry import LineString

from packages.schema.reports import NoiseScenario
from packages.schema.twin import Floor, Opening, Vec3
from services.api.integrations.config import fixtures_dir
from services.api.sim import noise, traffic
from services.api.sim.tests.test_noise import TABLE, _room, _twin, _wall, house
from services.api.twin import frames

T = traffic.load_traffic(json.loads((fixtures_dir() / "tables" / "traffic.json").read_text(encoding="utf-8")),
                         TABLE.a_weighting)
FLAT = dataclasses.replace(T, housing_density=0.0, porous_ground=False)  # open, paved: the textbook case


def road(y, length=4000.0, aadt=24000.0, heavy=0.0, speed=50.0, t=T) -> traffic.LineSource:
    """A straight two-way road along x at north = y."""
    lw = traffic.line_power(t, traffic.flows(aadt, heavy), speed)
    points, lengths = traffic._cut(LineString([(-length / 2, y), (length / 2, y)]), t.segment_m)
    return traffic.LineSource("r", "Test Road", "road", "class_default", points, 10 ** (lw / 10) * lengths[:, None],
                              t.hourly * 24)


def heard(src, height=10.0, normal=(0.0, 1.0), obs=None, t=FLAT) -> float:
    rec = traffic.Receiver("w", "a", np.zeros(2), np.array(normal), height)
    e = traffic.incident(t, rec, src.points, src.power, obs).sum(axis=0)
    return traffic.a_sum(10 * np.log10(np.maximum(e, 1e-30)), T.a_weighting)


def test_cnossos_emission_matches_the_reference_example():
    """1104 cars/h + 96 heavy/h at 50 km/h: 84.1 dB(A) per metre (roadnoise 0.1.1, 2021 Table F-1)."""
    lw = traffic.line_power(T, {"1": 1104, "3": 96}, 50)
    assert traffic.a_sum(lw, T.a_weighting) == pytest.approx(84.1, abs=0.05)


def test_doubling_the_flow_adds_3_db():
    one = traffic.a_sum(traffic.line_power(T, {"1": 500, "3": 40}, 60), T.a_weighting)
    two = traffic.a_sum(traffic.line_power(T, {"1": 1000, "3": 80}, 60), T.a_weighting)
    assert two - one == pytest.approx(10 * np.log10(2), abs=0.01)


def test_long_road_matches_the_infinite_line_formula():
    """Over reflecting ground a line source gives L = L_W' - 10 lg d - 3 (d perpendicular)."""
    src = road(10.0, aadt=24000)
    lw_a = traffic.a_sum(traffic.line_power(T, traffic.flows(24000, 0.0), 50), T.a_weighting)
    d = np.hypot(10.0, 10.0 - T.source_height_m)
    assert heard(src) == pytest.approx(lw_a - 10 * np.log10(d) - 3.0, abs=0.4)


def test_doubling_the_distance_from_a_line_source_takes_off_3_db():
    near, far = heard(road(20.0)), heard(road(40.0))
    assert near - far == pytest.approx(3.0, abs=0.3)


def test_a_wall_facing_away_hears_nothing_from_the_road():
    assert heard(road(30.0), normal=(0.0, -1.0)) < -100


def _block(x0, x1, y0, y1, h):
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    starts, ends = np.array(corners[:-1], float), np.array(corners[1:], float)
    return traffic.Obstacles(starts, ends, np.full(4, h), float(np.hypot(max(abs(x0), abs(x1)), max(abs(y0), abs(y1)))))


def test_a_building_between_road_and_window_screens_it_more_the_taller_it_is():
    src = road(30.0, length=200.0)
    open_ground = heard(src, height=1.5, t=FLAT)
    low = heard(src, height=1.5, obs=_block(-150, 150, 8, 18, 4.0), t=FLAT)
    high = heard(src, height=1.5, obs=_block(-150, 150, 8, 18, 12.0), t=FLAT)
    assert open_ground - low > 5.0
    assert high < low
    assert open_ground - high <= T.barrier_max_db + 0.5


def test_a_traffic_count_applies_to_its_road_and_one_way_carriageways_carry_half():
    lat, lon = 29.7, -95.5

    def way(name, y, oneway=None, highway="secondary"):
        geometry = [frames.enu_to_latlon(x, y, lat, lon) for x in (-300.0, 300.0)]
        return {"osm_id": name + str(y), "highway": highway, "name": name, "oneway": oneway, "maxspeed": "35 mph",
                "geometry": geometry}

    station_lat, station_lon = frames.enu_to_latlon(0.0, 52.0, lat, lon)
    surroundings = {"roads": [way("Main Street", 50.0, "yes"), way("Main Street", 60.0, "yes"), way("Side Street", -50.0)]}
    stations = [{"station_id": "X1", "aadt": 20000, "lat": station_lat, "lon": station_lon}]
    sources = {s.name: s for s in traffic.road_sources(T, surroundings, stations, lat, lon)}
    main, side = sources["Main Street"], sources["Side Street"]
    assert main.data == "txdot_aadt" and main.aadt == 20000
    assert side.data == "class_default" and side.aadt == T.classes["secondary"].aadt
    per_metre = main.power.sum(axis=0) / (2 * 600.0)  # two carriageways, 600 m each
    expected = 10 ** (traffic.line_power(T, traffic.flows(10000, T.classes["secondary"].heavy), 35 * traffic.MPH) / 10)
    assert np.allclose(per_metre, expected, rtol=1e-6)


def test_rush_hour_is_louder_than_3am_by_the_traffic_ratio():
    out = traffic.Outdoor(receivers=[traffic.Receiver("w", "a", np.zeros(2), np.array([0.0, 1.0]), 1.5)],
                          sources=[road(30.0)], energy=np.ones((1, 1, 8)), background=np.zeros((24, 8)))
    hourly = out.hourly()[0].sum(axis=1)
    assert 10 * np.log10(hourly[17] / hourly[3]) == pytest.approx(10 * np.log10(T.hourly[17] / T.hourly[3]), abs=1e-9)


# --- indoors ----------------------------------------------------------------------------------


def box(glazing="double") -> object:
    """One 4 x 4 m room with a 2 x 1.2 m window in its north wall (facing a road to the north)."""
    window = Opening(opening_id="win", room_id="a", kind="window", facade_azimuth_deg=0, glazing=glazing, exterior=True,
                     polygon=[Vec3(x=1, y=0.9, z=0), Vec3(x=3, y=0.9, z=0), Vec3(x=3, y=2.1, z=0), Vec3(x=1, y=2.1, z=0)])
    walls = [_wall("n", (0, 0), (4, 0), ["a"], True, ["win"]), _wall("s", (0, 4), (4, 4), ["a"], True),
             _wall("w", (0, 0), (0, 4), ["a"], True), _wall("e", (4, 0), (4, 4), ["a"], True)]
    return _twin([_room("a", 0, 4, label="Bedroom")], [window], walls, [Floor(floor_id="f0", index=0, elevation_m=0.0)], [])


def test_one_room_follows_the_facade_transmission_formula():
    """L_in = L_out + 10 lg(sum S tau / A_total) for one room with outside level everywhere equal."""
    twin = box()
    net = noise.network(twin, TABLE)
    e_out = 10 ** (np.array([60, 62, 63, 62, 60, 57, 52, 45]) / 10)
    e_in = net.solve_outside({key: e_out for key, _, _ in net.envelope})[0]
    s_tau = sum(sti for _, _, sti in net.envelope)
    assert np.allclose(e_in, s_tau * e_out / (net.absorption[0] + net.outside[0]), rtol=1e-9)


def test_better_glazing_and_closed_windows_are_quieter_inside():
    twin = box()
    out = traffic.outdoor(twin, T, [road(30.0)], None)
    data, caveats = "class_default", []

    def report(**scenario):
        sc = NoiseScenario(**scenario)
        return noise.noise_report(twin, noise.network(twin, TABLE, glazing=sc.glazing, windows_open=sc.windows_open),
                                  TABLE, out, "a", sc, data, caveats)

    single, double, triple = report(glazing="single"), report(), report(glazing="triple")
    opened = report(windows_open=True)
    assert single.laeq_24h_dba > double.laeq_24h_dba > triple.laeq_24h_dba
    assert opened.laeq_24h_dba - double.laeq_24h_dba > 15
    assert opened.laeq_24h_dba < opened.outdoor_lden_dba
    assert double.laeq_night_dba < double.laeq_day_dba


def test_a_room_with_no_outside_wall_gets_the_outdoor_level_it_hears_through():
    """b sits behind a: its outdoor level is a's road-facing wall, so outdoor minus inside (what the
    house stops) is larger for b than for a, never a guess."""
    twin = house()
    twin = twin.model_copy(update={"walls": [w.model_copy(update={"exterior": False}) if w.wall_id.startswith("b_") else w
                                             for w in twin.walls]})
    out = traffic.outdoor(twin, T, [road(-30.0)], None)
    net = noise.network(twin, TABLE)
    a, b = (noise.noise_report(twin, net, TABLE, out, rid, NoiseScenario(), "class_default", []) for rid in "ab")
    assert len(b.outdoor_hourly) == 24
    assert [h.bands_db for h in b.outdoor_hourly] == [h.bands_db for h in a.outdoor_hourly]
    stopped = lambda rep: rep.outdoor_hourly[17].laeq_dba - rep.hourly[17].laeq_dba
    assert stopped(b) > stopped(a) > 0
    by_room = {r.room_id: r for r in noise.noise_house(twin, net, TABLE, out, 17, NoiseScenario(), "class_default", []).rooms}
    assert by_room["b"].outdoor_bands_db == by_room["a"].outdoor_bands_db
    assert by_room["b"].outdoor_laeq_dba > 0


def test_source_contributions_add_up_to_the_room_total():
    twin = box()
    out = traffic.outdoor(twin, T, [road(30.0), road(-200.0, aadt=60000)], None)
    rep = noise.noise_report(twin, noise.network(twin, TABLE), TABLE, out, "a", NoiseScenario(), "class_default", [])
    total = 10 * np.log10(sum(10 ** (s.contribution_dba / 10) for s in rep.sources))
    assert total == pytest.approx(rep.laeq_24h_dba, abs=0.15)
    assert rep.sources[0].name == "Test Road"
