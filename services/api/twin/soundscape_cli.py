"""The soundscape dataset: how loud each outdoor sound (highway, main roads, horns, dogs, birds,
cicadas, crickets, frogs, ...) is at the house, per local hour, for every season x weekday/weekend.

Writes fixtures/reports/<pid>/soundscape.json, a long-format CSV and a chart to out/twin/.

    python -m services.api.twin.soundscape_cli onXQLXhfyQv
    INTEGRATION_MODE=record python -m services.api.twin.soundscape_cli onXQLXhfyQv   # fetch habitat, species, weather once
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from services.api.integrations import nature, roads  # noqa: E402
from services.api.integrations.config import fixtures_dir  # noqa: E402
from services.api.sim import soundscape  # noqa: E402
from services.api.twin import service  # noqa: E402
from services.api.twin.sound_cli import OUT_DIR  # noqa: E402

COLORS = {"highway": "#495057", "arterial": "#868e96", "local_streets": "#ced4da", "rail": "#5f3dc4", "horns": "#e03131",
          "dogs": "#e8590c", "birds": "#2f9e44", "cicadas": "#f08c00", "night_insects": "#1971c2", "frogs": "#0c8599",
          "residual": "#dee2e6"}


def build(property_id: str) -> dict:
    twin = service.get_twin(property_id)
    t = service.traffic_tables()
    table = json.loads((fixtures_dir() / "tables" / "soundscape.json").read_text(encoding="utf-8"))
    out, data, caveats = service._outdoor(property_id, twin.version)
    surroundings = roads.surroundings(twin.lat, twin.lon)
    stations = roads.aadt_counts(twin.lat, twin.lon, twin.address)
    habitat = nature.habitat(twin.lat, twin.lon)
    species = nature.species(twin.lat, twin.lon)
    weather = nature.weather(twin.lat, twin.lon, twin.timezone)
    result = soundscape.soundscape(twin, t, table, out, surroundings, stations, habitat, species, weather, date.today().year)
    return {
        "property_id": property_id,
        "address": twin.address,
        "reference": "Outside the house: energy average over every outside wall, 0.4 m out, window height. LAeq per local "
                     "hour. Event peaks (event_lmax_dba, audible_per_hour) are at the wall facing the event; an event "
                     "is audible when its peak beats the hour's steady total.",
        "traffic": data,
        "data": {
            "roads": "OpenStreetMap (Overpass) roads, buildings and rail; TxDOT AADT counts",
            "habitat": "OpenStreetMap water, waterways, woods, trees, grass and parks",
            "species": f"iNaturalist research-grade observations within {nature.SPECIES_RADIUS_KM} km",
            "weather": f"Open-Meteo historical archive (ERA5), {nature.WEATHER_YEAR}: hourly temperature and rain",
            "levels": "fixtures/tables/soundscape.json and traffic.json: published typical values and stated assumptions",
        },
        **result,
        "caveats": [
            *caveats,
            "Animal sound levels come from typical published values and calling densities per habitat, not from "
            "recordings at this house. Expect +/-5 dB, more for frogs (they depend on the weather of the week).",
            "Horn and dog-barking rates are assumptions (see soundscape.json); their levels per event are typical values.",
            "The residual background already stands for some insects and birds; with the nature sources on top it errs loud.",
            "Not modelled: aircraft (Hobby airport is about 13 km away), helicopters, train horns at crossings, "
            "construction, lawn equipment, AC condensers next door, wind in the trees.",
        ],
    }


def write_csv(ds: dict, path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["day_type", "season", "day", "hour", "source", "category", "laeq_dba", "events_per_hour", "audible_per_hour"])
        meta = {s["source_id"]: s for s in ds["sources"]}
        for d in ds["day_types"]:
            for sid, entry in ds["levels"][d["id"]]["sources"].items():
                for h in range(24):
                    w.writerow([d["id"], d["season"], d["day"], h, sid, meta[sid]["category"], entry["laeq_dba"][h],
                                entry.get("events_per_hour", [""] * 24)[h], entry.get("audible_per_hour", [""] * 24)[h]])


def chart(ds: dict, path) -> None:
    seasons = [d for d in ds["day_types"] if d["day"] == "weekday"]
    fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True, sharey=True)
    hours = np.arange(24)
    for ax, d in zip(axes.ravel(), seasons):
        lv = ds["levels"][d["id"]]
        for sid, entry in lv["sources"].items():
            ax.plot(hours, entry["laeq_dba"], color=COLORS.get(sid, "#000"), lw=2.2 if sid in ("birds", "cicadas", "night_insects", "frogs", "horns", "dogs") else 1.4,
                    label=sid.replace("_", " "))
        ax.plot(hours, lv["total_laeq_dba"], color="black", lw=2.5, ls="--", label="all together")
        ax.axvspan(0, _hours(d["sunrise"]), color="#000", alpha=0.06)
        ax.axvspan(_hours(d["sunset"]), 24, color="#000", alpha=0.06)
        ax.set_title(f"{d['season']} weekday ({d['date']}), sunrise {d['sunrise']}, sunset {d['sunset']}", fontsize=10)
        ax.set_ylim(10, 70)
        ax.grid(alpha=0.3)
    for ax in axes[1]:
        ax.set_xticks(hours[::2])
        ax.set_xlabel("local hour")
    for ax in axes[:, 0]:
        ax.set_ylabel("LAeq outside the house (dBA)")
    axes[0][0].legend(fontsize=7, ncol=3, loc="upper left")
    fig.suptitle(f"Soundscape at {ds['address']}: each source alone, outside the house", fontsize=11)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def _hours(clock: str) -> float:
    h, m = clock.split(":")
    return int(h) + int(m) / 60


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    args = parser.parse_args()

    ds = build(args.property_id)
    target = fixtures_dir() / "reports" / args.property_id / "soundscape.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(ds, separators=(",", ":")) + "\n", encoding="utf-8", newline="\n")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path, png_path = OUT_DIR / f"soundscape_{args.property_id}.csv", OUT_DIR / f"soundscape_{args.property_id}.png"
    write_csv(ds, csv_path)
    chart(ds, png_path)

    for d in ds["day_types"]:
        if d["day"] != "weekday":
            continue
        lv = ds["levels"][d["id"]]
        print(f"\n{d['id']}: sunrise {d['sunrise']}, sunset {d['sunset']}, evenings after rain {d['evenings_after_rain']:.0%}")
        print("  " + f"{'source':15s}" + "".join(f"{h:>5d}" for h in range(0, 24, 2)))
        for sid, entry in lv["sources"].items():
            print("  " + f"{sid:15s}" + "".join(f"{entry['laeq_dba'][h]:5.0f}" for h in range(0, 24, 2)))
        print("  " + f"{'TOTAL':15s}" + "".join(f"{lv['total_laeq_dba'][h]:5.0f}" for h in range(0, 24, 2)))
    for s in ds["sources"]:
        extra = {k: v for k, v in s.items() if k in ("full_chorus_dba", "callers_in_range", "event_lmax_dba", "houses", "vehicle_km_per_day")}
        print(f"  {s['source_id']:14s} {extra}")
    print(f"\n  dataset {target}\n  csv     {csv_path}\n  chart   {png_path}")


if __name__ == "__main__":
    main()
