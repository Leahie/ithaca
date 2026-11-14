"""Daylight (sky + sun + reflected light) inside the house, at the property's local time.

    python -m services.api.twin.daylight_cli onXQLXhfyQv --at 2026-12-21T09:00 [--sky typical]
    python -m services.api.twin.daylight_cli onXQLXhfyQv --day 2026-12-21 --gif [--sky typical]
"""

from __future__ import annotations

import argparse
import io
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402
from PIL import Image  # noqa: E402

from packages.schema.reports import DaylightMoment  # noqa: E402
from packages.schema.twin import PropertyTwin  # noqa: E402
from services.api.integrations.config import REPO_ROOT  # noqa: E402
from services.api.sim import sun  # noqa: E402
from services.api.twin import service  # noqa: E402

OUT_DIR = REPO_ROOT / "out" / "twin"
NORM = LogNorm(vmin=20, vmax=20000)
CMAP = plt.get_cmap("inferno")
LEVEL_TICKS = {30: "dark", 150: "dim", 500: "moderate", 1500: "bright", 10000: "very bright\n(direct sun)"}


def draw(twin: PropertyTwin, floor_index: int, m: DaylightMoment, ax, colorbar: bool = True) -> None:
    floor = twin.floors[floor_index]
    rooms = [r for r in twin.rooms if r.floor_id == floor.floor_id]
    room_ids = {r.room_id for r in rooms}
    reflected = {r.room_id: r.reflected_lux for r in m.rooms}
    for room in rooms:
        xs, zs = zip(*(room.polygon + room.polygon[:1]))
        base = CMAP(NORM(max(reflected[room.room_id], 1.0))) if room.room_id in reflected else "#000"
        ax.fill(xs, [-z for z in zs], color=base, zorder=1)
    image = None
    for r in m.rooms:
        if r.room_id not in room_ids or r.grid is None:
            continue
        grid = np.array([[np.nan if v is None else max(v, 1.0) for v in row] for row in r.grid.lux])
        rows, cols = grid.shape
        x0, z0 = r.grid.origin_xz
        c = r.grid.cell_m
        image = ax.imshow(np.ma.masked_invalid(grid), extent=(x0, x0 + cols * c, -(z0 + rows * c), -z0),
                          origin="upper", cmap=CMAP, norm=NORM, interpolation="bilinear", zorder=2)
    labels = {r.room_id: r for r in m.rooms}
    for room in rooms:
        xs, zs = zip(*(room.polygon + room.polygon[:1]))
        ax.plot(xs, [-z for z in zs], color="#bbb", lw=0.7, zorder=3)
        info = labels.get(room.room_id)
        text = f"{room.label}\n{info.median_lux:.0f} lx" if info else f"{room.label}\n(no windows)"
        ax.text(np.mean(xs[:-1]), -np.mean(zs[:-1]), text, ha="center", va="center", fontsize=6, zorder=4,
                color="white", bbox={"facecolor": "black", "alpha": 0.35, "pad": 1, "lw": 0})
    for o in twin.openings:
        if o.kind in sun.TRANSPARENT_KINDS and o.room_id in room_ids:
            ax.plot([o.polygon[0].x, o.polygon[1].x], [-o.polygon[0].z, -o.polygon[1].z], color="#4dabf7", lw=3, zorder=5)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor("#222")
    when = pd.Timestamp(m.datetime)
    sky = "clear day" if m.sky == "clear" else "typical weather"
    state = (f"sun {m.elevation_deg:.0f}° up · outside {m.outdoor_lux:,.0f} lx" if m.sun_up else "night")
    ax.set_title(f"{floor.label} · {when.strftime('%a %b %d, %I:%M %p %Z')} · {sky} · {state}\n"
                 f"daylight at {m.height_m:.1f} m (sky + sun + light reflected inside the room)", fontsize=9)
    if colorbar:
        mappable = image or plt.cm.ScalarMappable(norm=NORM, cmap=CMAP)
        bar = plt.colorbar(mappable, ax=ax, fraction=0.035, pad=0.02)
        bar.set_ticks(list(LEVEL_TICKS))
        bar.set_ticklabels([f"{k:,} lx  {v}" for k, v in LEVEL_TICKS.items()], fontsize=7)


def plot_at(twin: PropertyTwin, when: str, sky: str, height_m: float) -> list:
    m = service.daylight_at(twin.property_id, when, sky=sky, height_m=height_m)
    paths = []
    for floor in twin.floors:
        fig, ax = plt.subplots(figsize=(10, 8))
        draw(twin, floor.index, m, ax)
        path = OUT_DIR / f"daylight_{sky}_{pd.Timestamp(m.datetime).strftime('%Y-%m-%dT%H%M')}_floor{floor.index}.png"
        fig.savefig(path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)
    return paths, m


def day_gif(twin: PropertyTwin, day, sky: str, height_m: float, step_min: int = 20) -> list:
    times = sun.day_times(day, twin.timezone, step_min)
    pos = sun.sun_positions(times, twin.lat, twin.lon)
    times = times[np.asarray(pos["apparent_elevation"] > -3)]
    moments = [service.daylight_at(twin.property_id, t.isoformat(), sky=sky, height_m=height_m) for t in times]
    paths = []
    for floor in twin.floors:
        frames = []
        for m in moments:
            fig, ax = plt.subplots(figsize=(8.5, 6.5))
            draw(twin, floor.index, m, ax)
            buffer = io.BytesIO()
            fig.savefig(buffer, dpi=80, bbox_inches="tight")
            plt.close(fig)
            buffer.seek(0)
            frames.append(Image.open(buffer).convert("RGB").quantize(colors=128))
        path = OUT_DIR / f"daylight_{sky}_{day.isoformat()}_floor{floor.index}.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=300, loop=0)
        paths.append(path)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    parser.add_argument("--at", action="append", default=[], help="local datetime, e.g. 2026-12-21T09:00")
    parser.add_argument("--day", action="append", default=[], help="YYYY-MM-DD, animate with --gif")
    parser.add_argument("--gif", action="store_true")
    parser.add_argument("--sky", choices=["clear", "typical"], default="clear")
    parser.add_argument("--height", type=float, default=0.8, help="sample height above the floor (m); 0.8 = desk")
    args = parser.parse_args()
    twin = service.get_twin(args.property_id)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    labels = {r.room_id: r.label for r in twin.rooms}
    floors = {r.room_id: r.floor_id for r in twin.rooms}
    floor_labels = {f.floor_id: f.label for f in twin.floors}

    for when in args.at:
        paths, m = plot_at(twin, when, args.sky, args.height)
        print(f"{pd.Timestamp(m.datetime).isoformat()} {m.sky}: outside {m.outdoor_lux:,.0f} lx "
              f"(DNI {m.dni_w_m2:.0f}, DHI {m.dhi_w_m2:.0f} W/m²)")
        for r in m.rooms:
            name = f"{floor_labels[floors[r.room_id]]} · {labels[r.room_id]}"
            print(f"  {name:24s} median {r.median_lux:7.0f} lx  max {r.max_lux:7.0f}  reflected {r.reflected_lux:5.0f}"
                  f"  sun {r.sun_area_m2:4.1f} m²  {r.level}")
        for p in paths:
            print(f"plot {p}")
    for d in args.day:
        day = datetime.strptime(d, "%Y-%m-%d").date()
        if args.gif:
            for p in day_gif(twin, day, args.sky, args.height):
                print(f"gif {p}")


if __name__ == "__main__":
    main()
