"""Sun inside the house, at the property's local time.

    python -m services.api.twin.sun_cli onXQLXhfyQv --at 2026-12-21T09:00          # one moment, plotted
    python -m services.api.twin.sun_cli onXQLXhfyQv --day 2026-12-21 [--gif]        # sun times per room
    python -m services.api.twin.sun_cli onXQLXhfyQv --reference                     # fixtures/sun/reference.json
"""

from __future__ import annotations

import argparse
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from PIL import Image  # noqa: E402

from packages.schema.reports import SunMoment  # noqa: E402
from packages.schema.twin import PropertyTwin  # noqa: E402
from services.api.integrations.config import REPO_ROOT, fixtures_dir  # noqa: E402
from services.api.sim import sun  # noqa: E402
from services.api.twin.service import get_twin  # noqa: E402

REFERENCE_DATES = ["2026-03-20", "2026-06-21", "2026-09-22", "2026-12-21"]
REFERENCE_HOURS = [8, 10, 12, 14, 16, 18]
OUT_DIR = REPO_ROOT / "out" / "twin"
HEIGHTS = {"floor": 0.0, "seated": sun.SEATED_M}
HEIGHT_COLORS = {"floor": "#ffb000", "seated": "#e8590c"}


def reference_cases(twin: PropertyTwin) -> dict:
    times = pd.DatetimeIndex(
        [pd.Timestamp(f"{d} {h:02d}:00").tz_localize(twin.timezone) for d in REFERENCE_DATES for h in REFERENCE_HOURS]
    )
    pos = sun.sun_positions(times, twin.lat, twin.lon)
    return {
        "about": "pvlib solar position at the property; A3: the client's sun direction must match within 0.5 deg. "
        "azimuth is degrees clockwise from true north; elevation is geometric, apparent_elevation adds refraction.",
        "lat": twin.lat,
        "lon": twin.lon,
        "timezone": twin.timezone,
        "cases": [
            {
                "datetime": t.isoformat(),
                "azimuth_deg": round(float(row.azimuth), 3),
                "elevation_deg": round(float(row.elevation), 3),
                "apparent_elevation_deg": round(float(row.apparent_elevation), 3),
            }
            for t, row in zip(times, pos.itertuples())
        ],
    }


def draw_moment(twin: PropertyTwin, floor_index: int, moments: dict[str, SunMoment], ax) -> None:
    floor = twin.floors[floor_index]
    rooms = [r for r in twin.rooms if r.floor_id == floor.floor_id]
    room_ids = {r.room_id for r in rooms}
    for room in rooms:
        xs, zs = zip(*(room.polygon + room.polygon[:1]))
        ax.fill(xs, [-z for z in zs], color="#f1f1f1")
        ax.plot(xs, [-z for z in zs], color="#444", lw=0.8)
        ax.text(np.mean(xs[:-1]), -np.mean(zs[:-1]), room.label, ha="center", va="center", fontsize=6, color="#555")
    for name in ("floor", "seated"):
        for patch in moments[name].rooms:
            if patch.room_id not in room_ids or patch.mask is None:
                continue
            grid = np.ma.masked_equal(np.asarray(patch.mask.lit, dtype=float), 0.0)
            rows, cols = grid.shape
            x0, z0 = patch.mask.origin_xz
            c = patch.mask.cell_m
            ax.imshow(grid, extent=(x0, x0 + cols * c, -(z0 + rows * c), -z0), origin="upper",
                      cmap=matplotlib.colors.ListedColormap([HEIGHT_COLORS[name]]), alpha=0.75 if name == "floor" else 0.55,
                      interpolation="nearest", zorder=2)
    for o in twin.openings:
        if o.kind in sun.TRANSPARENT_KINDS and o.room_id in room_ids:
            ax.plot([o.polygon[0].x, o.polygon[1].x], [-o.polygon[0].z, -o.polygon[1].z], color="#1f77b4", lw=3, zorder=3)
    m = moments["floor"]
    if m.direction_interior is not None:
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        d = np.array([m.direction_interior.x, -m.direction_interior.z])
        if np.linalg.norm(d) > 1e-6:
            d = d / np.linalg.norm(d)
            centre = np.array([x0 + 0.1 * (x1 - x0), y1 - 0.12 * (y1 - y0)])
            ax.annotate("", xy=centre, xytext=centre + d * 0.07 * (x1 - x0),
                        arrowprops={"arrowstyle": "->", "color": "#e8590c", "lw": 2.5})
            ax.text(*(centre + d * 0.09 * (x1 - x0)), "sun", color="#e8590c", ha="center", va="center", fontsize=9)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    when = pd.Timestamp(m.datetime)
    state = f"sun {m.elevation_deg:.0f}° up, azimuth {m.azimuth_deg:.0f}°" if m.sun_up else "sun below the horizon"
    ax.set_title(f"{floor.label} · {when.strftime('%a %b %d %Y, %I:%M %p %Z')} · {state}\n"
                 "yellow: sun on the floor · orange: sun at seated height (1.1 m)", fontsize=9)


