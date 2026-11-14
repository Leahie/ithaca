"""Points of interest near a property from OpenStreetMap (Overpass API, no key).

Recorded to fixtures/integrations/osm/ on first use (even in live mode) and replayed after
that: the public Overpass servers are slow and rate-limited, and amenities change slowly.
"""

from __future__ import annotations

import requests

from services.api.integrations.cache import FixtureMissing, IntegrationMode, RecordReplayCache
from services.api.integrations.config import IntegrationNotConfigured, env

ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
USER_AGENT = "ithaca-hackprinceton/0.1 (area profile)"

# (OSM key, values). Everything the area profile can categorise.
TAGS = {
    "shop": ["supermarket", "greengrocer", "convenience", "bakery", "mall", "department_store"],
    "amenity": ["school", "kindergarten", "college", "university", "library", "pharmacy", "hospital",
                "clinic", "doctors", "restaurant", "cafe", "fast_food", "place_of_worship"],
    "leisure": ["park", "playground", "fitness_centre", "sports_centre"],
    "public_transport": ["station"],
    "highway": ["bus_stop"],
    "railway": ["station", "tram_stop"],
}


def points_of_interest(lat: float, lon: float, radius_m: int = 2000) -> list[dict]:
    """[{osm_id, key, value, name, lat, lon}] for named places with one of TAGS within radius_m."""
    request = {"lat": round(lat, 5), "lon": round(lon, 5), "radius_m": radius_m, "tags": TAGS}
    mode = env("INTEGRATION_MODE", "replay")
    cache = RecordReplayCache(mode=IntegrationMode.REPLAY if mode == "replay" else IntegrationMode.RECORD)
    try:
        return cache.call("osm", "pois", request, lambda: _fetch(lat, lon, radius_m))["pois"]
    except FixtureMissing as e:
        raise IntegrationNotConfigured("osm", "network access to the Overpass API, then run once with "
                                       "INTEGRATION_MODE=record") from e


def _query(lat: float, lon: float, radius_m: int) -> str:
    around = f"(around:{radius_m},{lat:.5f},{lon:.5f})"
    parts = [f'nwr{around}["{k}"~"^({"|".join(v)})$"]["name"];' for k, v in TAGS.items()]
    return f"[out:json][timeout:60];({''.join(parts)});out center tags;"


def _fetch(lat: float, lon: float, radius_m: int) -> dict:
    query = _query(lat, lon, radius_m)
    errors = []
    for url in ENDPOINTS:
        try:
            r = requests.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=90)
            r.raise_for_status()
            return {"pois": _parse(r.json()["elements"]), "source": url}
        except (requests.RequestException, ValueError, KeyError) as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError("Overpass unavailable: " + "; ".join(errors))


def _parse(elements: list[dict]) -> list[dict]:
    pois = []
    for e in elements:
        tags = e.get("tags") or {}
        lat = e.get("lat", (e.get("center") or {}).get("lat"))
        lon = e.get("lon", (e.get("center") or {}).get("lon"))
        match = next(((k, tags[k]) for k, values in TAGS.items() if tags.get(k) in values), None)
        if lat is None or match is None:
            continue
        pois.append({"osm_id": f"{e['type']}/{e['id']}", "key": match[0], "value": match[1],
                     "name": tags["name"], "lat": lat, "lon": lon})
    return sorted(pois, key=lambda p: p["osm_id"])
