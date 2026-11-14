"""Area profile v1: the home's own listing, comparable homes and nearby amenities, plus a markdown
summary for the agent. Pure: build_profile() works on data already fetched by the integrations.

Sources: CoStar residential listings (listing facts, comps) and OpenStreetMap (amenities).
"""

from __future__ import annotations

import math
import re
from datetime import date
from statistics import median

from packages.schema.reports import Amenity, AreaProfile, Comp, ListingFacts
from packages.schema.twin import PropertyTwin
from services.api.integrations.costar import distance_m

COMP_RADIUS_M = 5000
AMENITY_RADIUS_M = 2000
COMPS = 8
PER_CATEGORY = 5
WALK_M_PER_MIN = 80.0  # about 4.8 km/h
DETOUR = 1.3  # street distance over straight-line distance, a typical urban grid value

CATEGORIES = {
    ("shop", "supermarket"): "grocery", ("shop", "greengrocer"): "grocery",
    ("shop", "convenience"): "shopping", ("shop", "mall"): "shopping", ("shop", "department_store"): "shopping",
    ("shop", "bakery"): "cafe", ("amenity", "cafe"): "cafe",
    ("amenity", "school"): "school", ("amenity", "kindergarten"): "school",
    ("amenity", "college"): "school", ("amenity", "university"): "school",
    ("amenity", "library"): "library", ("amenity", "pharmacy"): "pharmacy",
    ("amenity", "hospital"): "health", ("amenity", "clinic"): "health", ("amenity", "doctors"): "health",
    ("amenity", "restaurant"): "restaurant", ("amenity", "fast_food"): "restaurant",
    ("amenity", "place_of_worship"): "worship",
    ("leisure", "park"): "park", ("leisure", "playground"): "playground",
    ("leisure", "fitness_centre"): "fitness", ("leisure", "sports_centre"): "fitness",
    ("public_transport", "station"): "transit", ("highway", "bus_stop"): "transit",
    ("railway", "station"): "transit", ("railway", "tram_stop"): "transit",
}
SUMMARY_ORDER = ["grocery", "school", "park", "playground", "transit", "pharmacy", "health", "cafe",
                 "restaurant", "fitness", "library", "shopping", "worship"]
SYNONYMS = {
    "grocery": ["grocery", "groceries", "supermarket", "food store", "market"],
    "school": ["school", "schools", "kindergarten", "preschool", "college", "university", "education"],
    "park": ["park", "parks", "green", "trail", "outdoor"],
    "playground": ["playground", "kids", "children"],
    "transit": ["transit", "bus", "train", "rail", "station", "metro", "public transport"],
    "pharmacy": ["pharmacy", "drugstore", "chemist"],
    "health": ["hospital", "clinic", "doctor", "doctors", "health", "medical", "urgent care"],
    "cafe": ["cafe", "coffee", "bakery", "espresso"],
    "restaurant": ["restaurant", "restaurants", "food", "eat", "dinner", "lunch", "takeout"],
    "fitness": ["gym", "fitness", "workout", "sports"],
    "library": ["library", "books"],
    "shopping": ["shop", "shopping", "mall", "store", "convenience"],
    "worship": ["church", "temple", "synagogue", "mosque", "worship"],
}
STATUS = {1: "active", 2: "pending", 4: "sold"}


# --- amenities --------------------------------------------------------------------------------


def amenities(twin: PropertyTwin, pois: list[dict]) -> list[Amenity]:
    """Every categorised point of interest, nearest first; one per name and category (both
    directions of a bus stop count once)."""
    out: dict[tuple[str, str], Amenity] = {}
    for p in pois:
        category = CATEGORIES.get((p["key"], p["value"]))
        if category is None:
            continue
        a = Amenity(name=p["name"], category=category, lat=p["lat"], lon=p["lon"], source="osm",
                    distance_m=round(distance_m(twin.lat, twin.lon, p["lat"], p["lon"]), 1), kind=p["value"])
        key = (category, p["name"].strip().lower())
        if key not in out or a.distance_m < out[key].distance_m:
            out[key] = a
    return sorted(out.values(), key=lambda a: (a.distance_m, a.name))