def moments_at(twin: PropertyTwin, when) -> dict[str, SunMoment]:
    return {name: sun.sun_at(twin, when, height_m=h) for name, h in HEIGHTS.items()}


def plot_moment(twin: PropertyTwin, when: datetime) -> list[Path]:
    moments = moments_at(twin, when)
    paths = []
    for floor in twin.floors:
        fig, ax = plt.subplots(figsize=(10, 8))
        draw_moment(twin, floor.index, moments, ax)
        path = OUT_DIR / f"sun_at_{pd.Timestamp(when).strftime('%Y-%m-%dT%H%M')}_floor{floor.index}.png"
        fig.savefig(path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)
    return paths


def day_gif(twin: PropertyTwin, day: date, step_min: int = 15) -> list[Path]:
    times = sun.day_times(day, twin.timezone, step_min)
    pos = sun.sun_positions(times, twin.lat, twin.lon)
    times = times[pos["apparent_elevation"].to_numpy() > -2]
    moments = [moments_at(twin, t) for t in times]
    paths = []
    for floor in twin.floors:
        frames = []
        for m in moments:
            fig, ax = plt.subplots(figsize=(8, 6.5))
            draw_moment(twin, floor.index, m, ax)
            buffer = io.BytesIO()
            fig.savefig(buffer, dpi=80, bbox_inches="tight")
            plt.close(fig)
            buffer.seek(0)
            frames.append(Image.open(buffer).convert("P", palette=Image.ADAPTIVE))
        path = OUT_DIR / f"sun_day_{day.isoformat()}_floor{floor.index}.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:], duration=350, loop=0)
        paths.append(path)
    return paths


def fmt(intervals) -> str:
    return ", ".join(f"{pd.Timestamp(i.start).strftime('%H:%M')}-{pd.Timestamp(i.end).strftime('%H:%M')}"
                     for i in intervals) or "-"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    parser.add_argument("--at", action="append", default=[], help="local datetime, e.g. 2026-12-21T09:00; repeatable")
    parser.add_argument("--day", action="append", default=[], help="YYYY-MM-DD; repeatable")
    parser.add_argument("--gif", action="store_true", help="with --day: animate the day for each floor")
    parser.add_argument("--reference", action="store_true", help="write fixtures/sun/reference.json")
    args = parser.parse_args()
    twin = get_twin(args.property_id)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.reference:
        path = fixtures_dir() / "sun" / "reference.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reference_cases(twin), indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {path}")

    for when in args.at:
        for name, m in moments_at(twin, when).items():
            labels = {r.room_id: r.label for r in twin.rooms}
            lit = ", ".join(f"{labels[p.room_id]} {p.lit_area_m2:.1f} m²" for p in m.rooms) or "none"
            print(f"{pd.Timestamp(m.datetime).isoformat()} {name:6s} sun {m.elevation_deg:5.1f}° up: {lit}")
        for p in plot_moment(twin, datetime.fromisoformat(when)):
            print(f"plot {p}")

    floors = {f.floor_id: f.label for f in twin.floors}
    for d in args.day:
        day = datetime.strptime(d, "%Y-%m-%d").date()
        print(f"\n{day.isoformat()} ({twin.timezone}): when direct sun reaches each room")
        print(f"{'room':34s} {'on the floor':28s} at seated height")
        for r in twin.rooms:
            floor_day = sun.sun_room_day(twin, r.room_id, day, height_m=0.0)
            seated_day = sun.sun_room_day(twin, r.room_id, day, height_m=sun.SEATED_M)
            if not floor_day.intervals and not seated_day.intervals:
                continue
            name = f"{floors[r.floor_id]} · {r.label} ({r.area_m2:.0f} m²)"
            print(f"{name:34s} {fmt(floor_day.intervals):28s} {fmt(seated_day.intervals)}")
        if args.gif:
            for p in day_gif(twin, day):
                print(f"gif {p}")


if __name__ == "__main__":
    main()
