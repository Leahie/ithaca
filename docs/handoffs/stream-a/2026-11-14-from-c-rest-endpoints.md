---
from: C
to: A
date: 2026-11-14
status: open
source: stream-c/twin-v0 @ e6248b9
---
# Property REST endpoints are live (twin, sun, daylight, openings, assets)

**Why:** A can now fetch everything from HTTP instead of reading `fixtures/` directly: the twin
(with real asset URLs), direct sun, and daylight at any local time and height.

## What you need to do
- [ ] Point the web app at the API for the twin: `GET /api/properties/{pid}/twin`. Use
      `assets.interior_glb` as given (it is now a full URL, no `/fixtures/` prefix).
- [ ] Wire the time scrubber to `GET .../sun/at` (3D/plan sun patches) or `GET .../daylight/at`
      (lux overlay), passing `when` as local time and `sky` from the weather toggle.
- [ ] Snap the height control to the presets 0 / 0.8 / 1.1 / 1.6 m. 0 and 1.1 are warmed when the
      twin loads; any other height costs about 7 s the first time, then about 30 ms.
- [ ] Annotation mode saves with `PUT .../openings` (below).

## Running it
Until B's `main.py` mounts the router (requested in
`docs/handoffs/stream-b/2026-11-14-from-c-mount-properties-router.md`), run C's routes alone. Same
paths, same `/api` prefix:

```bash
pip install -r services/api/twin/requirements.txt
uvicorn services.api.twin.app:app --reload --port 8000   # docs at http://localhost:8000/docs
```

CORS is open in dev. Every route accepts `X-User-Id` (default `demo`). Types are the Pydantic
models in `packages/schema/{twin,reports}.py`; the OpenAPI schema at `/openapi.json` matches them.

## Endpoints (all under `/api/properties/{pid}`)

| Route | Returns | Notes |
|---|---|---|
| `GET /twin` | `PropertyTwin` | asset keys resolved to URLs |
| `PUT /openings` (body `Opening[]`) | `Opening[]` | replaces all openings; see below |
| `GET /sun?room_id&start&end[&height_m=0]` | `SunReport` | contract route; at most 366 days |
| `GET /sun/at?when[&height_m=0]` | `SunMoment` | every room's direct-sun patch with `mask` |
| `GET /sun/day?room_id&date[&height_m=0]` | `SunRoomDay` | intervals when any of the room is lit |
| `GET /sun/spot?room_id&x&z&date[&height_m=1.1]` | `SunSpot` | "when does sun hit my desk?" |
| `GET /daylight/at?when[&sky=clear][&height_m=0][&room_id][&grids=true]` | `DaylightMoment` | lux per room, `grid` per room |
| `GET /daylight/spot?room_id&x&z&date[&sky=clear][&height_m=1.1]` | `DaylightSpot` | 15-min lux series for one spot |
| `GET /noise?room_id[&glazing][&add_carpet]` | `NoiseReport` | 503 not configured (not built yet) |
| `GET /area` | `AreaProfile` | listing facts, comps, amenities, markdown (see below) |
| `GET /area/search?q[&limit=10]` | `Amenity[]` | free text: "coffee", "bus", "Kroger" |
| `GET /area/comps` | `Comp[]` | same as `AreaProfile.comps` |
| `POST /ingest` | `{job_id}` | rebuild the twin; poll `GET /ingest/{job_id}` |

Assets: `GET /api/assets/{key}` serves files under `fixtures/blobs/` (Range requests work, so
loaders can stream). You never build these URLs; take them from `twin.assets`.

### Parameters
- `when`: ISO 8601. Without an offset it is property-local (`2026-12-21T09:00`); with one it is
  converted (`2026-12-21T09:00-06:00`). Responses always carry the property offset (I-G13).
- `date`, `start`, `end`: `YYYY-MM-DD`, property-local days.
- `height_m`: sample height above each room's floor, 0 to 3 m. 0 floor, 0.8 desk, 1.1 seated,
  1.6 standing.
- `sky`: `clear` or `typical` (NSRDB typical-year weather; offline for `onXQLXhfyQv`). In
  `typical`, treat `dni_w_m2 < 120` as overcast (no sun patch), as in the daylight note.
- `x`, `z`: interior frame (I-G9), must be inside the room (422 otherwise).
- `grids=false` on `daylight/at` drops the per-cell grids: about 2 KB instead of about 60 KB.
  Use it for scrubbing summaries; fetch grids for the frame you stop on.

### Errors
`404` unknown property, room or job · `422` bad input (bad time, height out of range, point
outside the room, invalid openings) · `503` feature not configured, body
`{"detail": {"error": "not_configured", "service": "...", "message": "..."}}` (I-G21). Show the
message; don't retry.

### Opening annotations
`PUT /openings` takes the full list (same shape as `twin.openings`, edit and send all of them).
C checks: unique `opening_id`, known `room_id` / `other_room_id`, 4 corners in order
(bottom-start, bottom-end, top-end, top-start), `facade_azimuth_deg` in [0, 360). On success
the twin's `version` goes up by one and `quality.openings` becomes `"annotated"`; re-fetch the twin.
Sun and daylight answers follow the new openings immediately. Annotations survive re-ingestion.

### Area profile (Neighborhood tab)
Added later the same day; schema changes are additive (re-run your TS codegen from
`packages/schema/json/reports.schema.json`).
- `listing` (`ListingFacts`): the home's own CoStar listing (price, status, days on market, sq ft,
  beds, baths, year, lot, HOA, neighborhood, subdivision, `highlights`). Null if not linked.
- `comps` (`Comp[]`): the 8 most comparable homes within 5 km, best first, each with `lat`/`lon`
  for the map, `status` (`active`/`pending`/`sold`), `price_per_sqft`, and `price_kind`. Show
  `last_asking` as "last asking price": Texas doesn't disclose sale prices.
- `amenities` (`Amenity[]`): OpenStreetMap places within 2 km, the nearest 5 per `category`
  (grocery, school, park, playground, transit, pharmacy, health, cafe, restaurant, fitness,
  library, shopping, worship). `distance_m` is straight-line.
- `markdown`: a ready summary (facts, price vs comps table, nearest of each kind with walking
  minutes). Fine to render as-is in a side panel.
- `caveats`: show them as small print. Missing sources appear here, never as an error.

### Speed (this laptop, after the twin is fetched)
`twin` 30 ms · `sun/at` 20-160 ms · `daylight/at` 130-380 ms (first `typical` call per day
reads the weather fixture) · `sun` (one month) 20 ms · `interior.glb` (18 MB) 60 ms on localhost.
Answers are cached per twin version, so scrubbing back to a moment is instant.
