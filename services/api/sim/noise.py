"""Sound in and between rooms (statistical room acoustics). Pure: (PropertyTwin, inputs) -> report (C4).

Each room is a diffuse field with mean-square pressure e_i. Per octave band, power balance:

    4 W_i + sum_j C_ij e_j + C_out_i e_out_i = e_i (A_i + sum_j C_ij + C_out_i)

A_i is the room's Sabine absorption (surfaces, furnishings, air), C_ij = sum S*tau over everything
between rooms i and j (wall, doors, passages, floor/ceiling), C_out_i the same towards outside.
With no coupling this is the classic L_rev = L_w + 10 log(4/A). Every path (through the wall,
round through the hall, down through the floor) is in one linear solve, and the matrix is
symmetric, so the result is reciprocal. Near a source, the direct field Q/(4 pi r^2) adds on top.
Outdoor noise (sim/traffic.py) enters through the envelope: the level outside each outside wall
drives that wall's S*tau, and the same solve spreads it through the house.
Units: e and W in "level" units, 10^(L/10), so L = 10 log10(e).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Polygon

from packages.schema.reports import (
    NoiseHour,
    NoiseHouse,
    NoiseReport,
    NoiseRoomHour,
    NoiseScenario,
    NoiseSource,
    NoiseSources,
    OutdoorSource,
    SoundDoors,
    SoundListener,
    SoundPreset,
    SoundRoom,
    SoundSource,
    TrafficData,
)
from packages.schema.twin import FloorOpening, Opening, PropertyTwin, Room, Vec3, Wall
from services.api.sim import traffic

N_BANDS = 8
SOURCE_HEIGHT_M = 1.0  # a TV, a speaker, a person talking, a crib: about 1 m off the floor
EAR_HEIGHT_M = 1.2
MIN_DISTANCE_M = 0.3
DIRECTIVITY_Q = 1.0
LW_FROM_LP_1M_DB = 11.0  # free field point source: L_p(1 m) = L_w - 11
MIN_FLOOR_OVERLAP_M2 = 0.5
SABINE = 0.161  # s/m
OPEN_WINDOW_FRACTION = 0.5  # a sash or slider opens half its area


@dataclass(frozen=True)
class Acoustics:
    """The shared acoustics table (fixtures/tables/acoustics.json), parsed."""

    a_weighting: np.ndarray
    tl: dict[str, np.ndarray]
    alpha: dict[str, np.ndarray]
    air_4m: np.ndarray
    furnishing: dict[str, np.ndarray]
    presets: dict[str, SoundPreset]
    loudness: dict[str, float] = field(default_factory=dict)

    def tau(self, key: str) -> np.ndarray:
        return 10.0 ** (-self.tl[key] / 10.0)


def load_acoustics(data: dict) -> Acoustics:
    a_weighting = np.array(data["a_weighting_db"], dtype=float)
    presets = {}
    for preset_id, p in data["sound_presets"].items():
        if preset_id == "about":
            continue
        shape = np.array(p["shape_db"], dtype=float)
        bands = shape + (p["level_dba"] - a_sum(shape, a_weighting))
        presets[preset_id] = SoundPreset(preset_id=preset_id, label=p["label"], level_dba=p["level_dba"],
                                         bands_db=_round(bands))
    return Acoustics(
        a_weighting=a_weighting,
        tl={k: np.array(v["tl"], dtype=float) for k, v in data["transmission_loss_db"].items()},
        alpha={k: np.array(v["alpha"], dtype=float) for k, v in data["absorption"].items()},
        air_4m=np.array(data["air_absorption_4m_per_m"], dtype=float),
        furnishing={k: np.array(v, dtype=float) for k, v in data["furnishing_m2_per_floor_m2"].items() if k != "about"},
        presets=presets,
        loudness=dict(data.get("loudness_choices_dba") or {}),
    )


def a_sum(bands_db: np.ndarray, a_weighting: np.ndarray) -> float:
    """A-weighted total of octave-band levels."""
    return float(10.0 * np.log10(np.sum(10.0 ** ((np.asarray(bands_db) + a_weighting) / 10.0))))


def scaled_preset(table: Acoustics, preset_id: str, level_dba: float | None) -> SoundPreset:
    """A preset, optionally re-levelled to level_dba at 1 m (same spectrum shape)."""
    if preset_id not in table.presets:
        raise KeyError(f"no sound preset {preset_id}; choose from {sorted(table.presets)}")
    p = table.presets[preset_id]
    if level_dba is None:
        return p
    shift = level_dba - p.level_dba
    return p.model_copy(update={"level_dba": level_dba, "bands_db": _round(np.array(p.bands_db) + shift)})


# --- the house as an acoustic network -------------------------------------------------------


@dataclass
class Network:
    room_ids: list[str]
    absorption: np.ndarray  # (rooms, bands) Sabine A of each room's own surfaces, m^2
    coupling: np.ndarray  # (rooms, rooms, bands) sum S*tau between rooms, m^2
    outside: np.ndarray  # (rooms, bands) sum S*tau towards outside or unmodelled space, m^2
    volume: np.ndarray  # (rooms,) m^3
    # the part of `outside` that is the building envelope: (wall_id or loose opening_id, room, S*tau)
    envelope: list[tuple[str, int, np.ndarray]] = field(default_factory=list)

    def index(self, room_id: str) -> int:
        try:
            return self.room_ids.index(room_id)
        except ValueError:
            raise KeyError(f"no room {room_id}") from None

    def rt60(self) -> np.ndarray:
        """(rooms, bands) Sabine reverberation time; energy leaving through openings counts as absorbed."""
        total = self.absorption + self.coupling.sum(axis=1) + self.outside
        return SABINE * self.volume[:, None] / np.maximum(total, 1e-6)

    def solve(self, power: np.ndarray) -> np.ndarray:
        """(rooms, bands) diffuse-field e_i for injected power (rooms, bands), both 10^(L/10)."""
        return self._solve(4.0 * power)

    def solve_outside(self, incident: dict[str, np.ndarray]) -> np.ndarray:
        """(rooms, ..., bands) e_i when the outside of each envelope element hears incident[key]
        (..., bands), 10^(L/10). Taking the incident field as diffuse, the power through S is
        S tau e_out / 4, so the source term is S tau e_out: L_in = L_out - R + 10 lg(S/A)."""
        shape = next(iter(incident.values())).shape if incident else (N_BANDS,)
        rhs = np.zeros((len(self.room_ids), *shape))
        for key, i, sti in self.envelope:
            if key in incident:
                rhs[i] += sti * incident[key]
        return self._solve(rhs)

    def _solve(self, rhs: np.ndarray) -> np.ndarray:
        """Solve the power balance per band for rhs (rooms, ..., bands)."""
        n = len(self.room_ids)
        e = np.zeros_like(rhs)
        for b in range(rhs.shape[-1]):
            m = -self.coupling[:, :, b].copy()
            m[np.arange(n), np.arange(n)] = self.absorption[:, b] + self.coupling[:, :, b].sum(axis=1) + self.outside[:, b]
            e[..., b] = np.linalg.solve(m, rhs[..., b].reshape(n, -1)).reshape(rhs.shape[:-1])
        return e


def network(
    twin: PropertyTwin,
    table: Acoustics,
    *,
    doors: SoundDoors = "closed",
    include_furniture: bool = True,
    add_carpet: bool = False,
    glazing: str | None = None,
    windows_open: bool = False,
) -> Network:
    rooms = twin.rooms
    ids = [r.room_id for r in rooms]
    index = {rid: i for i, rid in enumerate(ids)}
    by_id = {r.room_id: r for r in rooms}
    openings = {o.opening_id: o for o in twin.openings}
    n = len(rooms)
    coupling = np.zeros((n, n, N_BANDS))
    outside = np.zeros((n, N_BANDS))
    absorption = np.zeros((n, N_BANDS))
    volume = np.array([max(_area(r), 0.1) * r.height_m for r in rooms])

    wall_area = np.zeros(n)
    glass_area = np.zeros(n)
    door_area = np.zeros(n)

    def element_tau(o: Opening) -> np.ndarray:
        if o.kind in ("window", "slider") and o.glazing != "none":
            closed = table.tau(f"glazing_{glazing or o.glazing}")
            if windows_open and o.exterior is not False:
                return OPEN_WINDOW_FRACTION * table.tau("open") + (1.0 - OPEN_WINDOW_FRACTION) * closed
            return closed
        if o.exterior:
            return table.tau("door_exterior_solid")
        if o.leaf is False or (o.leaf and doors == "open"):
            return table.tau("open")
        return table.tau("door_interior_hollow")

    into_stairs = {o for f in twin.floor_openings for o in f.via_opening_ids}
    on_walls = set()
    envelope = []
    for w in twin.walls:
        sides = [rid for rid in w.room_ids if rid in index]
        if not sides:
            continue
        height = min(by_id[rid].height_m for rid in sides)
        gross = _length(w) * height
        holes = [openings[o] for o in w.opening_ids if o in openings]
        on_walls.update(o.opening_id for o in holes)
        net = max(gross - sum(_opening_area(o) for o in holes), 0.0)
        # passages into a stairwell are handled with the floor openings below, not as losses
        sti = net * table.tau(w.assembly) + sum(
            (_opening_area(o) * element_tau(o) for o in holes if o.opening_id not in into_stairs), np.zeros(N_BANDS))
        if len(sides) == 2:
            i, j = index[sides[0]], index[sides[1]]
            coupling[i, j] += sti
            coupling[j, i] += sti
        else:
            outside[index[sides[0]]] += sti
            if w.exterior:
                envelope.append((w.wall_id, index[sides[0]], sti))
        for rid in sides:
            wall_area[index[rid]] += net
            for o in holes:
                if o.kind in ("window", "slider") and o.glazing != "none":
                    glass_area[index[rid]] += _opening_area(o)
                elif element_tau(o)[0] < 1.0:
                    door_area[index[rid]] += _opening_area(o)

    for o in twin.openings:  # openings that aren't on a known wall (e.g. hand-annotated)
        if o.opening_id in on_walls or o.room_id not in index:
            continue
        sti = _opening_area(o) * element_tau(o)
        if o.other_room_id in index:
            i, j = index[o.room_id], index[o.other_room_id]
            coupling[i, j] += sti
            coupling[j, i] += sti
        else:
            outside[index[o.room_id]] += sti
            if o.exterior:
                envelope.append((o.opening_id, index[o.room_id], sti))

    if not twin.walls:  # no wall data: every room's perimeter is a partition to nowhere
        for r in rooms:
            wall_area[index[r.room_id]] = Polygon(r.polygon).length * r.height_m

    for (i, a), (j, b) in _stacked_pairs(twin):
        overlap = Polygon(a.polygon).intersection(Polygon(b.polygon)).area
        if overlap >= MIN_FLOOR_OVERLAP_M2:
            sti = overlap * table.tau("floor_ceiling_wood_joist")
            coupling[index[a.room_id], index[b.room_id]] += sti
            coupling[index[b.room_id], index[a.room_id]] += sti

    for f in twin.floor_openings:
        for a, b, area in _stair_links(f, openings, by_id):
            if a in index and b in index:
                coupling[index[a], index[b]] += area * table.tau("open")
                coupling[index[b], index[a]] += area * table.tau("open")

    for r in rooms:
        i = index[r.room_id]
        floor = _area(r)
        floor_material = "carpet" if add_carpet else r.floor_material
        absorption[i] = (
            floor * table.alpha.get(floor_material, table.alpha["hardwood"])
            + (floor + wall_area[i]) * table.alpha["gypsum"]  # ceiling and walls
            + glass_area[i] * table.alpha["glass"]
            + door_area[i] * table.alpha["door"]
            + table.air_4m * volume[i]
        )
        if include_furniture:
            absorption[i] += floor * table.furnishing.get(r.label.lower(), table.furnishing["default"])

    return Network(room_ids=ids, absorption=absorption, coupling=coupling, outside=outside, volume=volume,
                   envelope=envelope)


# --- questions ------------------------------------------------------------------------------


def sound_field(
    twin: PropertyTwin,
    net: Network,
    table: Acoustics,
    preset: SoundPreset,
    room_id: str,
    x: float,
    z: float,
    listener: tuple[str, float, float] | None = None,
) -> tuple[SoundSource, list[SoundRoom], SoundListener | None]:
    """Levels in every room for a source at (x, z) in room_id; optionally at one listener spot."""
    src_room = _room(twin, room_id)
    i = net.index(room_id)
    lp1 = np.array(preset.bands_db)
    w = np.zeros((len(net.room_ids), N_BANDS))
    w[i] = 10.0 ** ((lp1 + LW_FROM_LP_1M_DB) / 10.0)
    e = net.solve(w)
    rt60 = net.rt60()

    source = SoundSource(room_id=room_id, position=Vec3(x=x, y=src_room.floor_y + SOURCE_HEIGHT_M, z=z),
                         preset_id=preset.preset_id, level_dba=preset.level_dba, bands_db=preset.bands_db)
    rooms = []
    for k, rid in enumerate(net.room_ids):
        bands = _level(e[k])
        rooms.append(SoundRoom(
            room_id=rid,
            laeq_dba=round(max(a_sum(bands, table.a_weighting), 0.0), 1),
            bands_db=_round(bands),
            gain_db=_round(bands - lp1),
            rt60_s=round(float(rt60[k, 3:5].mean()), 2),
            rt60_bands_s=[round(float(t), 2) for t in rt60[k]],
        ))
    rooms.sort(key=lambda r: -r.laeq_dba)

    heard = None
    if listener is not None:
        l_room_id, lx, lz = listener
        l_room = _room(twin, l_room_id)
        k = net.index(l_room_id)
        position = Vec3(x=lx, y=l_room.floor_y + EAR_HEIGHT_M, z=lz)
        diffuse = e[k]
        direct = np.zeros(N_BANDS)
        distance = None
        if l_room_id == room_id:
            distance = float(np.linalg.norm([lx - x, position.y - source.position.y, lz - z]))
            r = max(distance, MIN_DISTANCE_M)
            direct = w[i] * DIRECTIVITY_Q / (4.0 * np.pi * r * r)
            distance = round(distance, 2)
        total = _level(diffuse + direct)
        weights = 10.0 ** (table.a_weighting / 10.0)
        direct_fraction = float((direct * weights).sum() / max(((diffuse + direct) * weights).sum(), 1e-30))
        heard = SoundListener(
            room_id=l_room_id,
            position=position,
            distance_m=distance,
            laeq_dba=round(max(a_sum(total, table.a_weighting), 0.0), 1),
            bands_db=_round(total),
            gain_db=_round(total - lp1),
            direct_fraction=round(direct_fraction, 3),
            rt60_s=round(float(rt60[k, 3:5].mean()), 2),
        )
    return source, rooms, heard


# --- outdoor noise heard inside ---------------------------------------------------------------

NIGHT_HOURS = [23, 0, 1, 2, 3, 4, 5, 6]
DAY_HOURS = list(range(7, 23))
EVENING_HOURS = [19, 20, 21, 22]
MAX_LISTED_SOURCES = 8


def noise_report(twin: PropertyTwin, net: Network, table: Acoustics, out: traffic.Outdoor, room_id: str,
                 scenario: NoiseScenario, data: TrafficData, caveats: list[str]) -> NoiseReport:
    """A room's level through the day from everything outside, what it's made of, and the level
    just outside its most exposed wall (for a room with no outside wall, the wall its outside
    sound mostly comes in by)."""
    _room(twin, room_id)
    k = net.index(room_id)
    parts = _per_source(out)  # (receivers, parts, 24, bands)
    names = [(s.name, s.kind) for s in out.sources] + [(f"Background ({out.background_label})", "background")]
    inside = net.solve_outside({r.key: parts[i] for i, r in enumerate(out.receivers)})[k]  # (parts, 24, bands)
    total = inside.sum(axis=0)
    hourly_dba = _hourly_dba(total, table)

    facades = [i for i, r in enumerate(out.receivers) if r.room_id == room_id]
    outdoor_hourly, outdoor_lden, outdoor_lnight, facade = [], None, None, None
    if facades:
        facade = max(facades, key=lambda i: _leq(_hourly_dba(parts[i].sum(axis=0), table), range(24)))
    elif out.receivers:
        facade = int(_feeding_facades(net, out, parts.sum(axis=(1, 2)), table)[k])
    if facade is not None:
        outside_total = parts[facade].sum(axis=0)
        outside_dba = _hourly_dba(outside_total, table)
        outdoor_hourly = _hours(outside_total, outside_dba)
        outdoor_lden = round(_lden(outside_dba), 1)
        outdoor_lnight = round(_leq(outside_dba, NIGHT_HOURS), 1)

    sources = []
    for j, (name, kind) in enumerate(names):
        level = _leq(_hourly_dba(inside[j], table), range(24))
        outdoor = None if facade is None else round(_leq(_hourly_dba(parts[facade, j], table), range(24)), 1)
        sources.append((level, NoiseSource(name=name, contribution_dba=round(max(level, 0.0), 1), kind=kind,
                                           outdoor_dba=outdoor)))
    sources.sort(key=lambda s: -s[0])
    listed = [s for _, s in sources[:MAX_LISTED_SOURCES]]
    rest = [lvl for lvl, _ in sources[MAX_LISTED_SOURCES:] if lvl > 0]
    if rest:
        listed.append(NoiseSource(name=f"{len(rest)} other roads and rail", kind="road",
                                  contribution_dba=round(10.0 * np.log10(np.sum(10.0 ** (np.array(rest) / 10.0))), 1)))

    rt60 = net.rt60()
    return NoiseReport(
        room_id=room_id, scenario=scenario, hourly=_hours(total, hourly_dba),
        rt60_s=round(float(rt60[k, 3:5].mean()), 2), sources=listed,
        laeq_24h_dba=round(_leq(hourly_dba, range(24)), 1),
        laeq_day_dba=round(_leq(hourly_dba, DAY_HOURS), 1), laeq_night_dba=round(_leq(hourly_dba, NIGHT_HOURS), 1),
        outdoor_hourly=outdoor_hourly, outdoor_lden_dba=outdoor_lden, outdoor_lnight_dba=outdoor_lnight,
        traffic=data, caveats=caveats)


def noise_house(twin: PropertyTwin, net: Network, table: Acoustics, out: traffic.Outdoor, hour: int,
                scenario: NoiseScenario, data: TrafficData, caveats: list[str]) -> NoiseHouse:
    """Every room's level from outside at one hour."""
    if not 0 <= hour <= 23:
        raise ValueError("hour must be 0..23 (property-local)")
    hourly = out.hourly()[:, hour]  # (receivers, bands)
    inside = net.solve_outside({r.key: hourly[i] for i, r in enumerate(out.receivers)})  # (rooms, bands)
    rt60 = net.rt60()
    loudest: dict[str, int] = {}  # room -> its most exposed receiver at this hour
    for i, r in enumerate(out.receivers):
        if r.room_id not in loudest or _hourly_dba(hourly[i], table) > _hourly_dba(hourly[loudest[r.room_id]], table):
            loudest[r.room_id] = i
    feeding = _feeding_facades(net, out, hourly, table) if out.receivers else None
    rooms = []
    for k, rid in enumerate(net.room_ids):
        facade = loudest.get(rid, None if feeding is None else int(feeding[k]))
        outside = hourly[facade] if facade is not None else np.zeros(N_BANDS)
        rooms.append(NoiseRoomHour(
            room_id=rid, laeq_dba=round(max(float(_hourly_dba(inside[k], table)), 0.0), 1),
            bands_db=_round(_level(inside[k])),
            outdoor_laeq_dba=round(max(float(_hourly_dba(outside, table)), 0.0), 1) if facade is not None else 0.0,
            outdoor_bands_db=_round(_level(outside)), rt60_s=round(float(rt60[k, 3:5].mean()), 2)))
    rooms.sort(key=lambda r: -r.laeq_dba)
    return NoiseHouse(hour=hour, scenario=scenario, rooms=rooms, traffic=data, caveats=caveats)