def nearest_per_category(all_amenities: list[Amenity], n: int = PER_CATEGORY) -> list[Amenity]:
    counts: dict[str, int] = {}
    kept = []
    for a in all_amenities:
        if counts.get(a.category, 0) < n:
            counts[a.category] = counts.get(a.category, 0) + 1
            kept.append(a)
    return kept


def search(all_amenities: list[Amenity], query: str, limit: int = 10) -> list[Amenity]:
    """Amenities matching a free-text query ("coffee", "bus stop", "H-E-B"), nearest first."""
    q = query.strip().lower()
    words = set(re.findall(r"[a-z0-9-]+", q))
    categories = {c for c, syns in SYNONYMS.items() if any(s in q if " " in s else s in words for s in syns)}
    hits = [a for a in all_amenities if a.category in categories or (q and q in a.name.lower())]
    return hits[:limit]


def walk_min(distance: float) -> int:
    return max(1, round(distance * DETOUR / WALK_M_PER_MIN))


# --- listing and comps ------------------------------------------------------------------------


def _price(row: dict) -> tuple[int | None, str | None]:
    status = STATUS.get(row.get("listing_status"))
    if status == "sold":
        if row.get("close_price"):
            return int(row["close_price"]), "sale"
        return (int(row["list_price"]), "last_asking") if row.get("list_price") else (None, None)
    price = row.get("current_price") or row.get("list_price")
    return (int(price), "asking") if price else (None, None)


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def listing_facts(row: dict) -> ListingFacts:
    price, kind = _price(row)
    return ListingFacts(
        listing_id=row["id"], status=STATUS.get(row.get("listing_status"), "off_market"), price=price,
        price_kind=kind, list_date=_date(row.get("list_date")), days_on_market=row.get("dom"),
        sqft=_int(row.get("sqft")), beds=row.get("beds"), baths=row.get("baths"),
        year_built=row.get("year_built"), lot_acres=row.get("lot_acres"), hoa_monthly=row.get("hoa_monthly"),
        subdivision=row.get("subdivision"), neighborhood=row.get("neighborhood"), zip=row.get("zip"),
        highlights=row.get("highlights") or [],
    )


def comps(twin: PropertyTwin, subject: dict | None, nearby: list[dict], n: int = COMPS) -> list[Comp]:
    """The n most comparable public for-sale listings nearby: close, similar size and bedrooms,
    sold preferred over still asking. The home itself is excluded."""
    candidates = []
    for r in nearby:
        status = STATUS.get(r.get("listing_status"))
        price, kind = _price(r)
        if status is None or not r.get("primary") or price is None or price < 10_000 or r.get("lat") is None:
            continue
        if subject is not None and r.get("property_id") == subject.get("property_id"):
            continue
        d = distance_m(twin.lat, twin.lon, r["lat"], r["lon"])
        score = d / 2000 + (0.0 if status == "sold" else 0.25)
        if subject and subject.get("sqft") and r.get("sqft"):
            score += 2 * abs(math.log(r["sqft"] / subject["sqft"]))
        if subject and subject.get("beds") is not None and r.get("beds") is not None:
            score += 0.15 * abs(r["beds"] - subject["beds"])
        sqft = _int(r.get("sqft"))
        candidates.append((score, Comp(
            address=r.get("address") or "address withheld", price=price, sqft=sqft, beds=r.get("beds"),
            sold_date=_date(r.get("close_date")) if status == "sold" else None, distance_m=round(d, 1),
            status=status, price_kind=kind, baths=r.get("baths"), year_built=r.get("year_built"),
            price_per_sqft=round(price / sqft, 1) if sqft else None, lat=r["lat"], lon=r["lon"],
            listing_id=r["id"],
        )))
    return [c for _, c in sorted(candidates, key=lambda x: (x[0], x[1].listing_id))[:n]]


def _int(x) -> int | None:
    return None if x is None else int(round(x))


# --- the profile ------------------------------------------------------------------------------


def build_profile(twin: PropertyTwin, subject: dict | None, nearby: list[dict] | None, pois: list[dict] | None,
                  caveats: list[str]) -> AreaProfile:
    caveats = list(caveats)
    facts = listing_facts(subject) if subject else None
    comp_list = comps(twin, subject, nearby or [])
    all_amenities = amenities(twin, pois or [])
    if any(c.price_kind == "last_asking" for c in comp_list) or (facts and facts.price_kind == "last_asking"):
        caveats.append("Texas does not disclose sale prices: sold homes show their last asking price.")
    if pois is not None:
        caveats.append(f"Amenities are OpenStreetMap places within {AMENITY_RADIUS_M / 1000:g} km; "
                       "distances are straight-line and walking times assume streets add 30%.")
    return AreaProfile(
        markdown=markdown(twin, facts, comp_list, all_amenities, caveats),
        amenities=nearest_per_category(all_amenities),
        comps=comp_list,
        listing=facts,
        caveats=caveats,
    )


