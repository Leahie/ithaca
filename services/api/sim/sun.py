"""Sun engine. Pure (C4): a function of (PropertyTwin, inputs), no I/O.

Direct sun reaches a sample point when the ray from it toward the sun leaves the room through one
of the room's exterior windows or sliders. Sample points sit `height_m` above the room's floor (0 =
floor, SEATED_M = a seated person, STANDING_M = a standing person's head). Times are local to the
property (twin.timezone); naive datetimes are read as property-local.

Rays must reach the window without crossing the room's own outline (L-shaped rooms). Not modelled
yet: neighbours and trees (the DSM context), roof overhangs, window reveals, furniture, and light
arriving through a neighbouring room's windows in an open plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pvlib
import shapely
from shapely.geometry import Polygon

from packages.schema.reports import (
    SunDay,
    SunHeatmap,
    SunInterval,
    SunMask,
    SunMoment,
    SunPatch,
    SunReport,
    SunRoomDay,
    SunSpot,
)
from packages.schema.twin import Opening, PropertyTwin, Room, Vec3

STEP_MIN = 10
SPOT_STEP_MIN = 5
CELL_M = 0.25
SEATED_M = 1.1
STANDING_M = 1.6
MIN_LIT_AREA_M2 = 0.5  # a room counts as sunlit once this much of it has direct sun
TRANSPARENT_KINDS = {"window", "slider"}
WINDOW_SAMPLES = 8  # positions along each window's width for the in-room line-of-sight test
WALL_TOLERANCE_M = 0.15  # openings and room outlines don't share an exact wall line


@dataclass(frozen=True)
class _Window:
    opening_id: str
    origin: np.ndarray  # bottom-start corner, interior frame
    along: np.ndarray  # unit vector along the bottom edge
    width: float
    height: float
    outward: np.ndarray  # unit normal pointing out of the room, interior frame


# --- sun position ---------------------------------------------------------------------------


def sun_positions(times: pd.DatetimeIndex, lat: float, lon: float) -> pd.DataFrame:
    """pvlib solar position: azimuth (deg clockwise from north), elevation, apparent_elevation."""
    return pvlib.solarposition.get_solarposition(times, lat, lon)[["azimuth", "elevation", "apparent_elevation"]]


def local_timestamp(when: datetime | str, timezone: str) -> pd.Timestamp:
    ts = pd.Timestamp(when)
    return ts.tz_localize(timezone) if ts.tzinfo is None else ts.tz_convert(timezone)


def day_times(day: date, timezone: str, step_min: int = STEP_MIN) -> pd.DatetimeIndex:
    """Local sample times for one day, at the middle of each step (DST-safe)."""
    start = pd.Timestamp(day).tz_localize(timezone)
    end = pd.Timestamp(day + timedelta(days=1)).tz_localize(timezone)
    return pd.date_range(start, end, freq=f"{step_min}min", inclusive="left") + pd.Timedelta(minutes=step_min / 2)


def sun_directions_interior(twin: PropertyTwin, azimuth_deg: np.ndarray, elevation_deg: np.ndarray) -> np.ndarray:
    """(T, 3) unit vectors toward the sun in the interior frame."""
    az, el = np.radians(azimuth_deg), np.radians(elevation_deg)
    enu = np.stack([np.cos(el) * np.sin(az), np.cos(el) * np.cos(az), np.sin(el)], axis=1)
    return enu @ _interior_to_world_rotation(twin)  # row-vector form of R.T @ v


def _sun_up(twin: PropertyTwin, times: pd.DatetimeIndex) -> tuple[pd.DatetimeIndex, pd.DataFrame, np.ndarray]:
    """Times with the sun above the horizon, their positions, and interior sun directions."""
    pos = sun_positions(times, twin.lat, twin.lon)
    up = pos["apparent_elevation"].to_numpy() > 0
    pos = pos[up]
    dirs = sun_directions_interior(twin, pos["azimuth"].to_numpy(), pos["apparent_elevation"].to_numpy())
    return times[up], pos, dirs


# --- geometry -------------------------------------------------------------------------------


def floor_grid(
    room: Room, cell_m: float = CELL_M, height_m: float = 0.0
) -> tuple[tuple[float, float], np.ndarray, np.ndarray]:
    """(origin_xz, inside mask (rows, cols), (N, 3) cell centres inside the room at height_m)."""
    polygon = Polygon(room.polygon)
    x0, z0, x1, z1 = polygon.bounds
    cols = max(1, int(np.ceil((x1 - x0) / cell_m)))
    rows = max(1, int(np.ceil((z1 - z0) / cell_m)))
    xs = x0 + (np.arange(cols) + 0.5) * cell_m
    zs = z0 + (np.arange(rows) + 0.5) * cell_m
    gx, gz = np.meshgrid(xs, zs)
    inside = shapely.contains_xy(polygon, gx, gz)
    points = np.stack([gx[inside], np.full(inside.sum(), room.floor_y + height_m), gz[inside]], axis=1)
    return (x0, z0), inside, points


def room_windows(twin: PropertyTwin, room_id: str) -> list[_Window]:
    rotation = _interior_to_world_rotation(twin)
    return [
        _window(o, rotation)
        for o in twin.openings
        if o.room_id == room_id and o.kind in TRANSPARENT_KINDS and is_exterior(o)
    ]


def is_exterior(o: Opening) -> bool:
    """Daylight comes through it. Twins without the exterior flag fall back to 'no far room'."""
    return o.exterior if o.exterior is not None else o.other_room_id is None


def window_visibility(room: Room, points: np.ndarray, w: _Window, samples: int = WINDOW_SAMPLES) -> np.ndarray:
    """(N, samples) True where the plan-view line from a point to a spot along the window stays in
    the room, i.e. no wall of an L-shaped room is in the way. Walls are vertical, so 2D suffices."""
    polygon = Polygon(room.polygon).buffer(WALL_TOLERANCE_M)
    spots = w.origin + np.outer((np.arange(samples) + 0.5) / samples * w.width, w.along)
    starts = np.repeat(points[:, [0, 2]], samples, axis=0)
    ends = np.tile(spots[:, [0, 2]], (len(points), 1))
    lines = shapely.linestrings(np.stack([starts, ends], axis=1))
    return shapely.covers(polygon, lines).reshape(len(points), samples)


def through_window(
    points: np.ndarray, directions: np.ndarray, w: _Window, visible: np.ndarray | None = None
) -> np.ndarray:
    """(T, N) True where the ray from point n in direction t leaves through window w.

    `visible` is the window_visibility of the points; without it walls are ignored."""
    out = np.zeros((len(directions), len(points)), dtype=bool)
    facing = directions @ w.outward  # > 0 when the direction is on the window's outer side
    steps = np.flatnonzero(facing > 1e-6)
    if len(steps) == 0 or len(points) == 0:
        return out
    depth = (w.origin - points) @ w.outward  # distance from each point to the window plane
    d = directions[steps]
    t = depth[None, :] / facing[steps, None]
    hit = points[None, :, :] + t[..., None] * d[:, None, :]
    rel = hit - w.origin
    s = rel @ w.along
    h = rel[..., 1]
    ok = (t > 0) & (s >= 0) & (s <= w.width) & (h >= 0) & (h <= w.height)
    if visible is not None:
        k = np.clip((s / w.width * visible.shape[1]).astype(int), 0, visible.shape[1] - 1)
        ok &= visible[np.arange(len(points))[None, :], k]
    out[steps] = ok
    return out


def lit_matrix(
    points: np.ndarray, directions: np.ndarray, windows: list[_Window], visibility: list[np.ndarray] | None = None
) -> np.ndarray:
    lit = np.zeros((len(directions), len(points)), dtype=bool)
    for i, w in enumerate(windows):
        lit |= through_window(points, directions, w, None if visibility is None else visibility[i])
    return lit


def room_rays(
    twin: PropertyTwin, room: Room, cell_m: float = CELL_M, height_m: float = 0.0
) -> tuple[tuple[float, float], np.ndarray, np.ndarray, list[_Window], list[np.ndarray]]:
    """Grid, exterior windows and per-window line of sight for one room at one sample height.
    Memoised on the room's geometry, so repeated questions only trace rays."""
    key = (
        room.model_dump_json(),
        tuple(o.model_dump_json() for o in twin.openings if o.room_id == room.room_id),
        tuple(twin.interior_to_world),
        cell_m,
        height_m,
    )
    if key not in _ROOM_RAYS:
        origin, inside, points = floor_grid(room, cell_m, height_m)
        windows = room_windows(twin, room.room_id)
        _ROOM_RAYS[key] = (origin, inside, points, windows, [window_visibility(room, points, w) for w in windows])
    return _ROOM_RAYS[key]


