---
from: C
to: B
date: 2026-11-14
status: open
source: stream-c/twin-v0
---
# Mount the panorama router

**Why:** the Sun Lab now draws the scan photos instead of the mesh (see A's handoff
`2026-11-14-from-c-sunlab-photo-rendering.md`). The faces come from a new C router.

## What you need to do
- [ ] In `services/api/main.py`, next to `properties.router`:
      `from services.api.routers import panos` and `app.include_router(panos.router, prefix="/api")`.
      It adds `GET /api/properties/{pid}/sweeps/{sweep_id}/skybox/{face}`.
- [ ] The first request per sweep calls Matterport (same `MATTERPORT_API_KEY` / `MATTERPORT_API_TOKEN`
      as ingestion; 503 `not_configured` without them) and caches under `data/matterport/` (gitignored).
