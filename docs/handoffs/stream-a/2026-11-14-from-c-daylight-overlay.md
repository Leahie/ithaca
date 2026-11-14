---
from: C
to: A
date: 2026-11-14
status: open
source: stream-c/twin-v0 @ 338958f
---
# Daylight overlay, weather toggle, and `Opening.exterior`

**Why:** C now computes where direct sun falls and how bright each spot is (lux), at the
property's local time, for a clear day or the typical-year weather. A can show this on the floor
plan and in the Sun Lab next to the realtime shadows.

## What you need to do
- [ ] Add a weather toggle next to the time scrubber: `clear` (a clear day) or `typical` (NSRDB
      typical-year weather). The user chooses. Pass it as `sky` when the REST endpoints land.
- [ ] Optional floor-plan overlay: draw `RoomDaylight.grid` (`LuxGrid`) per room on a **log**
      color scale (20 to 20,000 lux; `out/twin/daylight_*.png` from
      `python -m services.api.twin.daylight_cli` shows the intended look).
- [ ] Twin is version 3: `Opening.exterior: bool | None` tells you which doors and windows lead
      outside (for icons or labels).

## Details
- `LuxGrid` uses the same grid convention as `SunHeatmap` (`origin_xz`, `cell_m`, row-major in
  z then x, interior frame); cells outside the room are `null`.
- `SunMoment.rooms[].mask` (`SunMask`) is the direct-sun patch at one instant if you want crisp
  sun patches on the 2D plan; in 3D, keep the realtime directional light (recipe in
  `docs/stream-a-handoff.md` section 5).
- REST endpoints aren't built yet. Planned: `GET /properties/{pid}/sun/at?when&height_m`,
  `GET /properties/{pid}/daylight/at?when&sky&height_m`. Until then use the CLI images, or ask C
  for JSON fixtures.
