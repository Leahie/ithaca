"""Roads, buildings and rail around a property (OpenStreetMap / Overpass) and traffic counts
(state DOT AADT: Texas via TxDOT's public ArcGIS layer). Inputs to the traffic noise model.

Recorded to fixtures/integrations/{osm,txdot}/ on first use (even in live mode) and replayed
after that, like osm.py. Outside Texas there are no counts: the noise model falls back to
default volumes by road class (C3).
"""

from __future__ import annotations

import re

import requests

from services.api.integrations.cache import FixtureMissing, IntegrationMode, RecordReplayCache
from services.api.integrations.config import IntegrationNotConfigured, env
from services.api.integrations.osm import ENDPOINTS

USER_AGENT = "ithaca-hackprinceton/0.1 (traffic noise)"
TXDOT_AADT = ("https://services.arcgis.com/KTcxiTD9dsQw4r7Z/arcgis/rest/services/"
              "TxDOT_AADT_Annuals_(Public_View)/FeatureServer/0/query")

# Search radius per road class: a freeway is audible from far away, a cul-de-sac only next door.
ROAD_RADIUS_M = {
    "motorway|trunk|motorway_link|trunk_link": 2500,
    "primary|primary_link|secondary|secondary_link": 1200,
    "tertiary|tertiary_link|unclassified": 600,
    "residential|living_street": 300,
}
BUILDING_RADIUS_M = 350
RAIL_RADIUS_M = 2000
AADT_RADIUS_M = 2500


def surroundings(lat: float, lon: float) -> dict:
    """{"roads": [...], "buildings": [...], "rail": [...]} with lat/lon geometry and OSM tags."""
    request = {"lat": round(lat, 5), "lon": round(lon, 5), "roads": ROAD_RADIUS_M,
               "buildings": BUILDING_RADIUS_M, "rail": RAIL_RADIUS_M}
    return _cached("osm", "surroundings", request, lambda: _fetch_osm(lat, lon),
                   "network access to the Overpass API")


def aadt_counts(lat: float, lon: float, address: str) -> list[dict]:
    """[{station_id, aadt, year, lat, lon}] count stations within AADT_RADIUS_M; [] where unsupported."""
    if not re.search(r"(,\s*|\b)(TX|Texas)\b", address or ""):
        return []
    request = {"lat": round(lat, 5), "lon": round(lon, 5), "radius_m": AADT_RADIUS_M}
    return _cached("txdot", "aadt", request, lambda: _fetch_txdot(lat, lon), "network access to TxDOT's ArcGIS service")


def _cached(service: str, name: str, request: dict, fetch, needs: str):
    mode = env("INTEGRATION_MODE", "replay")
    cache = RecordReplayCache(mode=IntegrationMode.REPLAY if mode == "replay" else IntegrationMode.RECORD)
    try:
        return cache.call(service, name, request, fetch)
    except FixtureMissing as e:
        raise IntegrationNotConfigured(service, f"{needs}, then run once with INTEGRATION_MODE=record") from e


def _fetch_osm(lat: float, lon: float) -> dict:
    at = f"{lat:.5f},{lon:.5f}"
    parts = [f'way(around:{r},{at})["highway"~"^({classes})$"];' for classes, r in ROAD_RADIUS_M.items()]
    parts.append(f'way(around:{BUILDING_RADIUS_M},{at})["building"];')
    parts.append(f'way(around:{RAIL_RADIUS_M},{at})["railway"~"^(rail|light_rail)$"];')
    query = f"[out:json][timeout:90];({''.join(parts)});out geom tags;"
    errors = []
    for url in ENDPOINTS:
        try:
            r = requests.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=120)
            r.raise_for_status()
            return {**_parse_osm(r.json()["elements"]), "source": url}
        except (requests.RequestException, ValueError, KeyError) as e:
            errors.append(f"{url}: {e}")
    raise RuntimeError("Overpass unavailable: " + "; ".join(errors))


def _parse_osm(elements: list[dict]) -> dict:
    out = {"roads": [], "buildings": [], "rail": []}
    for e in elements:
        tags = e.get("tags") or {}
        geometry = [(round(p["lat"], 7), round(p["lon"], 7)) for p in e.get("geometry") or []]
        if len(geometry) < 2:
            continue
        item = {"osm_id": f"way/{e['id']}", "geometry": geometry}
        if "highway" in tags:
            out["roads"].append({**item, "highway": tags["highway"], "name": tags.get("name") or tags.get("ref"),
                                 "lanes": tags.get("lanes"), "maxspeed": tags.get("maxspeed"),
                                 "oneway": tags.get("oneway"), "surface": tags.get("surface")})
        elif "building" in tags:
            out["buildings"].append({**item, "levels": tags.get("building:levels"), "height": tags.get("height")})
        elif "railway" in tags:
            out["rail"].append({**item, "railway": tags["railway"], "name": tags.get("name")})
    for key in out:
        out[key].sort(key=lambda i: i["osm_id"])
    return out


def _fetch_txdot(lat: float, lon: float) -> list[dict]:
    r = requests.get(TXDOT_AADT, timeout=60, params={
        "geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint", "inSR": "4326",
        "distance": str(AADT_RADIUS_M), "units": "esriSRUnit_Meter", "spatialRel": "esriSpatialRelIntersects",
        "outFields": "TRFC_STATN_ID,AADT_RPT_QTY,AADT_RPT_YEAR,ON_ROAD", "returnGeometry": "true",
        "outSR": "4326", "f": "json",
    })
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        raise RuntimeError(f"TxDOT: {body['error']}")
    stations = []
    for f in body.get("features") or []:
        a, g = f["attributes"], f.get("geometry") or {}
        if a.get("AADT_RPT_QTY") and "y" in g:
            stations.append({"station_id": a["TRFC_STATN_ID"], "aadt": int(a["AADT_RPT_QTY"]),
                             "year": a.get("AADT_RPT_YEAR"), "lat": round(g["y"], 7), "lon": round(g["x"], 7)})
    return sorted(stations, key=lambda s: s["station_id"])