_ROOM_RAYS: dict[tuple, tuple] = {}


# --- questions ------------------------------------------------------------------------------


def sun_at(
    twin: PropertyTwin,
    when: datetime | str,
    *,
    height_m: float = 0.0,
    room_ids: list[str] | None = None,
    cell_m: float = CELL_M,
    with_masks: bool = True,
) -> SunMoment:
    """Where direct sun is, room by room, at one instant."""
    ts = local_timestamp(when, twin.timezone)
    pos = sun_positions(pd.DatetimeIndex([ts]), twin.lat, twin.lon).iloc[0]
    up = bool(pos["apparent_elevation"] > 0)
    patches: list[SunPatch] = []
    direction = None
    if up:
        d = sun_directions_interior(twin, np.array([pos["azimuth"]]), np.array([pos["apparent_elevation"]]))
        direction = _vec(d[0])
        for room in twin.rooms:
            if room_ids is not None and room.room_id not in room_ids:
                continue
            if not room_windows(twin, room.room_id):
                continue
            origin, inside, points, windows, visibility = room_rays(twin, room, cell_m, height_m)
            lit = np.zeros(len(points), dtype=bool)
            through = []
            for w, vis in zip(windows, visibility):
                hit = through_window(points, d, w, vis)[0]
                if hit.any():
                    through.append(w.opening_id)
                    lit |= hit
            if not lit.any():
                continue
            mask = None
            if with_masks:
                grid = np.zeros(inside.shape, dtype=bool)
                grid[inside] = lit
                mask = SunMask(cell_m=cell_m, origin_xz=(round(origin[0], 3), round(origin[1], 3)), lit=grid.tolist())
            patches.append(SunPatch(
                room_id=room.room_id,
                lit_area_m2=round(float(lit.sum()) * cell_m * cell_m, 2),
                lit_fraction=round(float(lit.mean()), 3),
                through_opening_ids=sorted(through),
                mask=mask,
            ))
    return SunMoment(
        datetime=ts.to_pydatetime(),
        azimuth_deg=round(float(pos["azimuth"]), 2),
        elevation_deg=round(float(pos["apparent_elevation"]), 2),
        sun_up=up,
        direction_interior=direction,
        height_m=height_m,
        rooms=patches,
    )


