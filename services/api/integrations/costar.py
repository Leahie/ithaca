"""CoStar Data Kit (local files under COSTAR_KIT_DIR).

The kit is not in git, so every lookup is recorded to fixtures/integrations/costar/ (even in live
mode; the kit never changes) and replayed from there. CI and other streams never need the kit.
"""

from __future__ import annotations

import csv
import json
import logging
import math
from functools import lru_cache

from services.api.integrations.cache import FixtureMissing, IntegrationMode, RecordReplayCache
from services.api.integrations.config import IntegrationNotConfigured, costar_kit_dir, env

log = logging.getLogger(__name__)

MODEL_INDEX_FILE = "residential-listings.csv"  # ListingId, ResidentialListingId, MatterportModelId, ...
LISTINGS_FILE = "data/residential-listings.jsonl"
FEATURES_FILE = "data/residential-listing-features.jsonl"
PUBLIC = 10  # permissionLevels.homes: anyone can see it on Homes.com
SUBMARKET, NEIGHBORHOOD = 5, 17  # EnterpriseEntityType of property.address.areas[]


def listing_id_for_model(model_id: str, cache: RecordReplayCache | None = None) -> str | None:
    """The residential ListingId joined to a Matterport model, or None if unknown or unavailable."""
    cache = cache or RecordReplayCache()

    def fetch() -> dict:
        path = costar_kit_dir() / MODEL_INDEX_FILE
        if not path.exists():
            raise IntegrationNotConfigured("CoStar", f"COSTAR_KIT_DIR (missing {path})")
        with path.open(encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("MatterportModelId") == model_id:
                    return {"listing_id": row.get("ListingId") or None}
        return {"listing_id": None}

    request = {"file": MODEL_INDEX_FILE, "MatterportModelId": model_id}
    try:
        return cache.call("costar", f"listing_for_model_{model_id}", request, fetch)["listing_id"]
    except (IntegrationNotConfigured, FixtureMissing) as exc:
        log.warning("CoStar listing lookup skipped: %s", exc)
        return None


def home_listing(listing_id: str) -> dict | None:
    """One residential listing in compact form (see _compact) plus `highlights` and `hoa_monthly`
    from the listing's feature text. None if the listing isn't in the kit."""
    request = {"file": LISTINGS_FILE, "id": str(listing_id), "v": 1}

    def fetch() -> dict:
        row = next((r for r in _listings() if r["id"] == str(listing_id)), None)
        if row is not None:
            row = {**row, **_features(listing_id)}
        return {"listing": row}

    return _cache().call("costar", f"listing_{listing_id}", request, _needs_kit(fetch))["listing"]


def nearby_listings(lat: float, lon: float, radius_m: float = 5000) -> list[dict]:
    """Public for-sale listings within radius_m, nearest first, in compact form with `distance_m`."""
    request = {"file": LISTINGS_FILE, "lat": round(lat, 5), "lon": round(lon, 5), "radius_m": radius_m, "v": 1}

    def fetch() -> dict:
        rows = []
        for r in _listings():
            if r["lat"] is None or not r["public"] or r["transaction_type"] != 1:
                continue
            d = distance_m(lat, lon, r["lat"], r["lon"])
            if d <= radius_m:
                rows.append({**r, "distance_m": round(d, 1)})
        return {"listings": sorted(rows, key=lambda r: r["distance_m"])}

    return _cache().call("costar", "nearby", request, _needs_kit(fetch))["listings"]


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(a))


def _cache() -> RecordReplayCache:
    mode = env("INTEGRATION_MODE", "replay")
    return RecordReplayCache(mode=IntegrationMode.REPLAY if mode == "replay" else IntegrationMode.RECORD)


def _needs_kit(fetch):
    def run():
        if not (costar_kit_dir() / LISTINGS_FILE).exists():
            raise IntegrationNotConfigured("costar", f"COSTAR_KIT_DIR (missing {costar_kit_dir() / LISTINGS_FILE})")
        return fetch()
    return run


@lru_cache(maxsize=1)
def _listings() -> tuple[dict, ...]:
    with (costar_kit_dir() / LISTINGS_FILE).open(encoding="utf-8") as f:
        return tuple(_compact(json.loads(line)) for line in f)


def _compact(r: dict) -> dict:
    p = r.get("property") or {}
    a = p.get("address") or {}
    rv = r.get("resoValues") or {}
    areas = {x.get("enterpriseEntityType"): x.get("name") for x in a.get("areas") or [] if x.get("name")}
    street = a.get("fullStreetAddress")
    if street and a.get("unitNumber"):
        street = f"{street} #{a['unitNumber']}"
    return {
        "id": str(r["id"]),
        "property_id": r.get("propertyId"),
        "public": (r.get("permissionLevels") or {}).get("homes") == PUBLIC,
        "primary": bool(r.get("isPrimaryForThisListingInstance")),
        "transaction_type": r.get("transactionType"),
        "listing_status": r.get("listingStatus"),
        "list_date": (r.get("listDate") or "")[:10] or None,
        "close_date": (r.get("closeDate") or "")[:10] or None,
        "dom": r.get("dom"),
        "close_price": r.get("closePrice"),
        "current_price": r.get("currentPrice"),
        "list_price": rv.get("listPrice") or rv.get("originalListPrice") or r.get("originalPrice"),
        "address": ", ".join(x for x in (street, a.get("city"), a.get("state")) if x) or None,
        "state": a.get("state"),
        "zip": a.get("zip"),
        "lat": a.get("latitude"),
        "lon": a.get("longitude"),
        "sqft": p.get("sqftMin"),
        "beds": p.get("bedsMin"),
        "baths": p.get("bathsMin"),
        "year_built": p.get("yearBuilt"),
        "lot_acres": p.get("lotSizeAcresMin"),
        "subdivision": p.get("subdivision"),
        "neighborhood": areas.get(SUBMARKET) or areas.get(NEIGHBORHOOD),
    }


def _features(listing_id: str) -> dict:
    path = costar_kit_dir() / FEATURES_FILE
    if not path.exists():
        return {"highlights": [], "hoa_monthly": None}
    with path.open(encoding="utf-8") as f:
        for line in f:
            if f'"id": {listing_id},' not in line[:40]:  # skip parsing the other 1,898 records
                continue
            r = json.loads(line)
            if str(r["id"]) != str(listing_id):
                continue
            return {
                "highlights": [h["text"] for h in r.get("highlights") or [] if h.get("text")],
                "hoa_monthly": (r.get("derivedValues") or {}).get("totalMonthlyAssociationFees"),
            }
    return {"highlights": [], "hoa_monthly": None}
