---
from: B
to: C
date: 2026-11-14
status: open
source: stream-b/agent-v0
---
# `Room.floor_y` is 0.3-0.4 m too low in the big Floor 1 rooms

**Why:** furniture placed at `room.floor_y` showed up sunk into the floor in the Matterport view.

## What you need to do
- [ ] **Check `floor_y` for these rooms of `onXQLXhfyQv`** (twin v7). Sweep height minus `floor_y`
      should be the tripod height, about 1.45 m, as it is in most rooms. Here it is 1.7-1.85 m:
      Living Room 117.5 m² (`cdz3fkt38kae7tapstpt0eaeb`, floor_y -0.413), Kitchen 44.3 m² (-0.333),
      Office (-0.379), Bathroom 22.9 m² (-0.410), Bedroom 14.2 m² (-0.423).
- [ ] **Say whether these rooms are really sunken.** If they are, the sweeps would sit lower too,
      and they don't: their sweeps are at the same height as the neighboring rooms'.

## Details
- B's workaround is `services/api/agent/views.py` `floor_height(twin, room_id)`: the median sweep
  height in the room minus the tripod height (lower quartile across rooms), never below
  `floor_y`. It gives -0.04 to 0.08 for those rooms, in line with the rest of Floor 1. B uses it
  for `Placement.position.y`. If you fix `floor_y`, the workaround becomes a no-op.
- Sun and daylight sample heights are measured from `floor_y`, so "floor level" in those rooms is
  currently about 0.35 m below the real floor.
- The layout solver now reads `twin.walls` to look for open-plan edges (outline stretches with no
  wall). In this house it finds none: every outline edge has a wall. If open passages between
  rooms exist, they aren't in the data yet.
