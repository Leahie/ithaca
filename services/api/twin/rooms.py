"""Room polygons from Matterport's wall graph.

A floor is a graph of 2D vertices joined by edges (walls, or invisible separators in open plans).
A room's boundary is an unordered set of those edges. It is not always a clean loop: it can carry
wall stubs that end inside the room, so the outline comes from the faces the edges enclose.
"""

from __future__ import annotations

from typing import Sequence

from shapely.geometry import LineString, MultiPolygon
from shapely.ops import polygonize, unary_union

Point = tuple[float, float]


def signed_area(polygon: Sequence[Point]) -> float:
    pts = list(polygon)
    return 0.5 * sum(
        x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])
    )


def outline_from_edges(edges: Sequence[tuple[str, str]], vertices: dict[str, Point]) -> list[Point]:
    """A room's outline, CCW in the model's (x, y) plan; [] if its edges enclose nothing.

    Dangling stubs enclose no face and drop out; if the edges enclose several faces, the outline
    is the largest piece of their union.
    """
    lines = [
        LineString([vertices[a], vertices[b]])
        for a, b in edges
        if a in vertices and b in vertices and vertices[a] != vertices[b]
    ]
    faces = list(polygonize(unary_union(lines))) if lines else []
    if not faces:
        return []
    merged = unary_union(faces)
    if isinstance(merged, MultiPolygon):
        merged = max(merged.geoms, key=lambda g: g.area)
    outline = _dedupe([(float(x), float(y)) for x, y in merged.exterior.coords])
    if len(outline) < 3:
        return []
    return outline if signed_area(outline) > 0 else outline[::-1]


def point_in_polygon(p: Point, polygon: Sequence[Point]) -> bool:
    x, y = p
    inside = False
    n = len(polygon)
    for i in range(n):
        x0, y0 = polygon[i]
        x1, y1 = polygon[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def start_at_min(polygon: list[Point]) -> list[Point]:
    """Rotate the vertex order so the outline starts at its lexicographically smallest point."""
    if not polygon:
        return polygon
    i = min(range(len(polygon)), key=lambda k: polygon[k])
    return polygon[i:] + polygon[:i]


def _dedupe(points: list[Point], eps: float = 1e-6) -> list[Point]:
    out: list[Point] = []
    for p in points:
        if not out or abs(p[0] - out[-1][0]) > eps or abs(p[1] - out[-1][1]) > eps:
            out.append(p)
    if len(out) > 1 and abs(out[0][0] - out[-1][0]) <= eps and abs(out[0][1] - out[-1][1]) <= eps:
        out.pop()
    return out