def noise_sources(table: Acoustics, out: traffic.Outdoor, data: TrafficData, caveats: list[str]) -> NoiseSources:
    """The roads and railways around the house, with how loud each is at the most exposed wall."""
    parts = _per_source(out)
    listed = []
    for j, s in enumerate(out.sources):
        facade = max((_leq(_hourly_dba(parts[i, j], table), range(24)) for i in range(len(out.receivers))), default=0.0)
        listed.append(OutdoorSource(
            source_id=s.source_id, name=s.name, kind=s.kind, data=s.data,
            aadt=None if s.aadt is None else int(s.aadt), heavy_share=s.heavy, speed_kmh=s.speed_kmh,
            distance_m=round(float(np.hypot(*s.points.T).min()), 1) if len(s.points) else None,
            facade_laeq_dba=round(max(facade, 0.0), 1), path=s.path_latlon))
    listed.append(OutdoorSource(source_id="background", name=f"Background ({out.background_label})",
                                kind="background", data="ansi_category",
                                facade_laeq_dba=round(_leq(_hourly_dba(out.background, table), range(24)), 1)))
    listed.sort(key=lambda s: -s.facade_laeq_dba)
    return NoiseSources(sources=listed, traffic=data, caveats=caveats)


def _feeding_facades(net: Network, out: traffic.Outdoor, energy: np.ndarray, table: Acoustics) -> np.ndarray:
    """(rooms,) for each room, the receiver whose outside level brings it the most sound (A-weighted),
    for energy (receivers, bands). A room with no outside wall of its own hears the outside through
    its neighbours; this is the wall that sound mostly comes in by."""
    n = len(out.receivers)
    incident = {}
    for i, r in enumerate(out.receivers):
        one = np.zeros((n, N_BANDS))
        one[i] = energy[i]
        incident[r.key] = one
    return _hourly_dba(net.solve_outside(incident), table).argmax(axis=1)  # (rooms, receivers) -> (rooms,)


