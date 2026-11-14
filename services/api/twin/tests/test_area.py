import shutil

import pytest

from services.api.integrations.config import fixtures_dir
from services.api.twin import area, service

PID = "onXQLXhfyQv"


@pytest.fixture(autouse=True)
def replay(monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "replay")


def test_profile_from_recorded_sources():
    p = service.area_profile(PID)
    assert p.listing is not None and p.listing.price == 1_995_000 and p.listing.status == "active"
    assert p.listing.sqft == 5800 and p.listing.beds == 6
    assert len(p.comps) == area.COMPS
    assert all(c.listing_id != p.listing.listing_id for c in p.comps)
    assert all(c.distance_m <= area.COMP_RADIUS_M for c in p.comps)
    assert {c.price_kind for c in p.comps if c.status == "sold"} == {"last_asking"}  # Texas
    assert any("Texas" in c for c in p.caveats)
    cats = {a.category for a in p.amenities}
    assert {"grocery", "school", "park"} <= cats
    assert all(sum(1 for a in p.amenities if a.category == c) <= area.PER_CATEGORY for c in cats)
    assert "Listed at $1,995,000" in p.markdown and "### Nearby" in p.markdown
    assert service.get_comps(PID) == p.comps


def test_search_understands_everyday_words():
    coffee = service.area_search(PID, "coffee", 3)
    assert coffee and all(a.category == "cafe" for a in coffee)
    assert [a.distance_m for a in coffee] == sorted(a.distance_m for a in coffee)
    assert service.area_search(PID, "Kroger")[0].name == "Kroger"
    assert service.area_search(PID, "zzz-nothing") == []


def _row(id, **kw):
    base = dict(id=id, property_id=int(id), primary=True, listing_status=4, close_price=None, current_price=None,
                list_price=500_000, address=f"{id} Main St", lat=29.68, lon=-95.46, sqft=2500, beds=4,
                baths=2.5, year_built=1990, close_date="2026-06-01")
    return base | kw


def test_comps_rules():
    twin = service.get_twin(PID)
    subject = _row("1", listing_status=1, current_price=900_000, sqft=3000)
    nearby = [
        subject,
        _row("2"),                                                  # TX sold: last asking
        _row("3", close_price=610_000),                             # disclosed sale
        _row("4", listing_status=1, current_price=1.0),             # placeholder price
        _row("5", primary=False),                                   # not the primary listing
        _row("6", listing_status=1, current_price=700_000, sqft=9000),  # very different size
    ]
    comps = area.comps(twin, subject, nearby)
    ids = [c.listing_id for c in comps]
    assert "1" not in ids and "4" not in ids and "5" not in ids
    assert ids[-1] == "6"
    kinds = {c.listing_id: c.price_kind for c in comps}
    assert kinds["2"] == "last_asking" and kinds["3"] == "sale" and kinds["6"] == "asking"
    assert next(c for c in comps if c.listing_id == "3").price_per_sqft == pytest.approx(244.0)


def test_amenities_dedupe_by_name():
    twin = service.get_twin(PID)
    pois = [
        {"osm_id": "node/1", "key": "highway", "value": "bus_stop", "name": "Main @ 1st", "lat": twin.lat + 0.001, "lon": twin.lon},
        {"osm_id": "node/2", "key": "highway", "value": "bus_stop", "name": "Main @ 1st", "lat": twin.lat + 0.002, "lon": twin.lon},
        {"osm_id": "node/3", "key": "amenity", "value": "bench", "name": "Not a category", "lat": twin.lat, "lon": twin.lon},
    ]
    out = area.amenities(twin, pois)
    assert len(out) == 1 and out[0].category == "transit" and out[0].distance_m == pytest.approx(111, abs=2)


def test_profile_degrades_without_sources(tmp_path, monkeypatch):
    (tmp_path / "twin").mkdir()
    shutil.copy(fixtures_dir() / "twin" / f"{PID}.json", tmp_path / "twin" / f"{PID}.json")
    monkeypatch.setenv("FIXTURES_DIR", str(tmp_path))
    service._area_inputs.cache_clear()
    service._area_profile.cache_clear()
    try:
        p = service.area_profile(PID)
    finally:
        service._area_inputs.cache_clear()
        service._area_profile.cache_clear()
    assert p.comps == [] and p.amenities == [] and p.listing is None
    assert any("CoStar" in c for c in p.caveats) and any("amenity" in c for c in p.caveats)
    assert "No comparable listings" in p.markdown


def test_walk_minutes():
    assert area.walk_min(0) == 1
    assert area.walk_min(800) == 13
