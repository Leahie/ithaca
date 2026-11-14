"""Sun engine reference tests (C5)."""

import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from packages.schema.twin import (
    Floor,
    Opening,
    PropertyTwin,
    Room,
    TwinAssets,
    TwinQuality,
    TwinSources,
    Vec3,
)
from services.api.sim import sun
from services.api.twin import frames

LAT, LON, TZ = 29.678, -95.462, "America/Chicago"
OBLIQUITY = 23.44


def _noon_peak(day: str) -> pd.Series:
    times = pd.date_range(f"{day} 10:00", f"{day} 15:00", freq="1min", tz=TZ)
    pos = sun.sun_positions(times, LAT, LON)
    return pos.loc[pos["elevation"].idxmax()]


@pytest.mark.parametrize("day,declination", [("2026-06-21", OBLIQUITY), ("2026-12-21", -OBLIQUITY)])
def test_solar_noon_elevation_matches_textbook(day, declination):
    peak = _noon_peak(day)
    assert peak["elevation"] == pytest.approx(90.0 - LAT + declination, abs=0.5)
    assert peak["azimuth"] == pytest.approx(180.0, abs=1.0)


def test_reference_fixture_matches_engine():
    from services.api.integrations.config import fixtures_dir

    ref = json.loads((fixtures_dir() / "sun" / "reference.json").read_text(encoding="utf-8"))
    times = pd.to_datetime([c["datetime"] for c in ref["cases"]], utc=True)
    pos = sun.sun_positions(times, ref["lat"], ref["lon"])
    assert np.allclose(pos["azimuth"], [c["azimuth_deg"] for c in ref["cases"]], atol=0.01)
    assert np.allclose(pos["elevation"], [c["elevation_deg"] for c in ref["cases"]], atol=0.01)


# A 4 x 4 m box with one 2 m window on the wall facing `azimuth`. Heading 0, so interior +X is east
# and interior -Z is north.
WALLS = {
    180: [(-1, 2), (1, 2)],  # south wall, z = +2
    0: [(1, -2), (-1, -2)],  # north wall, z = -2
    90: [(2, 1), (2, -1)],  # east wall, x = +2
    270: [(-2, -1), (-2, 1)],  # west wall, x = -2
}


def box_twin(azimuth: int) -> PropertyTwin:
    (x0, z0), (x1, z1) = WALLS[azimuth]
    sill, head = 0.9, 2.1
    window = Opening(
        opening_id="w", room_id="r", kind="window", facade_azimuth_deg=azimuth, glazing="double",
        polygon=[Vec3(x=x0, y=sill, z=z0), Vec3(x=x1, y=sill, z=z1), Vec3(x=x1, y=head, z=z1), Vec3(x=x0, y=head, z=z0)],
    )
    return PropertyTwin(
        property_id="box", version=1, sources=TwinSources(matterport_model_id="box"), address="box",
        lat=LAT, lon=LON, timezone=TZ,
        interior_to_world=frames.to_column_major(frames.interior_to_world_matrix(0.0, (0.0, 0.0))),
        floors=[Floor(floor_id="f", index=0, elevation_m=0.0)],
        rooms=[Room(room_id="r", floor_id="f", label="Box", polygon=[(-2, 2), (2, 2), (2, -2), (-2, -2)],
                    floor_y=0.0, height_m=2.5, floor_material="hardwood")],
        openings=[window], sweeps=[], assets=TwinAssets(),
        quality=TwinQuality(alignment="gps_only", openings="annotated", context="none", traffic="none"),
    )


def hours(azimuth: int, day: date) -> float:
    return sun.sun_report(box_twin(azimuth), "r", day, day, heatmap=False).daily[0].direct_sun_hours


def test_south_window_gets_winter_sun_and_north_window_none():
    winter = date(2026, 12, 21)
    assert hours(180, winter) > 4.0
    assert hours(0, winter) == 0.0


def test_south_window_gets_less_floor_sun_in_summer():
    assert hours(180, date(2026, 6, 21)) < hours(180, date(2026, 12, 21))


def test_east_window_is_lit_in_the_morning_only():
    twin = box_twin(90)
    _, _, points = sun.floor_grid(twin.rooms[0])
    windows = sun.room_windows(twin, "r")
    times = sun.day_times(date(2026, 3, 20), TZ)
    pos = sun.sun_positions(times, LAT, LON)
    up = pos["apparent_elevation"].to_numpy() > 0
    lit = sun.lit_matrix(points, sun.sun_directions_interior(
        twin, pos["azimuth"].to_numpy()[up], pos["apparent_elevation"].to_numpy()[up]), windows).any(axis=1)
    solar_noon = pos["elevation"].idxmax()
    assert lit.any() and times[up][lit].max() < solar_noon


def test_sun_at_east_window_morning_lit_afternoon_dark_night_down():
    twin = box_twin(90)
    morning = sun.sun_at(twin, "2026-03-20T08:30", height_m=sun.SEATED_M)
    assert morning.sun_up and [p.room_id for p in morning.rooms] == ["r"]
    assert morning.rooms[0].through_opening_ids == ["w"]
    assert 60 < morning.azimuth_deg < 120
    assert sun.sun_at(twin, "2026-03-20T16:00").rooms == []
    night = sun.sun_at(twin, "2026-03-20T23:00")
    assert not night.sun_up and night.direction_interior is None


def test_naive_times_are_property_local():
    moment = sun.sun_at(box_twin(90), "2026-03-20T08:30")
    assert moment.datetime.utcoffset() == pd.Timedelta(hours=-5)  # CDT
    assert sun.sun_at(box_twin(90), "2026-03-20T13:30Z").datetime.hour == 8


def test_spot_by_east_window_gets_morning_sun_only():
    spot = sun.sun_spot(box_twin(90), "r", 1.5, 0.0, date(2026, 3, 20))
    assert spot.intervals and spot.total_hours > 0.5
    assert spot.intervals[-1].end < _noon_peak("2026-03-20").name
    assert spot.point.y == pytest.approx(sun.SEATED_M)


def test_room_day_south_window_winter():
    day = sun.sun_room_day(box_twin(180), "r", date(2026, 12, 21))
    assert day.through_opening_ids == ["w"] and day.total_hours > 4.0
    assert day.intervals[0].start.hour < 12 < day.intervals[-1].end.hour


def test_intervals_merge_consecutive_steps():
    times = pd.date_range("2026-01-01 08:05", periods=6, freq="10min", tz=TZ)
    out = sun._intervals(times, np.array([True, True, False, True, True, True]), 10)
    assert [(i.start.strftime("%H:%M"), i.end.strftime("%H:%M")) for i in out] == [("08:00", "08:20"), ("08:30", "09:00")]


def test_heatmap_shape_and_bounds():
    report = sun.sun_report(box_twin(180), "r", date(2026, 12, 1), date(2026, 12, 3))
    grid = np.asarray(report.heatmap.hours)
    assert grid.shape == (16, 16)
    assert grid.max() > 0 and grid.min() >= 0
    assert len(report.daily) == 3