def _per_source(out: traffic.Outdoor) -> np.ndarray:
    """(receivers, sources + background, 24, bands), 10^(L/10)."""
    weights = np.array([s.weights for s in out.sources]).reshape(len(out.sources), 24)
    roads = out.energy[:, :, None, :] * weights[None, :, :, None]
    background = np.broadcast_to(out.background, (len(out.receivers), 1, 24, N_BANDS))
    return np.concatenate([roads, background], axis=1)


def _hourly_dba(e: np.ndarray, table: Acoustics) -> np.ndarray:
    """(..., ) A-weighted level of (..., bands) energies, unfloored so that parts add up."""
    weighted = (np.asarray(e) * 10.0 ** (table.a_weighting / 10.0)).sum(axis=-1)
    return 10.0 * np.log10(np.maximum(weighted, 1e-12))


def _hours(e: np.ndarray, dba: np.ndarray) -> list[NoiseHour]:
    return [NoiseHour(hour=h, laeq_dba=round(max(float(dba[h]), 0.0), 1), bands_db=_round(_level(e[h])))
            for h in range(e.shape[0])]


def _leq(dba: np.ndarray, hours) -> float:
    hours = list(hours)
    return float(10.0 * np.log10(np.mean(10.0 ** (np.asarray(dba)[hours] / 10.0))))