def sun_spot(
    twin: PropertyTwin,
    room_id: str,
    x: float,
    z: float,
    day: date,
    *,
    height_m: float = SEATED_M,
    step_min: int = SPOT_STEP_MIN,
) -> SunSpot:
    """When direct sun reaches the spot (x, z) of a room, at height_m above its floor."""
    room = _room(twin, room_id)
    point = np.array([[x, room.floor_y + height_m, z]])
    windows = room_windows(twin, room_id)
    visibility = [window_visibility(room, point, w) for w in windows]
    times, _, dirs = _sun_up(twin, day_times(day, twin.timezone, step_min))
    lit = lit_matrix(point, dirs, windows, visibility)[:, 0]
    intervals = _intervals(times, lit, step_min)
    return SunSpot(
        room_id=room_id,
        point=_vec(point[0]),
        height_m=height_m,
        date=day,
        intervals=intervals,
        total_hours=round(float(lit.sum()) * step_min / 60.0, 2),
    )


def sun_room_day(
    twin: PropertyTwin,
    room_id: str,
    day: date,
    *,
    height_m: float = 0.0,
    step_min: int = STEP_MIN,
    cell_m: float = CELL_M,
) -> SunRoomDay:
    """When direct sun reaches any part of a room (at least MIN_LIT_AREA_M2) on one day."""
    _, _, points, windows, visibility = room_rays(twin, _room(twin, room_id), cell_m, height_m)
    times, _, dirs = _sun_up(twin, day_times(day, twin.timezone, step_min))
    min_cells = max(1, int(np.ceil(MIN_LIT_AREA_M2 / (cell_m * cell_m))))
    lit_any = np.zeros(len(times), dtype=bool)
    through = []
    if len(points):
        total = np.zeros((len(times), len(points)), dtype=bool)
        for w, vis in zip(windows, visibility):
            hit = through_window(points, dirs, w, vis)
            if (hit.sum(axis=1) >= min_cells).any():
                through.append(w.opening_id)
            total |= hit
        lit_any = total.sum(axis=1) >= min_cells
    return SunRoomDay(
        room_id=room_id,
        date=day,
        height_m=height_m,
        intervals=_intervals(times, lit_any, step_min),
        total_hours=round(float(lit_any.sum()) * step_min / 60.0, 2),
        through_opening_ids=sorted(through),
    )


