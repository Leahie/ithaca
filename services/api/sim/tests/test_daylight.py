"""Daylight engine tests on a 4 x 4 m box with one window (see test_sun.box_twin)."""

import numpy as np
import pandas as pd
import pytest

from services.api.sim import daylight as dl
from services.api.sim.tests.test_sun import TZ, box_twin


def weather(twin, when, dni, dhi):
    times = dl.single_time(when, TZ)
    return dl.Weather(times, np.array([dni]), np.array([dhi]), np.array([dhi]), np.array([0.2]), "clear")


def test_tregenza_covers_the_hemisphere():
    assert len(dl.SKY.altitude_deg) == 145
    assert dl.SKY.solid_angle.sum() == pytest.approx(2 * np.pi, rel=1e-9)
    assert dl.SKY.sub_solid_angle.sum() == pytest.approx(2 * np.pi, rel=1e-9)


@pytest.mark.parametrize("dni,dhi", [(0.0, 200.0), (800.0, 100.0), (300.0, 250.0)])
def test_perez_sky_integrates_to_diffuse_horizontal(dni, dhi):
    luminance = dl.perez_luminance(40.0, 180.0, dni, dhi, 1.3, 1361.0)
    total = np.sum(luminance * dl.SKY.solid_angle * np.sin(np.radians(dl.SKY.altitude_deg)))
    assert total == pytest.approx(dhi * dl.EFFICACY_DIFFUSE, rel=1e-6)


def test_clear_sky_is_brightest_near_the_sun():
    luminance = dl.perez_luminance(40.0, 180.0, 800.0, 100.0, 1.3, 1361.0)
    brightest = np.argmax(luminance)
    assert abs(dl.SKY.azimuth_deg[brightest] - 180) <= 15 and abs(dl.SKY.altitude_deg[brightest] - 50) <= 12


def test_overcast_light_falls_off_away_from_the_window():
    twin = box_twin(180)
    m = dl.daylight_at(twin, weather(twin, "2026-03-20T12:00", 0.0, 250.0), height_m=0.8)
    grid = np.array([[np.nan if v is None else v for v in row] for row in m.rooms[0].grid.lux])
    # south window at z = +2: grid rows run from z = -2 (north) to +2 (south)
    near, far = np.nanmean(grid[-3:]), np.nanmean(grid[:3])
    assert near > 2 * far > 0
    assert m.rooms[0].sun_area_m2 == 0.0
    assert far >= m.rooms[0].reflected_lux > 0


def test_winter_noon_sun_through_a_south_window():
    twin = box_twin(180)
    m = dl.daylight_at(twin, weather(twin, "2026-12-21T12:15", 800.0, 100.0), height_m=0.0)
    room = m.rooms[0]
    assert room.sun_area_m2 > 0.5 and room.max_lux > 10000
    assert room.level in {"bright", "very_bright", "moderate"}


def test_weak_beam_is_light_but_not_sunshine():
    twin = box_twin(180)
    hazy = dl.daylight_at(twin, weather(twin, "2026-12-21T12:15", 60.0, 150.0), height_m=0.0).rooms[0]
    assert hazy.sun_area_m2 == 0.0 and hazy.max_lux > 0


def test_night_is_dark():
    twin = box_twin(180)
    m = dl.daylight_at(twin, weather(twin, "2026-03-20T23:00", 0.0, 0.0))
    assert not m.sun_up and m.rooms[0].max_lux == 0.0 and m.rooms[0].level == "dark"


def test_typical_weather_interpolates_the_matching_hour():
    hours = pd.date_range("2001-01-01", periods=8760, freq="1h")
    tmy = pd.DataFrame({"month": hours.month, "day": hours.day, "hour": hours.hour, "minute": 0,
                        "dni": (hours.dayofyear * 100 + hours.hour).astype(float), "dhi": 50.0, "ghi": 60.0})
    # 2026-06-21 13:30 CDT = 12:30 CST (UTC-6) -> halfway between rows 12:00 and 13:00 of day 172
    w = dl.typical_weather(tmy, dl.single_time("2026-06-21T13:30", TZ), utc_offset_h=-6.0)
    assert w.dni[0] == pytest.approx(172 * 100 + 12.5)
    assert w.dhi[0] == pytest.approx(50.0) and w.source == "typical"


def test_brightness_bands():
    assert [dl.brightness_level(v) for v in (10, 100, 500, 2000, 20000)] == [
        "dark", "dim", "moderate", "bright", "very_bright"]
