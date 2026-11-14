"""Plan drawings of a PropertyTwin, one PNG per floor, for checking ingestion by eye."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from packages.schema.twin import PropertyTwin  # noqa: E402
from services.api.integrations.config import REPO_ROOT  # noqa: E402
from services.api.twin import frames  # noqa: E402

OPENING_COLORS = {"window": "#1f77b4", "door": "#8c564b", "slider": "#17becf"}


def plot_floors(twin: PropertyTwin, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or REPO_ROOT / "out" / "twin"
    out_dir.mkdir(parents=True, exist_ok=True)
    world = frames.from_column_major(twin.interior_to_world)
    to_interior = frames.invert_rigid(world)
    north_xz = (to_interior[:3, :3] @ np.array([0.0, 1.0, 0.0]))[[0, 2]]
    paths = []
    for floor in twin.floors:
        fig, ax = plt.subplots(figsize=(11, 9))
        for room in (r for r in twin.rooms if r.floor_id == floor.floor_id):
            xs, ys = zip(*[(x, -z) for x, z in room.polygon + room.polygon[:1]])
            ax.fill(xs, ys, alpha=0.18)
            ax.plot(xs, ys, color="#333", lw=1)
            cx, cy = np.mean(xs[:-1]), np.mean(ys[:-1])
            ax.text(cx, cy, f"{room.label}\n{room.area_m2:.1f} m²", ha="center", va="center", fontsize=7)
        floor_rooms = {r.room_id for r in twin.rooms if r.floor_id == floor.floor_id}
        for op in (o for o in twin.openings if o.room_id in floor_rooms):
            p0, p1 = op.polygon[0], op.polygon[1]
            ax.plot([p0.x, p1.x], [-p0.z, -p1.z], color=OPENING_COLORS[op.kind], lw=4, solid_capstyle="butt")
        sweeps = [s for s in twin.sweeps if s.floor_id == floor.floor_id]
        ax.scatter([s.position.x for s in sweeps], [-s.position.z for s in sweeps], s=10, c="k", zorder=3)
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        base = np.array([x1 - 0.08 * (x1 - x0), y0 + 0.1 * (y1 - y0)])
        tip = base + 0.06 * (x1 - x0) * np.array([north_xz[0], -north_xz[1]])
        ax.annotate("N", xy=tip, xytext=base, ha="center", arrowprops={"arrowstyle": "->", "lw": 2})
        ax.set_aspect("equal")
        ax.set_title(f"{twin.address} · {floor.label or floor.floor_id} (elevation {floor.elevation_m} m)\n"
                     "rooms, windows (blue), doors (brown), scan positions (dots)", fontsize=10)
        ax.set_xlabel("interior x (m)")
        ax.set_ylabel("interior -z (m)")
        path = out_dir / f"{twin.property_id}_floor{floor.index}.png"
        fig.savefig(path, dpi=110, bbox_inches="tight")
        plt.close(fig)
        paths.append(path)
    return paths
