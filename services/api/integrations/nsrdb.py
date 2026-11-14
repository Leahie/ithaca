"""NSRDB typical-meteorological-year weather (NREL), via pvlib.

Needs NREL_API_KEY and NREL_EMAIL (free: https://developer.nrel.gov/signup/). A typical year
never changes, so it is always recorded to fixtures (even in live mode) and replayed after that.
"""

from __future__ import annotations

import pandas as pd

from services.api.integrations.cache import FixtureMissing, IntegrationMode, RecordReplayCache
from services.api.integrations.config import IntegrationNotConfigured, env

PARAMETERS = ["dni", "dhi", "ghi", "surface_albedo"]


def typical_year(lat: float, lon: float) -> tuple[pd.DataFrame, float]:
    """(hourly rows with month, day, hour, minute, dni, dhi, ghi, albedo; UTC offset of the
    local standard time the rows are stamped in)."""
    request = {"lat": round(lat, 3), "lon": round(lon, 3), "names": "tmy", "parameters": PARAMETERS}
    mode = env("INTEGRATION_MODE", "replay")
    cache = RecordReplayCache(mode=IntegrationMode.REPLAY if mode == "replay" else IntegrationMode.RECORD)
    try:
        record = cache.call("nsrdb", "tmy", request, lambda: _fetch(lat, lon))
    except FixtureMissing as e:
        raise IntegrationNotConfigured("nsrdb", "NREL_API_KEY and NREL_EMAIL, then run once with "
                                       "INTEGRATION_MODE=record") from e
    return pd.DataFrame(record["rows"]), float(record["utc_offset_h"])


def _fetch(lat: float, lon: float) -> dict:
    import pvlib

    key, email = env("NREL_API_KEY"), env("NREL_EMAIL")
    if not key or not email:
        raise IntegrationNotConfigured("nsrdb", "NREL_API_KEY and NREL_EMAIL")
    data, meta = pvlib.iotools.get_nsrdb_psm4_tmy(
        round(lat, 3), round(lon, 3), key, email, year="tmy", time_step=60, parameters=PARAMETERS,
        map_variables=True,
    )
    albedo = data["albedo"] if "albedo" in data else data.get("surface_albedo")
    rows = {
        "month": data.index.month.tolist(),
        "day": data.index.day.tolist(),
        "hour": data.index.hour.tolist(),
        "minute": data.index.minute.tolist(),
        "dni": data["dni"].round(1).tolist(),
        "dhi": data["dhi"].round(1).tolist(),
        "ghi": data["ghi"].round(1).tolist(),
    }
    if albedo is not None:
        rows["albedo"] = albedo.round(3).tolist()
    offset = meta.get("Time Zone", meta.get("timezone"))
    return {"utc_offset_h": float(offset), "rows": rows, "source": "NSRDB PSM4 GOES TMY"}