def markdown(twin: PropertyTwin, facts: ListingFacts | None, comp_list: list[Comp], all_amenities: list[Amenity],
             caveats: list[str]) -> str:
    lines = [f"## {twin.address}", ""]
    if facts:
        verb = {"active": "Listed at", "pending": "Under contract at", "sold": "Sold at"}.get(facts.status, "Last listed at")
        head = []
        if facts.price:
            dom = f", {facts.days_on_market} days on market" if facts.days_on_market is not None else ""
            head.append(f"**{verb} {_money(facts.price)}** ({facts.status}{dom})")
        if facts.sqft:
            per = f" ({_money(facts.price / facts.sqft)}/sq ft)" if facts.price else ""
            head.append(f"{facts.sqft:,} sq ft{per}")
        if facts.beds is not None:
            head.append(f"{facts.beds} bd")
        if facts.baths is not None:
            head.append(f"{facts.baths:g} ba")
        if facts.year_built:
            head.append(f"built {facts.year_built}")
        if facts.lot_acres:
            head.append(f"{facts.lot_acres:.2f} acre lot")
        if facts.hoa_monthly:
            head.append(f"HOA {_money(facts.hoa_monthly)}/mo")
        lines.append(" · ".join(head))
        place = ", ".join(x for x in (facts.neighborhood, facts.subdivision and f"{facts.subdivision} subdivision") if x)
        if place:
            lines += ["", place + "."]
        if facts.highlights:
            lines += ["", "Highlights: " + ", ".join(facts.highlights[:10]) + "."]
    else:
        lines.append("No CoStar listing is linked to this property.")

    lines += ["", "### Price compared with nearby homes", ""]
    priced = [c for c in comp_list if c.price_per_sqft]
    if priced:
        med = median(c.price_per_sqft for c in priced)
        lines.append(f"{len(comp_list)} comparable homes within {COMP_RADIUS_M / 1000:g} km: median "
                     f"{_money(med)}/sq ft.")
        if facts and facts.price and facts.sqft:
            own = facts.price / facts.sqft
            lines.append(f"This home is {_money(own)}/sq ft, {abs(own / med - 1):.0%} "
                         f"{'above' if own >= med else 'below'} that median.")
        lines += ["", "| Address | Status | Price | Sq ft | $/sq ft | Beds | Distance |", "|---|---|---|---|---|---|---|"]
        for c in comp_list:
            status = c.status + (f" {c.sold_date:%b %Y}" if c.sold_date else "")
            price = _money(c.price) + (" (last ask)" if c.price_kind == "last_asking" else "")
            cells = [c.address, status, price, f"{c.sqft:,}" if c.sqft else "?",
                     _money(c.price_per_sqft) if c.price_per_sqft else "?",
                     str(c.beds) if c.beds is not None else "?", f"{c.distance_m / 1000:.1f} km"]
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append("No comparable listings available.")

    lines += ["", "### Nearby", ""]
    nearest = {}
    for a in all_amenities:
        nearest.setdefault(a.category, a)
    if nearest:
        for category in SUMMARY_ORDER:
            a = nearest.get(category)
            if a is None:
                continue
            count = sum(1 for x in all_amenities if x.category == category)
            more = f" ({count} within {AMENITY_RADIUS_M / 1000:g} km)" if count > 1 else ""
            lines.append(f"- **{category.capitalize()}:** {a.name}, {_distance(a.distance_m)}{more}")
    else:
        lines.append("No amenity data available.")

    if caveats:
        lines += ["", "### Caveats", ""] + [f"- {c}" for c in caveats]
    return "\n".join(lines) + "\n"


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _distance(d: float) -> str:
    where = f"{d:.0f} m" if d < 1000 else f"{d / 1000:.1f} km"
    return f"{where} (~{walk_min(d)} min walk)"
