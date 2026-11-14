import pytest

from services.api.twin import rooms

SQUARE = {"a": (0.0, 0.0), "b": (2.0, 0.0), "c": (2.0, 2.0), "d": (0.0, 2.0)}


def test_outline_from_shuffled_edges_is_ccw():
    edges = [("c", "b"), ("a", "d"), ("b", "a"), ("c", "d")]
    outline = rooms.outline_from_edges(edges, SQUARE)
    assert len(outline) == 4
    assert rooms.signed_area(outline) == pytest.approx(4.0)


def test_wall_stub_inside_room_is_ignored():
    vertices = {**SQUARE, "s": (1.0, 2.0), "t": (1.0, 1.2)}
    edges = [("a", "b"), ("b", "c"), ("c", "s"), ("s", "d"), ("d", "a"), ("s", "t")]
    assert rooms.signed_area(rooms.outline_from_edges(edges, vertices)) == pytest.approx(4.0)


def test_open_boundary_has_no_outline():
    assert rooms.outline_from_edges([("a", "b"), ("b", "c"), ("c", "d")], SQUARE) == []


def test_point_in_polygon():
    square = list(SQUARE.values())
    assert rooms.point_in_polygon((1.0, 1.0), square)
    assert not rooms.point_in_polygon((3.0, 1.0), square)


def test_start_at_min():
    assert rooms.start_at_min([(2, 0), (0, 0), (1, 1)]) == [(0, 0), (1, 1), (2, 0)]
