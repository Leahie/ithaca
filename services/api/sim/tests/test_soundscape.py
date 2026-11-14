"""Soundscape: activity timing, source levels and event counting."""

import json

import numpy as np
import pandas as pd
import pytest

from services.api.integrations.config import fixtures_dir
from services.api.sim import soundscape
from services.api.sim.tests.test_traffic import T

TABLE = json.loads((fixtures_dir() / "tables" / "soundscape.json").read_text(encoding="utf-8"))
WARM = np.full(24, 27.0)


def day(season="summer", sunrise=6.5, sunset=20.4, temperature=WARM, rain=0.5) -> soundscape.DayType:
    return soundscape.DayType(f"{season}-weekday", season, "weekday", "2026-07-15", sunrise, sunset, temperature, rain)


def test_dawn_chorus_peaks_around_sunrise():
    a = soundscape.activity("birds", TABLE["biophony"]["birds"], day(sunrise=6.5))
    assert int(np.argmax(a)) in (5, 6)
    assert a[2] < 0.02 * a.max()


def test_cicadas_sing_on_hot_summer_afternoons_only():
    cfg = TABLE["biophony"]["cicadas"]
    summer = soundscape.activity("cicadas", cfg, day())
    assert summer[14] > 0.5 and summer[2] == 0.0
    assert soundscape.activity("cicadas", cfg, day(season="winter")).max() == 0.0
    assert soundscape.activity("cicadas", cfg, day(temperature=np.full(24, 18.0))).max() == 0.0


def test_night_insects_and_frogs_call_after_dark():
    for group in ("night_insects", "frogs"):
        a = soundscape.activity(group, TABLE["biophony"][group], day())
        assert a[23] > 5 * a[13]


def test_frogs_follow_rain():
    cfg = TABLE["biophony"]["frogs"]
    dry, wet = (soundscape.activity("frogs", cfg, day(rain=r))[22] for r in (0.0, 1.0))
    assert dry == pytest.approx(cfg["rain"]["base"] * wet)


def test_band_power_is_the_level_at_one_metre():
    for level in (62.0, 80.0, 90.0):
        lw = 10 * np.log10(soundscape.band_power(T, TABLE["biophony"]["birds"]["shape_db"], level))
        assert soundscape.dba(10 ** ((lw - 11.0) / 10.0), T.a_weighting) == pytest.approx(level, abs=0.01)


def test_rain_share_counts_evenings_after_rain():
    times = pd.date_range("2025-01-01", "2025-12-31 23:00", freq="h")
    rain = np.zeros(len(times))
    rain[(times.month == 7) & (times.day == 10) & (times.hour == 12)] = 20.0  # wets the evenings of July 10 and 11
    weather = {"time": [t.isoformat() for t in times], "temperature_c": [25.0] * len(times), "precipitation_mm": rain.tolist()}
    days = soundscape.day_types(TABLE, 29.68, -95.46, "America/Chicago", weather, 2026)
    summer = next(d for d in days if d.id == "summer-weekday")
    assert summer.rain_share == pytest.approx(2 / 92)
    assert 6.0 < summer.sunrise_h < 7.0 and 20.0 < summer.sunset_h < 21.0


def test_events_are_audible_only_above_the_steady_level():
    d = [day()]
    steady = np.zeros((1, 24, soundscape.N_BANDS))
    steady[..., 5] = 10 ** (50 / 10)  # 1 kHz: about 50 dBA every hour
    events = np.zeros((1, 24, 2))
    events[0, 8] = [3.0, 2.0]  # 08:00: three quiet honks and two loud ones
    sources = [
        soundscape.Source("road", "Road", "traffic", "steady", "", steady),
        soundscape.Source("horns", "Horns", "traffic", "events", "", np.zeros_like(steady), events=events,
                          lmax_dba=np.array([40.0, 65.0])),
    ]
    out = soundscape.assemble(d, sources, T.a_weighting)
    horns = out["levels"]["summer-weekday"]["sources"]["horns"]
    assert horns["events_per_hour"][8] == 5.0
    assert horns["audible_per_hour"][8] == 2.0
    assert out["sources"][1]["event_lmax_dba"]["median"] == 40.0
