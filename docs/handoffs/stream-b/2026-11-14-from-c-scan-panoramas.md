---
from: C
to: B
date: 2026-11-14
status: open
source: stream-c/twin-v0 (scan panoramas)
---
# Mount C's `panos` router; twin schema gains `Sweep.rotation`

**Why:** A's Sun Lab draws the scan photos from each sweep's panorama. Those are served by a new
router, separate from `properties.py` because it returns files rather than JSON.

## What you need to do
- [ ] In `services/api/main.py`, next to the properties router:
      ```python
      from services.api.routers import panos, properties, sessions
      app.include_router(panos.router, prefix="/api")
      ```
- [ ] **Schema:** take C's `packages/schema/twin.py` and `json/twin.schema.json`, or re-run
      `python -m packages.schema.export`. The change is additive: a new `Quat` model and
      `Sweep.rotation: Quat | None`.

## Details
- Route: `GET /api/properties/{pid}/sweeps/{sweep_id}/skybox/{face}?res=` returns a JPEG.
- **Server setup:** it needs `MATTERPORT_API_KEY` and `MATTERPORT_API_TOKEN` (it returns 503
  without them). Matterport's download links expire, so these images can't be recorded or replayed
  like other fixtures. The re-cut faces are cached under `data/matterport/<model>/skybox/`, which is
  gitignored. In Docker, mount `data/` as a volume so they aren't downloaded again on every restart.
- **Agent:** nothing changes. Ignore `rotation`; it only matters for rendering.
- **Re-ingest:** the Matterport ingest query now also asks for `rotation`, so its recorded fixture
  has a new cache key: `twin_inputs_onXQLXhfyQv-26f299fade3ecd53.json` replaces `...-984aaf6ff6280463.json`.
