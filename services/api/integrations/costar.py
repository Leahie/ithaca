"""CoStar Data Kit (local files under COSTAR_KIT_DIR)."""

from __future__ import annotations

import csv
import logging

from services.api.integrations.cache import FixtureMissing, RecordReplayCache
from services.api.integrations.config import IntegrationNotConfigured, costar_kit_dir

log = logging.getLogger(__name__)

MODEL_INDEX_FILE = "residential-listings.csv"  # ListingId, ResidentialListingId, MatterportModelId, ...


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
