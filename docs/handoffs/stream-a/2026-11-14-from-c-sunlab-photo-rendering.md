---
from: C
to: A
date: 2026-11-14
status: open
source: stream-c/twin-v0
depends on: 2026-11-14-from-c-walk-between-scan-points.md
---
# Sun Lab: show the scan photos, use the mesh only for depth and sun

**Why:** Inside rooms the Sun Lab draws `interior.glb` directly: smeared floors, a vanity with
grey panels, a shredded bench top. Matterport's viewer looks sharp because at a scan point you see
the scan's own 360 photo; its coarse mesh is invisible and only supplies depth (for moving between
scan points and for clicking). Do the same, and let the mesh carry the sun:

```
pixel = photo(scan point, view ray) * light(time)
light = night_level .. 1 (sky brightness), plus a sun boost where the mesh is in direct sun
```

At a scan point the photo is exact however bad the mesh is, because every ray is drawn from the
same point the camera took the photo from. Mesh errors only show as slightly misplaced sun
patches, and as stretching while moving between scan points.

## What you need to do
- [ ] **Walk mode first** (previous handoff): the camera stands on a scan point and only rotates.
      That is what makes the photo exact.
- [ ] **Projective material on the interior mesh** (in walk mode, replacing its textures): for each
      fragment at world position `p`, look up the current scan point's skybox in direction
      `R⁻¹ · (p − eye)` (`R` = the sweep's rotation, see below). Draw the mesh invisibly
      (depth only) where nothing should show, or just let this material cover it.
- [ ] **Sun on top:** keep a `DirectionalLight` from `GET .../sun/at` (azimuth, altitude) with
      `castShadow`; the mesh casts and receives. In the shader, multiply the photo by
      `sky_level + sun_gain * shadow * max(0, n·l)`. The photo already contains the light at scan
      time, so keep `sun_gain` modest (about 0.6) and `sky_level` from 1.0 at midday down to about 0.15 at night.
- [ ] **Moving between scan points:** for about 0.6 s, blend the two scan points' projections
      (from the old and new eye) while the camera slides. This is Matterport's transition.
- [ ] **Overview (dollhouse) and areas with no scan point:** keep drawing the mesh's own textures.
      C is replacing `interior.glb` with a mesh rebuilt from the point cloud with the photos baked
      on, so this view improves without changes on your side.

## Coming from C (contract; endpoints land next)
- `Sweep.rotation: {x, y, z, w}`: the panorama's orientation in the interior frame.
- `GET /api/properties/{pid}/sweeps/{sweep_id}/skybox/{face}?res=low|2k` JPEG, `face` in
  `px nx py ny pz nz` (three.js cube order), so
  `new CubeTextureLoader().load(['px','nx','py','ny','pz','nz'].map(f => url(f)))` works as is.
  `low` (512 px faces) for neighbors you might move to next, `2k` for where you stand.
- A small test page in C's repo that renders one room this way, with the exact GLSL lookup
  line (cube texture handedness is easy to get backwards), verified against the photos.
