---
from: C
to: B
date: 2026-11-14
status: open
source: stream-c/twin-v0 (commit "C: area profile v1")
---
# `area_profile`, `area_search` and `get_comps` are real now

**Why:** the scout subagent can answer neighborhood and price questions for `onXQLXhfyQv`
("how does the price compare?", "nearest grocery?", "is there a bus nearby?") without keys.

## What you need to do
- [ ] Point the scout tools at `twin.service.area_profile / area_search / get_comps`. They no
      longer raise `IntegrationNotConfigured`; keep your handling for other properties anyway.
- [ ] Put `AreaProfile.caveats` into the tool result and have the prompt mention them. The
      important one: Texas sold homes show their **last asking price** (`Comp.price_kind ==
      "last_asking"`), not a sale price. The agent must not call those "sold for $X".

## Details
- `area_profile(pid).markdown` is a compact summary written for an LLM: listing facts, a price vs
  comps table with the median $/sq ft and how far this home is above or below it, and the nearest
  place of each kind with walking minutes. It is usually the best single tool result.
- `area_search(pid, query, limit=10)` takes everyday words and maps them to categories ("coffee" →
  cafe, "bus"/"train" → transit, "gym" → fitness) or matches place names ("Kroger"). Nearest first,
  2 km radius, straight-line `distance_m`.
- `get_comps(pid)`: the 8 most comparable public for-sale listings within 5 km (distance, size,
  bedrooms; sold preferred). Each has `status`, `price_kind`, `price_per_sqft`, `sold_date`.
- Sources: CoStar residential listings (kit, recorded to `fixtures/integrations/costar/`) and
  OpenStreetMap (`fixtures/integrations/osm/`). Both replay offline; results are cached per twin
  version, so calls are milliseconds.
- REST mirrors these as `GET /area`, `/area/search?q&limit`, `/area/comps`.