def _lden(dba: np.ndarray) -> float:
    penalty = np.array([10.0 if h in NIGHT_HOURS else 5.0 if h in EVENING_HOURS else 0.0 for h in range(24)])
    return float(10.0 * np.log10(np.mean(10.0 ** ((np.asarray(dba) + penalty) / 10.0))))


# --- helpers --------------------------------------------------------------------------------


def _stair_links(f: FloorOpening, openings: dict[str, Opening], rooms: dict[str, Room]):
    """(room, other-floor room, open area m^2): the void's open area, shared out by passage size on
    this floor and by overlap on the other, and never more than a passage lets through."""
    shape = Polygon(f.polygon)
    passage = {}
    for oid in f.via_opening_ids:
        o = openings.get(oid)
        if o is not None:
            passage[o.room_id] = passage.get(o.room_id, 0.0) + _opening_area(o)
    overlap = {rid: Polygon(rooms[rid].polygon).intersection(shape).area for rid in f.other_floor_room_ids if rid in rooms}
    total_passage, total_overlap = sum(passage.values()), sum(overlap.values())
    if total_passage <= 0 or total_overlap <= 0:
        return
    for a, pa in passage.items():
        for b, ob in overlap.items():
            yield a, b, min(total_overlap * pa / total_passage, pa) * ob / total_overlap


