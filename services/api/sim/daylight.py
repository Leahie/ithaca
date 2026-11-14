"""Daylight engine: sky + sun + inter-reflected light. Pure (C4): no file or network access.

Horizontal illuminance (lux) at sample points `height_m` above each room's floor is the sum of:

- Sky component. The sky's luminance comes from the Perez all-weather model (Perez, Seals &
  Michalsky 1993) on the 145 Tregenza patches, normalised to the diffuse horizontal illuminance.
  Each point's response to each patch through the room's windows (daylight coefficients, 4x4
  sub-rays per patch, angle-dependent glass transmission) is computed once per room and height,
  so any time and weather is one matrix product: the Radiance two-phase idea, without bounces.
- Sun. Beam illuminance where the direct-sun ray reaches the point (sun.through_window).
- Inter-reflection. BRE split-flux (Hopkinson, Longmore & Petherbridge 1954): light entering
  downward (sky, sun) first meets the floor and lower walls, light entering upward (from the
  ground) the ceiling and upper walls; the rest is the integrating-sphere series. Uniform per room.

Irradiance (W/m²) becomes illuminance through fixed luminous efficacies. Not modelled: exterior
obstructions (neighbours, trees, overhangs), light from neighbouring rooms, furniture.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd
import pvlib
from shapely.geometry import Polygon

from packages.schema.reports import (
    BrightnessLevel,
    DaylightMoment,
    DaylightSpot,
    LuxGrid,
    RoomDaylight,
    SkySource,
)
from packages.schema.twin import PropertyTwin, Room
from services.api.sim import sun

CELL_M = 0.25
EFFICACY_BEAM = 93.0  # lm/W, typical clear-sky beam (Littlefair 1985)
EFFICACY_DIFFUSE = 120.0  # lm/W, typical sky diffuse
GLASS_TRANSMITTANCE = {"none": 1.0, "single": 0.88, "double": 0.78, "triple": 0.69}  # visible, normal incidence
WINDOW_FACTOR = 0.75  # frames and mullions (~0.8) x dirt (~0.95): Matterport outlines the whole opening
FLOOR_REFLECTANCE = {"carpet": 0.15, "hardwood": 0.3, "tile": 0.45, "concrete": 0.3, "stone": 0.35}
DEFAULT_FLOOR_REFLECTANCE = 0.3
WALL_REFLECTANCE = 0.6  # light painted walls
CEILING_REFLECTANCE = 0.8
GROUND_REFLECTANCE = 0.2
SUB = 4  # sub-rays per patch side

# Perez et al. 1993 coefficients, rows = 8 clearness bins (the same table as Radiance's gendaylit).
PEREZ_EPS_EDGES = [1.065, 1.230, 1.500, 1.950, 2.800, 4.500, 6.200]
PEREZ = {
    "a": [(1.3525, -0.2576, -0.2690, -1.4366), (-1.2219, -0.7730, 1.4148, 1.1016),
          (-1.1000, -0.2515, 0.8952, 0.0156), (-0.5484, -0.6654, -0.2672, 0.7117),
          (-0.6000, -0.3566, -2.5000, 2.3250), (-1.0156, -0.3670, 1.0078, 1.4051),
          (-1.0000, 0.0211, 0.5025, -0.5119), (-1.0500, 0.0289, 0.4260, 0.3590)],
    "b": [(-0.7670, 0.0007, 1.2734, -0.1233), (-0.2054, 0.0367, -3.9128, 0.9156),
          (0.2782, -0.1812, -4.5000, 1.1766), (0.7234, -0.6219, -5.6812, 2.6297),
          (0.2937, 0.0496, -5.6812, 1.8415), (0.2875, -0.5328, -3.8500, 3.3750),
          (-0.3000, 0.1922, 0.7023, -1.6317), (-0.3250, 0.1156, 0.7781, 0.0025)],
    "c": [(2.8000, 0.6004, 1.2375, 1.0000), (6.9750, 0.1774, 6.4477, -0.1239),
          (24.7219, -13.0812, -37.7000, 34.8438), (33.3389, -18.3000, -62.2500, 52.0781),
          (21.0000, -4.7656, -21.5906, 7.2492), (14.0000, -0.9999, -7.1406, 7.5469),
          (19.0000, -5.0000, 1.2438, -1.9094), (31.0625, -14.5000, -46.1148, 55.3750)],
    "d": [(1.8734, 0.6297, 0.9738, 0.2809), (-1.5798, -0.5081, -1.7812, 0.1080),
          (-5.0000, 1.5218, 3.9229, -2.6204), (-3.5000, 0.0016, 1.1477, 0.1062),
          (-3.5000, -0.1554, 1.4062, 0.3988), (-3.4000, -0.1078, -1.0750, 1.5702),
          (-4.0000, 0.0250, 0.3844, 0.2656), (-7.2312, 0.4050, 13.3500, 0.6234)],
    "e": [(0.0356, -0.1246, -0.5718, 0.9938), (0.2624, 0.0672, -0.2190, -0.4285),
          (-0.0156, 0.1597, 0.4199, -0.5562), (0.4659, -0.3296, -0.0876, -0.0329),
          (0.0032, 0.0766, -0.0656, -0.1294), (-0.0672, 0.4016, 0.3017, -0.4844),
          (1.0468, -0.3788, -2.4517, 1.4656), (1.5000, -0.6426, 1.8564, 0.5636)],
}
PEREZ = {k: np.array(v) for k, v in PEREZ.items()}

# Tregenza sky: (lowest altitude, highest altitude, patches) per band, then the zenith cap.
TREGENZA_BANDS = [(0, 12, 30), (12, 24, 30), (24, 36, 24), (36, 48, 24), (48, 60, 18), (60, 72, 12), (72, 84, 6),
                  (84, 90, 1)]


@dataclass(frozen=True)
class Weather:
    """Irradiance at given local times (W/m²)."""

    times: pd.DatetimeIndex
    dni: np.ndarray
    dhi: np.ndarray
    ghi: np.ndarray
    albedo: np.ndarray
    source: SkySource


@dataclass(frozen=True)
class SkyPatches:
    altitude_deg: np.ndarray  # (P,) patch centres
    azimuth_deg: np.ndarray
    solid_angle: np.ndarray
    sub_altitude_deg: np.ndarray  # (P * SUB², ) equal-solid-angle sub-rays, patch-major
    sub_azimuth_deg: np.ndarray
    sub_solid_angle: np.ndarray


@dataclass(frozen=True)
class RoomCoefficients:
    origin: tuple[float, float]
    inside: np.ndarray
    points: np.ndarray
    sky: np.ndarray  # (N, P) lux at each point per cd/m² of each patch
    windows: list
    visibility: list
    window_sky: np.ndarray  # (W, P) lumens through each window per cd/m² of each patch
    window_area: np.ndarray  # (W,) glazed area x window factor
    window_tau0: np.ndarray  # (W,)
    floor_points: np.ndarray  # (F, 3) room grid on the floor, to find where the sun beam lands
    floor_visibility: list
    floor_cell_m2: float
    rho_floor: float
    rho_down: float  # floor + walls below window mid-height
    rho_up: float  # ceiling + walls above
    rho_mean: float
    area_total: float


def tregenza(sub: int = SUB) -> SkyPatches:
    alt, az, omega, s_alt, s_az, s_omega = [], [], [], [], [], []
    for lo, hi, n in TREGENZA_BANDS:
        width = 360.0 / n
        band = np.radians(width) * (np.sin(np.radians(hi)) - np.sin(np.radians(lo)))
        u = np.sin(np.radians(lo)) + (np.arange(sub) + 0.5) / sub * (np.sin(np.radians(hi)) - np.sin(np.radians(lo)))
        sub_alts = np.degrees(np.arcsin(u))
        for k in range(n):
            centre = k * width
            alt.append(90.0 if n == 1 else (lo + hi) / 2)
            az.append(centre)
            omega.append(band)
            sub_az = centre - width / 2 + (np.arange(sub) + 0.5) * width / sub
            ga, gz = np.meshgrid(sub_alts, sub_az, indexing="ij")
            s_alt.append(ga.ravel())
            s_az.append(gz.ravel() % 360.0)
            s_omega.append(np.full(sub * sub, band / (sub * sub)))
    return SkyPatches(np.array(alt), np.array(az), np.array(omega),
                      np.concatenate(s_alt), np.concatenate(s_az), np.concatenate(s_omega))


SKY = tregenza()


# --- weather --------------------------------------------------------------------------------


def clear_sky(twin: PropertyTwin, times: pd.DatetimeIndex, linke_turbidity) -> Weather:
    """Ineichen-Perez clear sky with the given Linke turbidity (scalar or per time)."""
    location = pvlib.location.Location(twin.lat, twin.lon, tz=twin.timezone)
    cs = location.get_clearsky(times, model="ineichen", linke_turbidity=linke_turbidity)
    n = len(times)
    return Weather(times, cs["dni"].to_numpy(), cs["dhi"].to_numpy(), cs["ghi"].to_numpy(),
                   np.full(n, GROUND_REFLECTANCE), "clear")


def typical_weather(tmy: pd.DataFrame, times: pd.DatetimeIndex, utc_offset_h: float, shift_h: float = 0.0) -> Weather:
    """Typical-year weather at `times` (any year), interpolated from hourly TMY rows.

    `tmy` has month, day, hour, minute, dni, dhi, ghi and optionally albedo, in local standard
    time (`utc_offset_h`); `shift_h` moves the TMY stamps to the instant they represent."""
    def hour_of_year(month, day, hour):
        doy = pd.to_datetime({"year": 2001, "month": month, "day": day}).dt.dayofyear.to_numpy()
        return (doy - 1) * 24.0 + hour

    src = hour_of_year(tmy["month"], tmy["day"], tmy["hour"] + tmy["minute"] / 60.0 + shift_h)
    std = times.tz_convert("UTC").tz_localize(None) + pd.Timedelta(hours=utc_offset_h)
    month = np.where((std.month == 2) & (std.day == 29), 2, std.month)
    day = np.where((std.month == 2) & (std.day == 29), 28, std.day)
    dst = hour_of_year(pd.Series(month), pd.Series(day), pd.Series(std.hour + std.minute / 60.0 + std.second / 3600.0))
    order = np.argsort(src)

    def at(column, default=0.0):
        if column not in tmy:
            return np.full(len(times), default)
        return np.interp(dst, src[order], tmy[column].to_numpy()[order], period=8760.0)

    return Weather(times, at("dni"), at("dhi"), at("ghi"), at("albedo", GROUND_REFLECTANCE), "typical")


# --- sky luminance --------------------------------------------------------------------------


def perez_luminance(sun_zenith_deg: float, sun_azimuth_deg: float, dni: float, dhi: float,
                    airmass: float, dni_extra: float) -> np.ndarray:
    """(P,) absolute luminance (cd/m²) of each Tregenza patch; integrates to the diffuse
    horizontal illuminance."""
    if dhi <= 0 or sun_zenith_deg >= 90:
        return np.zeros(len(SKY.altitude_deg))
    z = np.radians(sun_zenith_deg)
    eps = ((dhi + dni) / dhi + 1.041 * z**3) / (1 + 1.041 * z**3)
    delta = dhi * airmass / dni_extra
    i = int(np.searchsorted(PEREZ_EPS_EDGES, eps))

    def coef(name):
        p = PEREZ[name][i]
        return p[0] + p[1] * z + delta * (p[2] + p[3] * z)

    a, b, e = coef("a"), coef("b"), coef("e")
    if i == 0:
        c1, c2, c3, _ = PEREZ["c"][0]
        d1, d2, d3, d4 = PEREZ["d"][0]
        c = np.exp((delta * (c1 + c2 * z)) ** c3) - 1
        d = -np.exp(delta * (d1 + d2 * z)) + d3 + delta * d4
    else:
        c, d = coef("c"), coef("d")

    alt = np.radians(SKY.altitude_deg)
    zeta = np.pi / 2 - alt
    sun_alt = np.pi / 2 - z
    cos_gamma = np.clip(np.sin(alt) * np.sin(sun_alt)
                        + np.cos(alt) * np.cos(sun_alt) * np.cos(np.radians(SKY.azimuth_deg - sun_azimuth_deg)), -1, 1)
    gamma = np.arccos(cos_gamma)
    relative = (1 + a * np.exp(b / np.maximum(np.cos(zeta), 0.01))) * (1 + c * np.exp(d * gamma) + e * cos_gamma**2)
    relative = np.maximum(relative, 0.0)
    norm = np.sum(relative * SKY.solid_angle * np.sin(alt))
    return dhi * EFFICACY_DIFFUSE * relative / norm if norm > 0 else relative


# --- per-room daylight coefficients ---------------------------------------------------------


def glass_transmittance(cos_theta: np.ndarray, tau0: float) -> np.ndarray:
    """Angle-dependent visible transmittance (Rivero's approximation), capped at tau0."""
    c = np.clip(cos_theta, 0.0, 1.0)
    s = np.sqrt(1.0 - c * c)
    return np.minimum(1.018 * tau0 * c * (1.0 + s**3), tau0)


_COEFFICIENTS: dict[tuple, RoomCoefficients] = {}


def room_coefficients(twin: PropertyTwin, room: Room, height_m: float = 0.0, cell_m: float = CELL_M,
                      points: np.ndarray | None = None) -> RoomCoefficients:
    """Daylight coefficients for a room's grid (or for given points). Memoised per twin version."""
    key = (twin.property_id, twin.version, room.room_id, height_m, cell_m, None if points is None else points.tobytes())
    if key in _COEFFICIENTS:
        return _COEFFICIENTS[key]
    if points is None:
        origin, inside, points = sun.floor_grid(room, cell_m, height_m)
    else:
        origin, inside = (0.0, 0.0), np.ones((1, len(points)), dtype=bool)
    windows = sun.room_windows(twin, room.room_id)
    visibility = [sun.window_visibility(room, points, w) for w in windows]
    taus = {o.opening_id: GLASS_TRANSMITTANCE[o.glazing] for o in twin.openings}

    dirs = sun.sun_directions_interior(twin, SKY.sub_azimuth_deg, SKY.sub_altitude_deg)
    horizontal = np.sin(np.radians(SKY.sub_altitude_deg)) * SKY.sub_solid_angle
    n_patch, n_sub = len(SKY.altitude_deg), SUB * SUB
    sky_sub = np.zeros((len(dirs), len(points)))
    window_sky = np.zeros((len(windows), n_patch))
    for wi, (w, vis) in enumerate(zip(windows, visibility)):
        cos_t = dirs @ w.outward
        tau = glass_transmittance(cos_t, taus[w.opening_id]) * WINDOW_FACTOR
        window_sky[wi] = (w.width * w.height * tau * np.maximum(cos_t, 0) * SKY.sub_solid_angle).reshape(
            n_patch, n_sub).sum(axis=1)
        for start in range(0, len(dirs), 512):
            chunk = slice(start, start + 512)
            hit = sun.through_window(points, dirs[chunk], w, vis)
            sky_sub[chunk] += np.where(hit & (sky_sub[chunk] == 0), (tau * horizontal)[chunk, None], 0.0)
    sky = sky_sub.reshape(n_patch, n_sub, len(points)).sum(axis=1).T

    rho_f = FLOOR_REFLECTANCE.get(room.floor_material, DEFAULT_FLOOR_REFLECTANCE)
    outline = Polygon(room.polygon)
    a_floor, perimeter, height = outline.area, outline.length, room.height_m
    mid = np.mean([w.origin[1] - room.floor_y + w.height / 2 for w in windows]) if windows else height / 2
    mid = float(np.clip(mid, 0.0, height))
    a_below, a_above = perimeter * mid, perimeter * (height - mid)
    a_total = 2 * a_floor + perimeter * height
    _, _, floor_points = sun.floor_grid(room, CELL_M, 0.0)
    coefficients = RoomCoefficients(
        origin=origin, inside=inside, points=points, sky=sky, windows=windows, visibility=visibility,
        window_sky=window_sky,
        window_area=np.array([w.width * w.height * WINDOW_FACTOR for w in windows]),
        window_tau0=np.array([taus[w.opening_id] for w in windows]),
        floor_points=floor_points,
        floor_visibility=[sun.window_visibility(room, floor_points, w) for w in windows],
        floor_cell_m2=CELL_M * CELL_M,
        rho_floor=rho_f,
        rho_down=(a_floor * rho_f + a_below * WALL_REFLECTANCE) / (a_floor + a_below),
        rho_up=(a_floor * CEILING_REFLECTANCE + a_above * WALL_REFLECTANCE) / (a_floor + a_above),
        rho_mean=(a_floor * (rho_f + CEILING_REFLECTANCE) + perimeter * height * WALL_REFLECTANCE) / a_total,
        area_total=a_total,
    )
    _COEFFICIENTS[key] = coefficients
    return coefficients


# --- one instant ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Instant:
    ts: pd.Timestamp
    azimuth: float
    elevation: float
    up: bool
    sun_dir: np.ndarray | None  # (1, 3) interior
    beam_lux: float  # normal-incidence beam illuminance
    luminance: np.ndarray  # (P,) cd/m²
    ground_lux: float  # global horizontal illuminance outside
    dni: float
    dhi: float
    ghi: float
    albedo: float


def _instants(twin: PropertyTwin, weather: Weather) -> list[_Instant]:
    pos = sun.sun_positions(weather.times, twin.lat, twin.lon)
    airmass = np.nan_to_num(pvlib.atmosphere.get_relative_airmass(90 - pos["apparent_elevation"]).to_numpy())
    extra = pvlib.irradiance.get_extra_radiation(weather.times).to_numpy()
    out = []
    for i, ts in enumerate(weather.times):
        el, az = float(pos["apparent_elevation"].iloc[i]), float(pos["azimuth"].iloc[i])
        up = el > 0
        dni, dhi, ghi = (max(float(v[i]), 0.0) for v in (weather.dni, weather.dhi, weather.ghi))
        if not up:
            dni = dhi = ghi = 0.0
        luminance = perez_luminance(90 - el, az, dni, dhi, float(airmass[i]) if up else 0.0, float(extra[i]))
        sun_dir = sun.sun_directions_interior(twin, np.array([az]), np.array([el])) if up else None
        out.append(_Instant(ts, az, el, up, sun_dir, dni * EFFICACY_BEAM, luminance,
                            dhi * EFFICACY_DIFFUSE + dni * EFFICACY_BEAM * max(np.sin(np.radians(el)), 0.0),
                            dni, dhi, ghi, float(weather.albedo[i])))
    return out


def _room_lux(c: RoomCoefficients, s: _Instant) -> tuple[np.ndarray, np.ndarray, float]:
    """(lux per point, direct-sun mask, uniform reflected lux) for one room at one instant."""
    n = len(c.points)
    if not s.up:
        return np.zeros(n), np.zeros(n, dtype=bool), 0.0
    lux = c.sky @ s.luminance
    lit = np.zeros(n, dtype=bool)
    flux_down = float(c.window_sky @ s.luminance @ np.ones(len(c.windows))) if c.windows else 0.0
    flux_sun = flux_sun_floor = 0.0
    sin_el = np.sin(np.radians(s.elevation))
    floor_lit = np.zeros(len(c.floor_points), dtype=bool)
    for w, vis, floor_vis, tau0, area in zip(c.windows, c.visibility, c.floor_visibility, c.window_tau0,
                                             c.window_area):
        cos_s = float(s.sun_dir[0] @ w.outward)
        if cos_s <= 0 or s.beam_lux <= 0:
            continue
        tau = float(glass_transmittance(np.array(cos_s), tau0)) * WINDOW_FACTOR
        flux_sun += area / WINDOW_FACTOR * tau * cos_s * s.beam_lux
        hit = sun.through_window(c.points, s.sun_dir, w, vis)[0] & ~lit
        lux[hit] += s.beam_lux * sin_el * tau
        lit |= hit
        on_floor = sun.through_window(c.floor_points, s.sun_dir, w, floor_vis)[0] & ~floor_lit
        flux_sun_floor += float(on_floor.sum()) * c.floor_cell_m2 * s.beam_lux * sin_el * tau
        floor_lit |= on_floor
    flux_sun_floor = min(flux_sun_floor, flux_sun)  # the rest of the beam lands on the walls
    flux_up = float(np.sum(c.window_area * 0.85 * c.window_tau0)) * s.albedo * s.ground_lux / 2
    first = (flux_down * c.rho_down + flux_sun_floor * c.rho_floor + (flux_sun - flux_sun_floor) * WALL_REFLECTANCE
             + flux_up * c.rho_up)
    reflected = first / (c.area_total * (1 - c.rho_mean))
    return lux + reflected, lit, reflected


def brightness_level(lux: float) -> BrightnessLevel:
    """Bands after Useful Daylight Illuminance (Nabil & Mardaljevic 2005)."""
    if lux < 50:
        return "dark"
    if lux < 300:
        return "dim"
    if lux < 1000:
        return "moderate"
    if lux < 3000:
        return "bright"
    return "very_bright"


def daylight_at(
    twin: PropertyTwin,
    weather: Weather,
    *,
    height_m: float = 0.0,
    room_ids: list[str] | None = None,
    cell_m: float = CELL_M,
    with_grids: bool = True,
) -> DaylightMoment:
    """Daylight in every room (with exterior windows) at weather.times[0]."""
    s = _instants(twin, weather.__class__(weather.times[:1], weather.dni[:1], weather.dhi[:1], weather.ghi[:1],
                                          weather.albedo[:1], weather.source))[0]
    rooms = []
    for room in twin.rooms:
        if room_ids is not None and room.room_id not in room_ids:
            continue
        if not sun.room_windows(twin, room.room_id):
            continue
        c = room_coefficients(twin, room, height_m, cell_m)
        if len(c.points) == 0:
            continue
        lux, lit, reflected = _room_lux(c, s)
        grid = None
        if with_grids:
            values = np.full(c.inside.shape, np.nan)
            values[c.inside] = lux
            grid = LuxGrid(cell_m=cell_m, origin_xz=(round(c.origin[0], 3), round(c.origin[1], 3)),
                           lux=[[None if np.isnan(v) else round(float(v), 1) for v in row] for row in values])
        median = float(np.median(lux))
        rooms.append(RoomDaylight(
            room_id=room.room_id,
            mean_lux=round(float(lux.mean()), 1),
            median_lux=round(median, 1),
            max_lux=round(float(lux.max()), 1),
            reflected_lux=round(reflected, 1),
            sun_area_m2=round(float(lit.sum()) * cell_m * cell_m, 2),
            level=brightness_level(median),
            grid=grid,
        ))
    return DaylightMoment(
        datetime=s.ts.to_pydatetime(), sky=weather.source, azimuth_deg=round(s.azimuth, 2),
        elevation_deg=round(s.elevation, 2), sun_up=s.up, dni_w_m2=round(s.dni, 1), dhi_w_m2=round(s.dhi, 1),
        ghi_w_m2=round(s.ghi, 1), outdoor_lux=round(s.ground_lux, 0), height_m=height_m, rooms=rooms,
    )


def daylight_spot(
    twin: PropertyTwin, room_id: str, x: float, z: float, day: date, weather: Weather, *, height_m: float = sun.SEATED_M,
) -> DaylightSpot:
    """Daylight at one spot at each of weather.times (one day)."""
    room = next(r for r in twin.rooms if r.room_id == room_id)
    point = np.array([[x, room.floor_y + height_m, z]])
    c = room_coefficients(twin, room, height_m, points=point)
    lux, sunny = [], []
    for s in _instants(twin, weather):
        value, lit, _ = _room_lux(c, s)
        lux.append(round(float(value[0]), 1))
        sunny.append(bool(lit[0]))
    return DaylightSpot(room_id=room_id, point=sun._vec(point[0]), height_m=height_m, date=day, sky=weather.source,
                        times=[t.to_pydatetime() for t in weather.times], lux=lux, direct_sun=sunny)


def single_time(when: datetime | str, timezone: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([sun.local_timestamp(when, timezone)])
