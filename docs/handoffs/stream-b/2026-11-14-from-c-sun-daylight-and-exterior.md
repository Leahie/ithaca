---
from: C
to: B
date: 2026-11-14
status: open
source: stream-c/twin-v0 @ 338958f
---
# Sun and daylight functions for agent tools, `Opening.exterior`, and your four requests

**Why:** C answered B's note (`docs/handoffs/stream-c/2026-11-14-from-b-agent-uses-twin.md`) and
added sun and daylight questions the agent can answer at the property's local time.

## What you need to do
- [ ] Use `Opening.exterior` for doors: `True` = leads outside, `False` = interior (if
      `other_room_id` is null, it opens into unlabeled space), `None` = unknown (older twins).
- [ ] Wrap the new functions below as agent tools (or extend your sun tool). All live in
      `services/api/twin/service.py`; the schemas are in `packages/schema/reports.py`.
- [ ] Catch `IntegrationNotConfigured` from `daylight_at(..., sky="typical")` and
      `daylight_spot(..., sky="typical")` (NSRDB weather needs `NREL_API_KEY` + `NREL_EMAIL`). Offer
      `sky="clear"` instead, which always works.
- [ ] Rebase or merge `stream-c/twin-v0`: the twin is now **version 3** (adds `exterior`).

## Details

### Your four requests
1. `trimesh` (plus `pandas`, `Pillow`, `pvlib`, `h5py`) is in `services/api/twin/requirements.txt`.
2. `Opening.exterior: bool | None` is added (I-G6, additive). It's a heuristic: an opening with
   no room on its far side leads outside unless another room outline (labeled or not) starts
   within 2 m beyond it. This house: 18 windows and 7 doors outside, 3 doors into unlabeled space.
   The Floor 2 Living Room's "outside" door may be the top of a stairwell nobody labeled.
3. `get_twin` and `config.{env, IntegrationNotConfigured, REPO_ROOT}` stay where they are.
4. `noise_report`, `area_profile`, `area_search` and `get_comps` exist with the section 2.5
   signatures and raise `IntegrationNotConfigured` until they're built.

### New functions (times are property-local; naive datetimes are read in `twin.timezone`)
| Function | Answers | Returns |
|---|---|---|
| `sun_at(pid, when, height_m=0)` | "Is the sun in the bedroom at 9am? Through which window?" | `SunMoment` |
| `sun_spot(pid, room_id, x, z, day, height_m=1.1)` | "When does the sun hit me at the desk?" | `SunSpot` (intervals) |
| `sun_room_day(pid, room_id, day, height_m=0)` | "When does the bedroom get sun?" | `SunRoomDay` (intervals) |
| `sun_report(pid, room_id, start, end, height_m=0)` | Sun hours per day over a range (2.5 contract, `height_m` added) | `SunReport` |
| `daylight_at(pid, when, sky="clear", height_m=0)` | "How bright is each room at 3pm?" (lux, sky + sun + reflected) | `DaylightMoment` |
| `daylight_spot(pid, room_id, x, z, day, sky="clear", height_m=1.1)` | "How bright is it at the desk through the day?" | `DaylightSpot` |

- `height_m` is above the room's floor: 0 = floor, 0.8 = desk, 1.1 = seated person
  (`sim.sun.SEATED_M`), 1.6 = standing head. A seated person can be in sun when the floor isn't
  (high windows, low winter sun).
- `x, z` are interior-frame meters (the same frame as `Room.polygon`).
- `sky` is the user's choice (the UI should offer it): `"clear"` = a clear day,
  `"typical"` = NSRDB typical-year weather for that date and hour (Houston is often hazy).
- `RoomDaylight.level` is one of `dark | dim | moderate | bright | very_bright` (<50, <300, <1000,
  <3000 lux, after Useful Daylight Illuminance). Use it in speech instead of raw lux.
- Only rooms with an exterior window appear in `SunMoment.rooms` / `DaylightMoment.rooms`. A
  missing room means "no daylight of its own" (it may borrow some from an open-plan neighbor).

### Caveats the agent should say when relevant
- No neighbors, trees or roof overhangs yet (`quality.context: none`), so sun is an upper bound.
- No furniture. Reflected light is averaged over each room. Against Radiance ray tracing the
  median spot is within 4-12% in three test cases (overcast, low morning sun, evening sun), and
  about 50% high in a small kitchen with a big midday sun patch. Speak in levels, not exact lux.
