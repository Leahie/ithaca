"""Stand-in generator for fixtures/sun/reference.json (owned by Stream C, invariant C5).

Sun positions from pvlib (NREL SPA) at the fixture property, used by the web test that
holds the client sun direction within 0.5 degrees of the server reference (A3).

    uv run --with pvlib python apps/web/scripts/fixtures/sun_reference.py
"""
import json
from pathlib import Path

import pandas as pd
import pvlib

LAT, LON, TZ = 29.678249880806373, -95.46197028272869, "America/Chicago"
OUT = Path(__file__).resolve().parents[4] / "fixtures" / "sun" / "reference.json"

days = ["2026-01-15", "2026-03-20", "2026-06-21", "2026-09-22", "2026-11-14", "2026-12-21"]
times = pd.DatetimeIndex(
    [pd.Timestamp(f"{d} {h:02d}:{m:02d}", tz=TZ) for d in days for h in range(5, 21) for m in (0, 30)]
)
pos = pvlib.solarposition.get_solarposition(times, LAT, LON, altitude=17, method="nrel_numpy")

samples = [
    {
        "datetime": t.isoformat(),
        "azimuth_deg": round(float(row.azimuth), 4),
        "elevation_deg": round(float(row.elevation), 4),
        "apparent_elevation_deg": round(float(row.apparent_elevation), 4),
    }
    for t, row in zip(times, pos.itertuples())
]
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(
    json.dumps(
        {
            "generator": f"pvlib {pvlib.__version__} (nrel_numpy)",
            "lat": LAT,
            "lon": LON,
            "timezone": TZ,
            "note": "azimuth: degrees clockwise from true north (I-G11). elevation: geometric, no refraction.",
            "samples": samples,
        },
        indent=1,
    )
    + "\n"
)
print(f"wrote {OUT} ({len(samples)} samples)")
