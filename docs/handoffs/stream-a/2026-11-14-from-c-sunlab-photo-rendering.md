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

## From C (live now, twin version 7)
- `Sweep.rotation: {x, y, z, w}`: takes pano-frame directions to the interior frame.
- `GET /api/properties/{pid}/sweeps/{sweep_id}/skybox/{face}?res=low|2k|high|4k` JPEG, `face` in
  `px nx py ny pz nz`, so
  `new CubeTextureLoader().load(['px','nx','py','ny','pz','nz'].map(f => url(f)))` works as is
  (set `colorSpace = SRGBColorSpace`). `low` is 256 px faces, `2k` is 2048 px. The first request
  per sweep takes a few seconds (C downloads and re-cuts it); after that it is a file read.
- The lookup, verified against the point cloud's colors (which come from the same photos):

```glsl
uniform samplerCube pano;  // the sweep's six faces
uniform vec3 eye;          // sweep.position
uniform mat3 toPano;       // inverse(sweep.rotation) as a mat3
varying vec3 vWorld;       // world position of the fragment
vec4 photo = textureCube(pano, toPano * (vWorld - eye));  // no axis flips needed
```

- **Working reference:** `services/api/twin/static/walk.html`. Run
  `uvicorn services.api.twin.app:app --port 8012` and open
  `http://127.0.0.1:8012/dev/walk.html?sweep=<sweep_id>` (or `?room=<room_id>`). It has walk mode
  (drag to look, click the floor or press the up arrow to move along links, a 0.6 s crossfade),
  sun from `GET .../sun/at` through a hand-rolled shadow map (the mesh's depth seen from the
  sun), and the time slider. Copy what helps.

## Things learned building it
- **Add a backdrop.** Wherever the mesh has no surface (through windows, since the yard is
  trimmed from `interior.glb`), nothing is drawn. A sphere of radius 150 m centered on the eye
  with the same material fills those pixels with the exact photo.
- **Normals:** `normalize(cross(dFdx(vWorld), dFdy(vWorld)))`, flipped to face the eye. The
  mesh's own normals are not needed.
- **Sun look:** `photo * (sky_level + 0.9 * lit * max(n·l, 0) * vec3(1.0, 0.9, 0.72))` with a
  3x3 PCF on the depth map reads well. `sky_level` 0.55 to 1.0 by sun elevation, 0.18 at night.
- **Known seams in the photos themselves:** some panoramas have a faint exposure seam where
  Matterport stitched two shots (for example on the primary bedroom floor). The face re-cut is
  pixel-exact, so this is in Matterport's data.
