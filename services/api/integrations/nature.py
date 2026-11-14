"""What lives and grows around a property, and its weather: inputs to the soundscape model.

- habitat: water, waterways, woods, trees, grass and parks from OpenStreetMap (Overpass).
- species: birds, frogs and toads, cicadas, crickets and katydids observed nearby (iNaturalist,
  research grade). Which animals are there; their sound levels come from fixtures/tables/soundscape.json.
- weather: a year of hourly temperature and rain (Open-Meteo historical archive, ERA5).

None needs a key. Like osm.py, each is recorded to fixtures/integrations/ on first use (even in
live mode) and replayed after that.
"""

from __future__ import annotations

import requests

from services.api.integrations.cache import FixtureMissing, IntegrationMode, RecordReplayCache
from services.api.integrations.config import IntegrationNotConfigured, env
from services.api.integrations.osm import ENDPOINTS

USER_AGENT = "ithaca-hackprinceton/0.1 (soundscape)"
HABITAT_RADIUS_M = 600
TREE_RADIUS_M = 400
SPECIES_RADIUS_KM = 2
INATURALIST = "https://api.inaturalist.org/v1"
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_YEAR = 2025

# iNaturalist taxon ids. Crickets and katydids are both Orthoptera; the order catches the rest.
TAXA = {"birds": 3, "frogs": 20979, "cicadas": 50186, "orthoptera": 47651}


def habitat(lat: float, lon: float) -> dict:
    """{"areas": [{osm_id, kind, name, geometry}], "lines": [...], "trees": [{osm_id, lat, lon}]}.
    kind is water | wetland | wood | scrub | grass | park | pitch for areas, waterway | tree_row for lines."""
    request = {"lat": round(lat, 5), "lon": round(lon, 5), "radius_m": HABITAT_RADIUS_M, "trees_m": TREE_RADIUS_M}
    return _cached("osm", "habitat", request, lambda: _fetch_habitat(lat, lon), "network access to the Overpass API")


def species(lat: float, lon: float) -> dict:
    """{group: {"species": n, "observations": n, "top": [{name, scientific, count}], "by_month": {1..12: n}}}."""
    request = {"lat": round(lat, 4), "lon": round(lon, 4), "radius_km": SPECIES_RADIUS_KM, "taxa": TAXA,
               "quality_grade": "research"}
    return _cached("inaturalist", "species", request, lambda: _fetch_species(lat, lon), "network access to iNaturalist")


def weather(lat: float, lon: float, timezone: str) -> dict:
    """{"time": [local ISO hour], "temperature_c": [...], "precipitation_mm": [...], "wind_ms": [...]} for WEATHER_YEAR."""
    request = {"lat": round(lat, 3), "lon": round(lon, 3), "year": WEATHER_YEAR, "timezone": timezone,
               "hourly": ["temperature_2m", "precipitation", "wind_speed_10m"]}
    return _cached("open_meteo", "weather", request, lambda: _fetch_weather(lat, lon, timezone), "network access to Open-Meteo")


def _cached(service: str, name: str, request: dict, fetch, needs: str):
    mode = env("INTEGRATION_MODE", "replay")
    cache = RecordReplayCache(mode=IntegrationMode.REPLAY if mode == "replay" else IntegrationMode.RECORD)
    try:
        return cache.call(service, name, request, fetch)
    except FixtureMissing as e:
        raise IntegrationNotConfigured(service, f"{needs}, then run once with INTEGRATION_MODE=record") from e


AREA_KINDS = {
    ("natural", "water"): "water", ("natural", "wetland"): "wetland", ("natural", "wood"): "wood",
    ("landuse", "forest"): "wood", ("natural", "scrub"): "scrub", ("natural", "grassland"): "grass",
    ("landuse", "grass"): "grass", ("landuse", "meadow"): "grass", ("landuse", "village_green"): "grass",
    ("landuse", "recreation_ground"): "park", ("landuse", "cemetery"): "park", ("leisure", "park"): "park",
    ("leisure", "garden"): "park", ("leisure", "nature_reserve"): "park", ("leisure", "golf_course"): "park",
    ("leisure", "pitch"): "pitch",
}


