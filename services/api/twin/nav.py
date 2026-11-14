"""Scan-point navigation graph: which sweeps you can walk between, so a viewer never crosses a wall.

Matterport's viewer only moves between scan positions along links whose straight path is clear.
The Model API does not expose those links, so they are rebuilt here by ray casting against the
interior mesh: two sweeps are linked when a body-wide corridor between them at eye height, and
a ray at knee height, are both unobstructed. Build-time (needs the MatterPak mesh); the result is
a fixture that ingest copies into `Sweep.neighbors`, so CI never needs the mesh.

    python -m services.api.twin.nav onXQLXhfyQv
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import trimesh

from services.api.integrations.config import fixtures_dir
from services.api.twin.mesh import MODEL_TO_INTERIOR, download_matterpak_mesh

log = logging.getLogger(__name__)

MAX_LINK_M = 6.0
MAX_NEIGHBORS = 10
HALF_WIDTH_M = 0.15  # a shoulder either side of the eye line
KNEE_DROP_M = 1.0  # below eye height: catches counters, beds, and walls under window sills
STAIR_RISE_M = 0.5  # past this height change the knee ray would hit the steps, so it is skipped
CLEARANCE_M = 0.1


def nav_path(property_id: str) -> Path:
    return fixtures_dir() / "twin" / "nav" / f"{property_id}.neighbors.json"


def load_neighbors(property_id: str) -> dict[str, list[str]]:
    path = nav_path(property_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def interior_mesh(model_id: str) -> trimesh.Trimesh:
    scene = trimesh.load(download_matterpak_mesh(model_id), force="scene", process=False)
    scene.apply_transform(MODEL_TO_INTERIOR)
    return trimesh.util.concatenate([g for g in scene.dump(concatenate=False)])


def neighbor_graph(sweep_ids: list[str], positions: np.ndarray, mesh: trimesh.Trimesh) -> dict[str, list[str]]:
    import open3d as o3d  # build-time only

    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.core.Tensor(np.asarray(mesh.vertices, np.float32)),
                        o3d.core.Tensor(np.asarray(mesh.faces, np.uint32)))

    i, j = np.triu_indices(len(positions), k=1)
    gap = np.linalg.norm(positions[j] - positions[i], axis=1)
    near = gap <= MAX_LINK_M
    i, j, gap = i[near], j[near], gap[near]

    direction = (positions[j] - positions[i]) / gap[:, None]
    side = np.cross(direction, [0.0, 1.0, 0.0])
    side_len = np.linalg.norm(side, axis=1, keepdims=True)
    side = np.divide(side, side_len, out=np.zeros_like(side), where=side_len > 1e-6) * HALF_WIDTH_M
    knee = np.array([0.0, -KNEE_DROP_M, 0.0])
    flat = np.abs(positions[j, 1] - positions[i, 1]) < STAIR_RISE_M

    def blocked(offset) -> np.ndarray:
        rays = np.hstack([positions[i] + offset, np.broadcast_to(direction, (len(i), 3))]).astype(np.float32)
        return scene.cast_rays(o3d.core.Tensor(rays))["t_hit"].numpy() < gap - CLEARANCE_M

    # A diagonal pass through a doorway grazes one jamb, so one shoulder may touch.
    clear = ~blocked(0.0) & ~(blocked(side) & blocked(-side)) & ~(blocked(knee) & flat)

    links: dict[str, list[tuple[float, str]]] = {s: [] for s in sweep_ids}
    for a, b, d in zip(i[clear], j[clear], gap[clear]):
        links[sweep_ids[a]].append((d, sweep_ids[b]))
        links[sweep_ids[b]].append((d, sweep_ids[a]))
    graph = {s: [n for _, n in sorted(v)[:MAX_NEIGHBORS]] for s, v in links.items()}
    # keep links symmetric after the per-sweep cap
    return {s: sorted(n for n in ns if s in graph[n]) for s, ns in graph.items()}


def components(graph: dict[str, list[str]]) -> list[set[str]]:
    seen: set[str] = set()
    out = []
    for start in graph:
        if start in seen:
            continue
        group, stack = set(), [start]
        while stack:
            s = stack.pop()
            if s in group:
                continue
            group.add(s)
            stack.extend(graph[s])
        seen |= group
        out.append(group)
    return sorted(out, key=len, reverse=True)


def build(property_id: str) -> dict[str, list[str]]:
    from services.api.twin.service import get_twin

    twin = get_twin(property_id)
    ids = [s.sweep_id for s in twin.sweeps]
    positions = np.array([[s.position.x, s.position.y, s.position.z] for s in twin.sweeps])
    graph = neighbor_graph(ids, positions, interior_mesh(twin.sources.matterport_model_id))
    path = nav_path(property_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return graph


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("property_id")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    graph = build(args.property_id)
    degree = [len(v) for v in graph.values()]
    groups = components(graph)
    print(f"wrote {nav_path(args.property_id)}")
    print(f"  {len(graph)} sweeps, {sum(degree) // 2} links, degree min {min(degree)} "
          f"median {sorted(degree)[len(degree) // 2]} max {max(degree)}")
    print(f"  {len(groups)} connected groups, sizes {[len(g) for g in groups]}")


if __name__ == "__main__":
    main()