def sun_report(
    twin: PropertyTwin,
    room_id: str,
    start: date,
    end: date,
    *,
    height_m: float = 0.0,
    step_min: int = STEP_MIN,
    cell_m: float = CELL_M,
    heatmap: bool = True,
) -> SunReport:
    """Daily direct-sun hours over a date range, plus a mean-hours-per-day heatmap."""
    if end < start:
        raise ValueError("end is before start")
    origin, inside, points, windows, visibility = room_rays(twin, _room(twin, room_id), cell_m, height_m)
    min_cells = max(1, int(np.ceil(MIN_LIT_AREA_M2 / (cell_m * cell_m))))
    step_h = step_min / 60.0
    cell_hours = np.zeros(len(points))
    daily = []
    day = start
    while day <= end:
        hours = 0.0
        if windows and len(points):
            _, _, dirs = _sun_up(twin, day_times(day, twin.timezone, step_min))
            if len(dirs):
                lit = lit_matrix(points, dirs, windows, visibility)
                hours = float((lit.sum(axis=1) >= min_cells).sum() * step_h)
                cell_hours += lit.sum(axis=0) * step_h
        daily.append(SunDay(date=day, direct_sun_hours=round(hours, 2)))
        day += timedelta(days=1)

    grid = None
    if heatmap:
        values = np.zeros(inside.shape)
        values[inside] = cell_hours / len(daily)
        grid = SunHeatmap(
            cell_m=cell_m,
            origin_xz=(round(origin[0], 3), round(origin[1], 3)),
            hours=np.round(values, 2).tolist(),
        )
    return SunReport(room_id=room_id, start=start, end=end, daily=daily, heatmap=grid, height_m=height_m)


# --- helpers --------------------------------------------------------------------------------


def _intervals(times: pd.DatetimeIndex, lit: np.ndarray, step_min: int) -> list[SunInterval]:
    """Merge consecutive lit sample times (each the middle of its step) into intervals."""
    half = pd.Timedelta(minutes=step_min / 2)
    gap = pd.Timedelta(minutes=step_min * 1.5)
    out: list[SunInterval] = []
    start = prev = None
    for t, on in zip(times, lit):
        if on and start is not None and t - prev <= gap:
            prev = t
            continue
        if start is not None:
            out.append(SunInterval(start=(start - half).to_pydatetime(), end=(prev + half).to_pydatetime()))
            start = None
        if on:
            start = prev = t
    if start is not None:
        out.append(SunInterval(start=(start - half).to_pydatetime(), end=(prev + half).to_pydatetime()))
    return out


def _room(twin: PropertyTwin, room_id: str) -> Room:
    room = next((r for r in twin.rooms if r.room_id == room_id), None)
    if room is None:
        raise KeyError(f"no room {room_id} in {twin.property_id}")
    return room


def _interior_to_world_rotation(twin: PropertyTwin) -> np.ndarray:
    return np.asarray(twin.interior_to_world, dtype=float).reshape(4, 4).T[:3, :3]


def _vec(p) -> Vec3:
    return Vec3(x=round(float(p[0]), 4) + 0.0, y=round(float(p[1]), 4) + 0.0, z=round(float(p[2]), 4) + 0.0)


def _window(o: Opening, interior_to_world: np.ndarray) -> _Window:
    corners = np.array([[p.x, p.y, p.z] for p in o.polygon])
    bottom = corners[1] - corners[0]
    width = float(np.linalg.norm(bottom))
    az = np.radians(o.facade_azimuth_deg)
    outward_world = np.array([np.sin(az), np.cos(az), 0.0])
    return _Window(
        opening_id=o.opening_id,
        origin=corners[0],
        along=bottom / width if width > 0 else bottom,
        width=width,
        height=float(corners[3][1] - corners[0][1]),
        outward=interior_to_world.T @ outward_world,
    )
