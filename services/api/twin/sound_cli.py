"""A sound placed in the house: how loud it is in every room. Prints a table and draws both floors.

    python -m services.api.twin.sound_cli onXQLXhfyQv                      # TV in the largest living room
    python -m services.api.twin.sound_cli onXQLXhfyQv --room Bedroom --preset baby --doors open
    python -m services.api.twin.sound_cli onXQLXhfyQv --room <room_id> --x 1.2 --z -3.4 --level 85
"""

from __future__ import annotations

import argparse

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from shapely.geometry import Polygon  # noqa: E402

from packages.schema.reports import SoundField  # noqa: E402
from packages.schema.twin import PropertyTwin, Room  # noqa: E402
from services.api.integrations.config import REPO_ROOT  # noqa: E402
from services.api.twin import service  # noqa: E402

OUT_DIR = REPO_ROOT / "out" / "twin"
NORM = Normalize(vmin=15, vmax=85)
CMAP = plt.get_cmap("magma")
LEVEL_TICKS = {20: "barely audible", 30: "whisper", 40: "quiet room", 50: "soft talk",
               60: "conversation", 70: "loud TV", 80: "shouting"}


def pick_room(twin: PropertyTwin, room: str | None) -> Room:
    if room is None:
        living = [r for r in twin.rooms if r.label.lower() == "living room"] or twin.rooms
        return max(living, key=lambda r: r.area_m2 or Polygon(r.polygon).area)
    by_id = next((r for r in twin.rooms if r.room_id == room), None)
    if by_id:
        return by_id
    named = [r for r in twin.rooms if r.label.lower() == room.lower()]
    if not named:
        raise SystemExit(f"no room {room!r}; labels: {sorted({r.label for r in twin.rooms})}")
    return max(named, key=lambda r: r.area_m2 or Polygon(r.polygon).area)


def draw(twin: PropertyTwin, floor_index: int, f: SoundField, ax) -> None:
    floor = twin.floors[floor_index]
    levels = {r.room_id: r for r in f.rooms}
    for room in (r for r in twin.rooms if r.floor_id == floor.floor_id):
        xs, zs = zip(*(room.polygon + room.polygon[:1]))
        level = levels[room.room_id]
        ax.fill(xs, [-z for z in zs], color=CMAP(NORM(level.laeq_dba)), zorder=1)
        ax.plot(xs, [-z for z in zs], color="#ccc", lw=0.7, zorder=3)
        p = Polygon(room.polygon).representative_point()
        ax.text(p.x, -p.y, f"{room.label}\n{level.laeq_dba:.0f} dBA", ha="center", va="center", fontsize=6,
                zorder=4, color="white", bbox={"facecolor": "black", "alpha": 0.35, "pad": 1, "lw": 0})
    for w in twin.walls:
        if w.floor_id == floor.floor_id:
            (x0, z0), (x1, z1) = w.ends
            ax.plot([x0, x1], [-z0, -z1], color="#eee" if w.exterior else "#999", lw=2.2 if w.exterior else 1.2, zorder=3)
    for o in twin.openings:
        if any(r.room_id == o.room_id and r.floor_id == floor.floor_id for r in twin.rooms) and o.kind == "door":
            color = "#51cf66" if o.leaf is False or (o.leaf and f.doors == "open") else "#ff6b6b"
            ax.plot([o.polygon[0].x, o.polygon[1].x], [-o.polygon[0].z, -o.polygon[1].z], color=color, lw=3, zorder=5)
    for fo in twin.floor_openings:
        if fo.floor_id == floor.floor_id or any(
                r.room_id in fo.other_floor_room_ids and r.floor_id == floor.floor_id for r in twin.rooms):
            xs, zs = zip(*(fo.polygon + fo.polygon[:1]))
            ax.plot(xs, [-z for z in zs], color="#74c0fc", lw=1.5, ls="--", zorder=5)
    if any(r.room_id == f.source.room_id and r.floor_id == floor.floor_id for r in twin.rooms):
        ax.plot(f.source.position.x, -f.source.position.z, marker="*", color="#4dabf7", markersize=16,
                markeredgecolor="white", zorder=6)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_facecolor("#222")
    ax.set_title(f"{floor.label} · {f.source.preset_id} at {f.source.level_dba:.0f} dBA (1 m) · doors {f.doors}\n"
                 "red doors closed, green open or passages, dashed blue = stairwell", fontsize=9)
    bar = plt.colorbar(plt.cm.ScalarMappable(norm=NORM, cmap=CMAP), ax=ax, fraction=0.035, pad=0.02)
    bar.set_ticks(list(LEVEL_TICKS))
    bar.set_ticklabels([f"{k} dBA  {v}" for k, v in LEVEL_TICKS.items()], fontsize=7)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    parser.add_argument("--room", help="room id or label (largest room with that label)")
    parser.add_argument("--x", type=float)
    parser.add_argument("--z", type=float)
    parser.add_argument("--preset", default="tv", help=", ".join(p.preset_id for p in service.sound_presets()))
    parser.add_argument("--level", type=float, help="dBA at 1 m (default: the preset's)")
    parser.add_argument("--doors", choices=["closed", "open"], default="closed")
    parser.add_argument("--no-furniture", action="store_true")
    args = parser.parse_args()

    twin = service.get_twin(args.property_id)
    room = pick_room(twin, args.room)
    p = Polygon(room.polygon).representative_point()
    x = args.x if args.x is not None else p.x
    z = args.z if args.z is not None else p.y
    f = service.sound_field(args.property_id, room.room_id, x, z, args.preset, args.level, args.doors,
                            not args.no_furniture)

    by_id = {r.room_id: r for r in twin.rooms}
    floor_no = {fl.floor_id: fl.index + 1 for fl in twin.floors}
    print(f"{f.source.preset_id} ({f.source.level_dba:.0f} dBA at 1 m) in {room.label} "
          f"[{room.room_id}] at ({x:.2f}, {z:.2f}), doors {f.doors}")
    print(f"  {'room':28s} {'dBA':>5s}  {'RT60':>5s}  band gains vs. 1 m (63 Hz .. 8 kHz)")
    for r in f.rooms:
        rm = by_id[r.room_id]
        name = f"{rm.label} (F{floor_no[rm.floor_id]}, {rm.area_m2 or 0:.0f} m2)"
        gains = " ".join(f"{g:5.0f}" for g in r.gain_db)
        print(f"  {name:28s} {r.laeq_dba:5.1f}  {r.rt60_s:4.2f}s  {gains}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for fl in twin.floors:
        fig, ax = plt.subplots(figsize=(10, 8))
        draw(twin, fl.index, f, ax)
        path = OUT_DIR / f"sound_{f.source.preset_id}_{f.doors}_floor{fl.index}.png"
        fig.savefig(path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        print(f"  plot {path}")


if __name__ == "__main__":
    np.set_printoptions(precision=1)
    main()
