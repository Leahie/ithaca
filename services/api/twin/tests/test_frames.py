import math

import numpy as np
import pytest

from services.api.twin import frames


def test_model_interior_round_trip():
    p = (1.5, -2.0, 0.75)
    assert frames.model_to_interior(p) == (1.5, 0.75, 2.0)
    assert frames.interior_to_model(frames.model_to_interior(p)) == p


def test_interior_to_world_axes_with_zero_heading():
    m = frames.interior_to_world_matrix(0.0, (0.0, 0.0))
    assert np.allclose(frames.apply(m, (1, 0, 0)), (1, 0, 0))  # interior +X -> east
    assert np.allclose(frames.apply(m, (0, 0, -1)), (0, 1, 0))  # interior -Z (model +Y) -> north
    assert np.allclose(frames.apply(m, (0, 1, 0)), (0, 0, 1))  # interior +Y -> up


def test_interior_world_interior_round_trip():
    m = frames.interior_to_world_matrix(math.radians(37.0), (5.9, -8.3))
    p = np.array([-3.2, 1.4, 7.7])
    back = frames.apply(frames.invert_rigid(m), frames.apply(m, p))
    assert np.allclose(back, p)


def test_column_major_matches_three_js_order():
    m = frames.interior_to_world_matrix(0.3, (5.0, -8.0))
    values = frames.to_column_major(m)
    assert values[12:15] == [5.0, -8.0, 0.0]  # translation is the last column
    assert np.allclose(frames.from_column_major(values), m)


def test_yaw_from_quaternion():
    half = math.radians(30.0) / 2
    assert frames.yaw_from_quaternion(math.cos(half), 0, 0, math.sin(half)) == pytest.approx(math.radians(30.0))


@pytest.mark.parametrize("east,north,expected", [(0, 1, 0), (1, 0, 90), (0, -1, 180), (-1, 0, 270)])
def test_compass_azimuth(east, north, expected):
    assert frames.compass_azimuth_deg(east, north) == pytest.approx(expected)


def test_enu_latlon_round_trip():
    lat, lon = frames.enu_to_latlon(12.0, -7.5, 29.678, -95.462)
    east, north = frames.latlon_to_enu(lat, lon, 29.678, -95.462)
    assert (east, north) == pytest.approx((12.0, -7.5), abs=1e-6)


def test_ccw_in_model_plan_is_ccw_from_above_in_interior():
    model_ccw = [(0, 0), (2, 0), (2, 1), (0, 1)]
    interior = [frames.model_xy_to_interior_xz(x, y) for x, y in model_ccw]
    assert frames.signed_area_from_above(interior) == pytest.approx(2.0)
    assert frames.ccw_from_above(interior[::-1]) == interior
