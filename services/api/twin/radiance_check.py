"""Validate the daylight engine against Radiance (backward ray tracing with full inter-reflection).

For one room and instant: build the room from the twin (floor, ceiling, walls with the real window
openings, glazing, the engine's reflectances), light it with Radiance's own Perez sky (gendaylit)
for the same sun position and irradiance, trace the engine's sample points with rtrace, and
compare. Needs `pip install pyradiance` (bundles the Radiance binaries); not used by the API.

    python -m services.api.twin.radiance_check onXQLXhfyQv --room <room_id> --at 2026-12-21T09:00
    python -m services.api.twin.radiance_check onXQLXhfyQv --default-cases
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.colors import LogNorm  # noqa: E402

from packages.schema.twin import PropertyTwin, Room  # noqa: E402
from services.api.integrations.config import REPO_ROOT  # noqa: E402
from services.api.sim import daylight, sun  # noqa: E402
from services.api.twin import service  # noqa: E402

OUT_DIR = REPO_ROOT / "out" / "twin" / "radiance"
RTRACE = ["-I+", "-h", "-ab", "5", "-ad", "2048", "-as", "512", "-aa", "0.12", "-ar", "256", "-lw", "2e-4"]
EDGE_TOLERANCE_M = 0.35


@dataclass
class Case:
    room_label: str  # "Floor 1/Bedroom" or a room_id
    when: str
    sky: str = "clear"  # clear | overcast


DEFAULT_CASES = [
    Case("Floor 1/Bedroom", "2026-12-21T09:00"),
    Case("Floor 1/Bedroom", "2026-12-21T09:00", "overcast"),
    Case("Floor 1/Kitchen", "2026-12-21T12:30"),
    Case("Floor 2/Bedroom", "2026-06-21T17:00"),
]


def _radiance_env() -> tuple[Path, dict]:
    import pyradiance

    root = Path(pyradiance.__file__).parent
    env = dict(os.environ)
    env["RAYPATH"] = os.pathsep.join([".", str(root / "lib")])
    env["PATH"] = os.pathsep.join([str(root / "bin"), env.get("PATH", "")])
    return root / "bin", env


def _run(bin_dir: Path, env: dict, args: list[str], stdin: bytes | None = None, cwd: Path | None = None) -> bytes:
    exe = bin_dir / (args[0] + ".exe" if os.name == "nt" else args[0])
    result = subprocess.run([str(exe), *args[1:]], input=stdin, capture_output=True, env=env, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.decode(errors='replace')}")
    return result.stdout


def _tn(transmittance: float) -> float:
    """Radiance glass transmissivity for a given normal-incidence transmittance."""
    t = transmittance
    return (np.sqrt(0.8402528435 + 0.0072522239 * t * t) - 0.9166530661) / 0.0036261119 / t


def _poly(material: str, name: str, pts: np.ndarray) -> str:
    coords = " ".join(f"{v:.5f}" for p in pts for v in p)
    return f"{material} polygon {name}\n0\n0\n{3 * len(pts)} {coords}\n"


def room_scene(twin: PropertyTwin, room: Room) -> tuple[str, list[str]]:
    """Radiance scene text in local ENU (x east, y north, z up), plus warnings."""
    rotation = np.asarray(twin.interior_to_world, dtype=float).reshape(4, 4).T[:3, :3]

    def enu(x, y, z):
        return rotation @ np.array([x, y, z])

    rho_f = daylight.FLOOR_REFLECTANCE.get(room.floor_material, daylight.DEFAULT_FLOOR_REFLECTANCE)
    out = [
        f"void plastic floor_m\n0\n0\n5 {rho_f} {rho_f} {rho_f} 0 0\n",
        f"void plastic wall_m\n0\n0\n5 {daylight.WALL_REFLECTANCE} {daylight.WALL_REFLECTANCE} {daylight.WALL_REFLECTANCE} 0 0\n",
        f"void plastic ceil_m\n0\n0\n5 {daylight.CEILING_REFLECTANCE} {daylight.CEILING_REFLECTANCE} {daylight.CEILING_REFLECTANCE} 0 0\n",
    ]
    y0, y1 = room.floor_y, room.floor_y + room.height_m
    ring = list(room.polygon)
    out.append(_poly("floor_m", "floor", np.array([enu(x, y0, z) for x, z in ring])))
    out.append(_poly("ceil_m", "ceiling", np.array([enu(x, y1, z) for x, z in reversed(ring)])))

    windows = [o for o in twin.openings if o.room_id == room.room_id and o.kind in sun.TRANSPARENT_KINDS
               and sun.is_exterior(o)]
    edges = [(np.array(ring[i]), np.array(ring[(i + 1) % len(ring)])) for i in range(len(ring))]
    on_edge: dict[int, list] = {i: [] for i in range(len(edges))}
    warnings = []
    for o in windows:
        p0, p1 = np.array([o.polygon[0].x, o.polygon[0].z]), np.array([o.polygon[1].x, o.polygon[1].z])
        best = None
        for i, (a, b) in enumerate(edges):
            length = np.linalg.norm(b - a)
            t = (b - a) / length
            n = np.array([-t[1], t[0]])
            dist = max(abs((p0 - a) @ n), abs((p1 - a) @ n))
            u = sorted([(p0 - a) @ t, (p1 - a) @ t])
            overlap = min(u[1], length) - max(u[0], 0)
            if dist < EDGE_TOLERANCE_M and overlap > 0.1 and (best is None or dist < best[0]):
                best = (dist, i, max(u[0], 0.0), min(u[1], length))
        if best is None:
            warnings.append(f"window {o.opening_id} is not on the room outline; left out")
            continue
        _, i, u0, u1 = best
        on_edge[i].append((u0, u1, o.polygon[0].y - y0, o.polygon[3].y - y0, o.glazing))

    tau_glass = {}
    for i, (a, b) in enumerate(edges):
        length = np.linalg.norm(b - a)
        t = (b - a) / length
        h = room.height_m

        def rect(u_lo, u_hi, h_lo, h_hi):
            return np.array([enu(*((a + u * t)[[0]].tolist() + [y0 + hh] + (a + u * t)[[1]].tolist()))
                             for u, hh in [(u_lo, h_lo), (u_hi, h_lo), (u_hi, h_hi), (u_lo, h_hi)]])

        cursor = 0.0
        for k, (u0, u1, sill, head) in enumerate(sorted((w[0], w[1], w[2], w[3]) for w in on_edge[i])):
            if u0 > cursor + 1e-3:
                out.append(_poly("wall_m", f"wall_{i}_{k}_a", rect(cursor, u0, 0, h)))
            if sill > 1e-3:
                out.append(_poly("wall_m", f"wall_{i}_{k}_below", rect(u0, u1, 0, sill)))
            if head < h - 1e-3:
                out.append(_poly("wall_m", f"wall_{i}_{k}_above", rect(u0, u1, head, h)))
            glazing = next(w[4] for w in on_edge[i] if w[0] == u0)
            transmittance = daylight.GLASS_TRANSMITTANCE[glazing] * daylight.WINDOW_FACTOR
            if glazing not in tau_glass:
                tau_glass[glazing] = transmittance
                tn = _tn(transmittance)
                out.insert(0, f"void glass glass_{glazing}\n0\n0\n3 {tn:.4f} {tn:.4f} {tn:.4f}\n")
            out.append(_poly(f"glass_{glazing}", f"glass_{i}_{k}", rect(u0, u1, sill, head)))
            cursor = max(cursor, u1)
        if cursor < length - 1e-3:
            out.append(_poly("wall_m", f"wall_{i}_end", rect(cursor, length, 0, h)))
    return "".join(out), warnings


def compare(twin: PropertyTwin, room: Room, when: str, sky: str, height_m: float = 0.8) -> dict:
    times = daylight.single_time(when, twin.timezone)
    weather = service.weather(twin.property_id, times, "clear")
    if sky == "overcast":
        weather = daylight.Weather(times, np.zeros(1), np.full(1, 250.0), np.full(1, 250.0), weather.albedo, "clear")
    instant = daylight._instants(twin, weather)[0]
    coefficients = daylight.room_coefficients(twin, room, height_m)
    engine, lit, reflected = daylight._room_lux(coefficients, instant)

    bin_dir, env = _radiance_env()
    rotation = np.asarray(twin.interior_to_world, dtype=float).reshape(4, 4).T[:3, :3]
    scene, warnings = room_scene(twin, room)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "scene.rad").write_text(scene, encoding="utf-8")
        dn_lux = instant.dni * daylight.EFFICACY_BEAM
        dh_lux = instant.dhi * daylight.EFFICACY_DIFFUSE
        sky_args = ["gendaylit", "-ang", f"{instant.elevation:.4f}", f"{instant.azimuth - 180:.4f}",
                    "-L", f"{max(dn_lux, 0):.1f}", f"{max(dh_lux, 1e-3):.1f}", "-g", f"{instant.albedo:.3f}"]
        sky_text = _run(bin_dir, env, sky_args, cwd=tmp).decode()
        sky_text += ("\nskyfunc glow sky_glow\n0\n0\n4 1 1 1 0\nsky_glow source sky\n0\n0\n4 0 0 1 180\n"
                     "\nskyfunc glow ground_glow\n0\n0\n4 1 1 1 0\nground_glow source ground\n0\n0\n4 0 0 -1 180\n")
        (tmp / "sky.rad").write_text(sky_text, encoding="utf-8")
        octree = _run(bin_dir, env, ["oconv", "sky.rad", "scene.rad"], cwd=tmp)
        (tmp / "scene.oct").write_bytes(octree)
        sensors = (coefficients.points @ rotation.T)
        rays = "".join(f"{p[0]:.4f} {p[1]:.4f} {p[2]:.4f} 0 0 1\n" for p in sensors).encode()
        raw = _run(bin_dir, env, ["rtrace", *RTRACE, "scene.oct"], stdin=rays, cwd=tmp).decode().split()
    rgb = np.array(raw, dtype=float).reshape(-1, 3)
    radiance = 179.0 * (0.265 * rgb[:, 0] + 0.670 * rgb[:, 1] + 0.065 * rgb[:, 2])

    shade = ~lit
    stats = {
        "room": room.room_id, "when": str(instant.ts), "sky": sky, "height_m": height_m,
        "elevation_deg": round(instant.elevation, 1), "azimuth_deg": round(instant.azimuth, 1),
        "engine_median_lux": round(float(np.median(engine)), 1),
        "radiance_median_lux": round(float(np.median(radiance)), 1),
        "engine_mean_lux": round(float(engine.mean()), 1),
        "radiance_mean_lux": round(float(radiance.mean()), 1),
        "engine_reflected_lux": round(reflected, 1),
        "out_of_sun_median_ratio": round(float(np.median(engine[shade] / np.maximum(radiance[shade], 1e-6))), 3)
        if shade.any() else None,
        "sunlit_median_ratio": round(float(np.median(engine[lit] / np.maximum(radiance[lit], 1e-6))), 3)
        if lit.any() else None,
        "warnings": warnings,
    }
    return stats, engine, radiance, coefficients


def plot(twin, room, stats, engine, radiance, c, path: Path) -> None:
    norm = LogNorm(vmin=20, vmax=20000)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.8))
    for ax, values, title in [(axes[0], engine, "daylight engine"), (axes[1], radiance, "Radiance (rtrace -ab 5)")]:
        grid = np.full(c.inside.shape, np.nan)
        grid[c.inside] = values
        rows, cols = grid.shape
        x0, z0 = c.origin
        image = ax.imshow(np.ma.masked_invalid(np.maximum(grid, 1)), cmap="inferno", norm=norm, origin="upper",
                          extent=(x0, x0 + cols * daylight.CELL_M, -(z0 + rows * daylight.CELL_M), -z0))
        xs, zs = zip(*(room.polygon + room.polygon[:1]))
        ax.plot(xs, [-z for z in zs], color="white", lw=0.8)
        ax.set_title(f"{title}: median {np.median(values):.0f} lx", fontsize=10)
        ax.set_xticks([])
        ax.set_yticks([])
    plt.colorbar(image, ax=axes[:2], fraction=0.025, label="lux")
    ax = axes[2]
    ax.loglog(radiance, engine, ".", ms=3, alpha=0.5)
    lo, hi = 10, max(engine.max(), radiance.max(), 100) * 1.2
    ax.plot([lo, hi], [lo, hi], "k--", lw=0.8)
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_xlabel("Radiance (lux)")
    ax.set_ylabel("engine (lux)")
    ax.set_title("per sample point", fontsize=10)
    fig.suptitle(f"{room.label} · {stats['when']} · {stats['sky']} sky · sun {stats['elevation_deg']}° up", fontsize=11)
    fig.savefig(path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def find_room(twin: PropertyTwin, label: str) -> Room:
    if "/" not in label:
        return next(r for r in twin.rooms if r.room_id == label)
    floor_label, room_label = label.split("/", 1)
    floor = next(f for f in twin.floors if f.label == floor_label)
    candidates = [r for r in twin.rooms if r.floor_id == floor.floor_id and r.label == room_label
                  and sun.room_windows(twin, r.room_id)]
    return max(candidates, key=lambda r: len(sun.room_windows(twin, r.room_id)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    parser.add_argument("--room", help="room_id or 'Floor 1/Bedroom'")
    parser.add_argument("--at")
    parser.add_argument("--sky", choices=["clear", "overcast"], default="clear")
    parser.add_argument("--default-cases", action="store_true")
    args = parser.parse_args()
    twin = service.get_twin(args.property_id)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cases = DEFAULT_CASES if args.default_cases else [Case(args.room, args.at, args.sky)]
    results = []
    for case in cases:
        room = find_room(twin, case.room_label)
        stats, engine, radiance, c = compare(twin, room, case.when, case.sky)
        stats["room_label"] = case.room_label
        path = OUT_DIR / f"{room.room_id}_{case.when.replace(':', '')}_{case.sky}.png"
        plot(twin, room, stats, engine, radiance, c, path)
        stats["plot"] = str(path.relative_to(REPO_ROOT))
        results.append(stats)
        print(json.dumps(stats))
    (OUT_DIR / "summary.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
