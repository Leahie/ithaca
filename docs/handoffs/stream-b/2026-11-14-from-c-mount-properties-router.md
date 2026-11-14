---
from: C
to: B
date: 2026-11-14
status: open
source: stream-c/twin-v0 @ e6248b9
---
# Mount C's property router in `main.py`

**Why:** C's REST routes (WORKSTREAMS.md section 2.4 plus sun/daylight moments) are in
`services/api/routers/properties.py`. Your `main.py` docstring says C adds them with
`include_router`, but `main.py` is yours and not on C's branch, so C hasn't touched it.

## What you need to do
- [ ] In `services/api/main.py`:
      ```python
      from services.api.routers import properties, sessions
      app.include_router(properties.router, prefix="/api")
      ```
- [ ] Nothing else: no lifespan state, no new env vars. `services/api/routers/__init__.py` is
      empty on both branches.

## Details
- Until then C runs its routes alone with `uvicorn services.api.twin.app:app` (same paths). Once
  yours mounts the router, `twin/app.py` can go; tell C and C will delete it.
- `GET /properties/{pid}/twin` also starts a background thread that warms sun/daylight geometry
  for that twin version (once per version). It's CPU-bound numpy/shapely work for about 40 s on
  the first fetch; requests still get served meanwhile.
- Agent tools keep calling `twin.service` in-process; nothing changes for you there. New:
  `service.daylight_at(..., room_id=None, grids=True)` can limit to one room and drop grids,
  which is a smaller payload for tool results.
- `PUT /properties/{pid}/openings` rewrites `fixtures/twin/<pid>.json` (version bump). If the agent
  caches a twin, key the cache on `twin.version`.
- C copied your `packages/schema/{events,session,export}.py` and `json/` onto `stream-c/twin-v0`
  unchanged, except two edits to `export.py`: a `reports` group (so A gets TS types for the
  sun/daylight/noise/area responses, `json/reports.schema.json`), and `write_text(...,
  newline="\n")` so Windows doesn't write CRLF. `twin.schema.json` is regenerated (adds
  `Opening.exterior`). Take C's versions of these when merging, or re-run
  `python -m packages.schema.export`.
- C's `twin/requirements.txt` now pins `fastapi>=0.115`, `uvicorn[standard]>=0.30`,
  `httpx>=0.27`, the same as your `requirements-b.txt`.
