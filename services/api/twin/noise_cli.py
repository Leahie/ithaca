"""Noise from outside (traffic, rail, background): the roads around the house, every room at one hour,
and one room through the day. Prints tables and writes three plots to out/twin/.

    python -m services.api.twin.noise_cli onXQLXhfyQv                    # 5 pm, the largest bedroom
    python -m services.api.twin.noise_cli onXQLXhfyQv --hour 3 --room "Living Room"
"""

from __future__ import annotations

import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LogNorm, Normalize  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

from packages.schema.reports import NoiseHouse, NoiseScenario  # noqa: E402
from packages.schema.twin import PropertyTwin  # noqa: E402
from services.api.integrations import roads  # noqa: E402
from services.api.twin import frames, service  # noqa: E402
from services.api.twin.sound_cli import OUT_DIR, pick_room  # noqa: E402

INSIDE = Normalize(vmin=0, vmax=50)
OUTSIDE = Normalize(vmin=35, vmax=70)
CMAP = plt.get_cmap("magma")
SITE_M = 450
SCENARIOS = {"windows closed": NoiseScenario(), "triple glazing": NoiseScenario(glazing="triple"),
             "windows open": NoiseScenario(windows_open=True)}


def site_map(twin: PropertyTwin, hour: int, path) -> None:
    out, _, _ = service._outdoor(twin.property_id, twin.version)
    sources = service.noise_sources(twin.property_id).sources
    loudness = {s.source_id: s.facade_laeq_dba for s in sources}
    fig, ax = plt.subplots(figsize=(10, 9))
    try:
        for b in roads.surroundings(twin.lat, twin.lon)["buildings"]:
            xs, ys = zip(*(frames.latlon_to_enu(a, c, twin.lat, twin.lon) for a, c in b["geometry"]))
            ax.fill(xs, ys, color="#555", lw=0, zorder=1)
    except Exception:  # the map still shows roads and the house without footprints
        pass
    norm = Normalize(vmin=30, vmax=55)
    for s in out.sources:
        width = 0.8 + 3.5 * np.log10(max(s.aadt or 1000, 100) / 100) / 3 if s.kind == "road" else 2.0
        for line in s.path_latlon:
            xs, ys = zip(*(frames.latlon_to_enu(a, c, twin.lat, twin.lon) for a, c in line))
            ax.plot(xs, ys, color=plt.get_cmap("viridis")(norm(loudness.get(s.source_id, 30))), lw=width, zorder=2,
                    ls="--" if s.kind == "rail" else "-")
    hourly = out.hourly()[:, hour]
    levels = [service.noise.traffic.a_sum(10 * np.log10(np.maximum(e, 1e-30)), service.acoustics().a_weighting)
              for e in hourly]
    pts = np.array([r.point for r in out.receivers])
    sc = ax.scatter(pts[:, 0], pts[:, 1], c=levels, cmap=CMAP, norm=OUTSIDE, s=18, zorder=4, edgecolors="white",
                    linewidths=0.3)
    for s in sources[:6]:
        if s.path:
            lat, lon = s.path[0][len(s.path[0]) // 2]
            x, y = frames.latlon_to_enu(lat, lon, twin.lat, twin.lon)
            if abs(x) < SITE_M and abs(y) < SITE_M:
                label = f"{s.name}\n{s.aadt:,}/day ({'TxDOT' if s.data == 'txdot_aadt' else 'typical'})" if s.aadt else s.name
                ax.text(x, y, label, fontsize=7, color="white", zorder=5,
                        bbox={"facecolor": "black", "alpha": 0.5, "pad": 1, "lw": 0})
    ax.set_xlim(-SITE_M, SITE_M)
    ax.set_ylim(-SITE_M, SITE_M)
    ax.set_aspect("equal")
    ax.set_facecolor("#1d1d1d")
    ax.set_xlabel("east (m)")
    ax.set_ylabel("north (m)")
    ax.set_title(f"Roads coloured by LAeq,24h they cause at the house; dots: outside each wall at {hour:02d}:00\n"
                 "grey: OpenStreetMap buildings (screening)", fontsize=9)
    plt.colorbar(sc, ax=ax, fraction=0.035, pad=0.02, label=f"outside the wall at {hour:02d}:00 (dBA)")
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def floors(twin: PropertyTwin, houses: dict[str, NoiseHouse], hour: int, path) -> None:
    names = ["windows closed", "windows open"]
    fig, axes = plt.subplots(len(twin.floors), len(names), figsize=(7 * len(names), 5.5 * len(twin.floors)),
                             squeeze=False)
    for col, name in enumerate(names):
        levels = {r.room_id: r for r in houses[name].rooms}
        for row, fl in enumerate(twin.floors):
            ax = axes[row][col]
            for room in (r for r in twin.rooms if r.floor_id == fl.floor_id):
                xs, zs = zip(*(room.polygon + room.polygon[:1]))
                lv = levels[room.room_id]
                ax.fill(xs, [-z for z in zs], color=CMAP(INSIDE(lv.laeq_dba)), zorder=1)
                ax.plot(xs, [-z for z in zs], color="#ccc", lw=0.6, zorder=2)
                p = Polygon(room.polygon).representative_point()
                ax.text(p.x, -p.y, f"{room.label}\n{lv.laeq_dba:.0f} dBA", ha="center", va="center", fontsize=6,
                        color="white", zorder=3, bbox={"facecolor": "black", "alpha": 0.35, "pad": 1, "lw": 0})
            for w in twin.walls:
                if w.floor_id == fl.floor_id and w.exterior:
                    (x0, z0), (x1, z1) = w.ends
                    ax.plot([x0, x1], [-z0, -z1], color="#eee", lw=2, zorder=2)
            ax.set_aspect("equal")
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_facecolor("#222")
            ax.set_title(f"{fl.label} · {hour:02d}:00 · {name}", fontsize=9)
    bar = fig.colorbar(plt.cm.ScalarMappable(norm=INSIDE, cmap=CMAP), ax=axes, fraction=0.02, pad=0.01)
    bar.set_label("traffic + outdoor noise inside (dBA)")
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def day_chart(twin: PropertyTwin, room_id: str, label: str, path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5))
    hours = np.arange(24)
    base = service.noise_report(twin.property_id, room_id, NoiseScenario())
    ax.plot(hours, [h.laeq_dba for h in base.outdoor_hourly], color="#adb5bd", lw=2, label="outside its loudest wall")
    for (name, sc), color in zip(SCENARIOS.items(), ["#e64980", "#7048e8", "#f59f00"]):
        rep = service.noise_report(twin.property_id, room_id, sc)
        ax.plot(hours, [h.laeq_dba for h in rep.hourly], color=color, lw=2, label=f"inside, {name}")
    g = service.traffic_tables().guidelines
    ax.axhline(g["bedroom_night_dba"], color="#2f9e44", ls=":", lw=1)
    ax.text(0.2, g["bedroom_night_dba"] + 0.6, "WHO bedroom at night (30)", fontsize=7, color="#2f9e44")
    ax.axhline(g["living_day_dba"], color="#1c7ed6", ls=":", lw=1)
    ax.text(8.2, g["living_day_dba"] + 0.6, "WHO living room by day (35)", fontsize=7, color="#1c7ed6")
    ax.set_xticks(hours[::2])
    ax.set_xlabel("local hour")
    ax.set_ylabel("LAeq (dBA)")
    ax.set_ylim(0, 75)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="upper left")
    ax.set_title(f"{label}: traffic and outdoor noise through a weekday", fontsize=10)
    fig.savefig(path, dpi=110, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    parser.add_argument("--hour", type=int, default=17, help="local hour 0..23")
    parser.add_argument("--room", default="Bedroom", help="room id or label for the day chart")
    args = parser.parse_args()

    twin = service.get_twin(args.property_id)
    room = pick_room(twin, args.room)
    print("Sources (LAeq,24h outside the most exposed wall, that source alone):")
    noise_sources = service.noise_sources(args.property_id)
    for s in noise_sources.sources[:12]:
        traffic = f"{s.aadt:>7,}/day {s.data:13s}" if s.aadt else f"{'':>11s} {s.data:13s}"
        dist = f"{s.distance_m:6.0f} m" if s.distance_m is not None else "       "
        print(f"  {s.facade_laeq_dba:5.1f} dBA  {dist}  {traffic}  {s.name}")
    for c in noise_sources.caveats:
        print("  *", c)

    houses = {name: service.noise_house(args.property_id, args.hour, sc) for name, sc in SCENARIOS.items()}
    by_id = {r.room_id: r for r in twin.rooms}
    floor_no = {fl.floor_id: fl.index + 1 for fl in twin.floors}
    print(f"\nAt {args.hour:02d}:00 (dBA)         outside  " + "  ".join(f"{n:>15s}" for n in SCENARIOS))
    columns = [{r.room_id: r for r in h.rooms} for h in houses.values()]
    for r in houses["windows closed"].rooms:
        rm = by_id[r.room_id]
        name = f"{rm.label} (F{floor_no[rm.floor_id]})"
        print(f"  {name:22s} {r.outdoor_laeq_dba:7.1f}  " + "  ".join(f"{c[r.room_id].laeq_dba:15.1f}" for c in columns))

    rep = service.noise_report(args.property_id, room.room_id, NoiseScenario())
    print(f"\n{room.label} [{room.room_id}]: LAeq day {rep.laeq_day_dba}, night {rep.laeq_night_dba} dBA inside; "
          f"outside Lden {rep.outdoor_lden_dba}, Lnight {rep.outdoor_lnight_dba}")
    for s in rep.sources:
        print(f"  {s.contribution_dba:5.1f} dBA inside  ({s.outdoor_dba if s.outdoor_dba is not None else '':>5} outside)  {s.name}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = [OUT_DIR / "noise_site.png", OUT_DIR / f"noise_floors_{args.hour:02d}h.png", OUT_DIR / "noise_day.png"]
    site_map(twin, args.hour, paths[0])
    floors(twin, houses, args.hour, paths[1])
    day_chart(twin, room.room_id, room.label, paths[2])
    for p in paths:
        print(f"  plot {p}")


if __name__ == "__main__":
    main()