def _stacked_pairs(twin: PropertyTwin):
    """(lower, upper) room pairs on consecutive floors."""
    order = {f.floor_id: f.index for f in twin.floors}
    rooms = list(enumerate(twin.rooms))
    for i, a in rooms:
        for j, b in rooms:
            if order.get(b.floor_id, 0) == order.get(a.floor_id, 0) + 1:
                yield (i, a), (j, b)


def _room(twin: PropertyTwin, room_id: str) -> Room:
    room = next((r for r in twin.rooms if r.room_id == room_id), None)
    if room is None:
        raise KeyError(f"no room {room_id} in {twin.property_id}")
    return room


def _area(room: Room) -> float:
    return room.area_m2 if room.area_m2 else Polygon(room.polygon).area


def _length(w: Wall) -> float:
    (x0, z0), (x1, z1) = w.ends
    return float(np.hypot(x1 - x0, z1 - z0))


def _opening_area(o: Opening) -> float:
    p = o.polygon
    width = float(np.hypot(p[1].x - p[0].x, p[1].z - p[0].z))
    height = float(p[3].y - p[0].y)
    return max(width * height, 0.0)


def _level(e: np.ndarray) -> np.ndarray:
    """dB per band, floored at 0 dB: below hearing, and rooms with no path at all (a separate
    building) come out silent rather than at -100 dB."""
    return np.maximum(10.0 * np.log10(np.maximum(e, 1e-12)), 0.0)


def _round(values) -> list[float]:
    return [round(float(v), 1) for v in values]
