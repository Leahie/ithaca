---
from: A
to: C
date: 2026-11-14
status: open
source: main @ 1e847d1 (uncommitted)
---
# Twin asset `house`: the house rebuilt from the floor plan

**Why:** the Sun Lab now draws the house rebuilt by the `3d_rendering_matterport` project
(clean walls, real window holes, baked light, Vexcel surroundings) instead of the scanned
`interior_glb`. The twin needs to point at it.

## What you need to do
- [ ] Review the additive field `TwinAssets.house: str | None` (`packages/schema/twin.py`, I-G3,
      I-G6). It is the key of a `house.json`; `house.glb`, `light_sky.png`, `light_lamps.png`,
      `light_sun_<k>.png`, `context.glb` and `context.jpg` sit beside it.
- [ ] `twin/ingest.py` `_assets` now sets it when `fixtures/blobs/<pid>/house/house.json`
      exists, and `routers/properties.py` turns it into a URL. Check that this matches how you
      want ingestion to own the asset; the files themselves come from
      `3d_rendering_matterport` (`pnpm house all`, then copy `data/house/*`).
- [ ] Consider adopting `house.json` `orientation.north` (measured on Vexcel's DSM: 0.07 degrees
      against Matterport's 6.56) for `interior_to_world`. The Sun Lab already uses it for the
      sun's direction; the server's sun report still uses the twin's transform, so the heatmap
      and the drawn sun disagree by about 6.5 degrees until then.

## Details
- `fixtures/twin/onXQLXhfyQv.json` has `"house": "blobs/onXQLXhfyQv/house/house.json"` and
  version 10. The files are under `fixtures/blobs/onXQLXhfyQv/house/` (Git LFS).
- The house is in the interior frame (Matterport model (x, y, z) at (x, z, -y)); no transform.
- `interior_glb` and `context_glb` are no longer read by the Sun Lab.
