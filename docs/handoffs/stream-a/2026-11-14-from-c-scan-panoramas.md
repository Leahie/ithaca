---
from: C
to: A
date: 2026-11-14
status: open
source: stream-c/twin-v0 (scan panoramas)
depends on: 2026-11-14-from-c-sunlab-photo-rendering.md
---
# Scan panoramas are live: `Sweep.rotation` and the skybox endpoint; cleaner `interior.glb`

**Why:** This delivers the "Coming from C" part of the Sun Lab photo-rendering handoff, so the
projective material can be built against real data now.

## What you need to do
- [ ] **Load a sweep's panorama:**
      `new CubeTextureLoader().load(['px','nx','py','ny','pz','nz'].map(f => `/api/properties/${pid}/sweeps/${id}/skybox/${f}?res=2k`))`.
      Use `res=low` to prefetch neighbouring sweeps.
- [ ] **Projective lookup** (in your own `ShaderMaterial`, not three.js `envMap`, which mirrors x
      for cube textures): `textureCube(skybox, inverse(R) * (p - eye))`, where `R` is the sweep's
      `rotation` as a `mat3` and `eye` is `Sweep.position`. No other flips are needed.
- [ ] **Reload `interior.glb`:** the overview (dollhouse) mesh is rebuilt. If you frame the camera
      from the model's bounding box, recheck that framing.

## Endpoints and fields
| | |
|---|---|
| `Sweep.rotation: {x, y, z, w}` | Quaternion taking pano-frame directions to the interior frame. Set on all 96 sweeps; `null` only if Matterport has none. |
| `GET /api/properties/{pid}/sweeps/{sweep_id}/skybox/{face}?res=low\|2k\|high\|4k` | JPEG. `face` is one of `px nx py ny pz nz` (GL cube order). `low` gives 256 px faces, `2k` (default) 2048 px. |

Errors: 404 for an unknown sweep, 422 for a bad face or `res`, and 503 if the server has no
Matterport keys. The first request for each sweep and resolution takes a few seconds (download and
re-cut). After that, faces are cached files with `Cache-Control: max-age=86400`.

## Details
- **Face convention:** fitted against the point cloud's colours, which come from the same photos.
  The side faces correlate 0.70 with this convention and 0.2 or less with any other (see the
  docstring of `services/api/twin/panos.py`).
- **Checked in three.js too:** `services/api/twin/static/walk.html` renders through this lookup
  (walk mode, crossfades, sun). How to run it and what was learned is in
  `2026-11-14-from-c-sunlab-photo-rendering.md`.
- **Mesh changes in `interior.glb`:** the yard is removed (scan geometry outside the house outline,
  plus 0.5 m, is cut), and loose fragments under 200 triangles are dropped. It now has smooth
  vertex normals, so three.js no longer shades each triangle flat.