def _fetch_habitat(lat: float, lon: float) -> dict:
    around = f"(around:{HABITAT_RADIUS_M},{lat:.5f},{lon:.5f})"
    query = (
        "[out:json][timeout:90];("
        f'way{around}["waterway"~"^(river|stream|canal|drain|ditch)$"];'
        f'way{around}["natural"~"^(water|wetland|wood|scrub|grassland|tree_row)$"];'
        f'relation{around}["natural"~"^(water|wetland|wood)$"];'
        f'way{around}["landuse"~"^(grass|forest|meadow|recreation_ground|cemetery|village_green)$"];'
        f'way{around}["leisure"~"^(park|garden|nature_reserve|golf_course|pitch)$"];'
        f"node(around:{TREE_RADIUS_M},{lat:.5f},{lon:.5f})[natural=tree];"
        ");out geom tags;"
    )
    errors = []
    for url in ENDPOINTS:
        try:
            r = requests.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=120)
            r.raise_for_status()
            return {**_parse_habitat(r.json()["elements"]), "source": url}
        except (requests.RequestException, ValueError, KeyError) as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError("Overpass unavailable: " + "; ".join(errors))


def _parse_habitat(elements: list[dict]) -> dict:
    out = {"areas": [], "lines": [], "trees": []}
    for e in elements:
        tags = e.get("tags") or {}
        oid = f"{e['type']}/{e['id']}"
        if e["type"] == "node":
            out["trees"].append({"osm_id": oid, "lat": round(e["lat"], 7), "lon": round(e["lon"], 7)})
            continue
        name = tags.get("name")
        if "waterway" in tags or tags.get("natural") == "tree_row":
            kind = "waterway" if "waterway" in tags else "tree_row"
            out["lines"].append({"osm_id": oid, "kind": kind, "name": name, "geometry": _ring(e.get("geometry"))})
            continue
        kind = next((k for (key, value), k in AREA_KINDS.items() if tags.get(key) == value), None)
        if kind is None:
            continue
        if e["type"] == "relation":
            for m in e.get("members") or []:
                if m.get("role") == "outer" and len(m.get("geometry") or []) >= 4:
                    out["areas"].append({"osm_id": oid, "kind": kind, "name": name, "geometry": _ring(m["geometry"])})
        elif len(e.get("geometry") or []) >= 4:
            out["areas"].append({"osm_id": oid, "kind": kind, "name": name, "geometry": _ring(e["geometry"])})
    for key in out:
        out[key].sort(key=lambda i: i["osm_id"])
    return out


def _ring(points) -> list[tuple[float, float]]:
    return [(round(p["lat"], 7), round(p["lon"], 7)) for p in points or []]


def _fetch_species(lat: float, lon: float) -> dict:
    out = {}
    for group, taxon in TAXA.items():
        params = {"lat": lat, "lng": lon, "radius": SPECIES_RADIUS_KM, "taxon_id": taxon, "quality_grade": "research"}
        counts = _get(f"{INATURALIST}/observations/species_counts", {**params, "per_page": 15})
        months = _get(f"{INATURALIST}/observations/histogram", {**params, "date_field": "observed", "interval": "month_of_year"})
        total = _get(f"{INATURALIST}/observations", {**params, "per_page": 0})
        out[group] = {
            "species": counts["total_results"],
            "observations": total["total_results"],
            "top": [{"name": c["taxon"].get("preferred_common_name") or c["taxon"]["name"], "scientific": c["taxon"]["name"],
                     "count": c["count"]} for c in counts["results"]],
            "by_month": {int(k): v for k, v in months["results"]["month_of_year"].items()},
        }
    return out


def _fetch_weather(lat: float, lon: float, timezone: str) -> dict:
    body = _get(OPEN_METEO, {"latitude": lat, "longitude": lon, "start_date": f"{WEATHER_YEAR}-01-01",
                             "end_date": f"{WEATHER_YEAR}-12-31", "hourly": "temperature_2m,precipitation,wind_speed_10m",
                             "wind_speed_unit": "ms", "timezone": timezone})
    h = body["hourly"]
    return {"time": h["time"], "temperature_c": h["temperature_2m"], "precipitation_mm": h["precipitation"],
            "wind_ms": h["wind_speed_10m"], "source": "Open-Meteo historical archive (ERA5)"}


def _get(url: str, params: dict) -> dict:
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
    r.raise_for_status()
    return r.json()
