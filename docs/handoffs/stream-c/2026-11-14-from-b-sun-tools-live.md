---
from: B
to: C
date: 2026-11-14
status: open
source: stream-b/agent-v0
---
# Your sun and daylight engines are in the agent; router is mounted

**Why:** B merged `stream-c/twin-v0` (twin version 3). The agent and the new background tour
observer now call your engines.

## What you need to do
- [ ] **Delete `services/api/twin/app.py`** when convenient. `services/api/main.py` mounts
      `routers/properties.py` under `/api`; run everything with `uvicorn services.api.main:app`.
- [ ] **Keep these fast and stable, the agent calls them in-process:** `sun_room_day`,
      `sun_report`, `daylight_at(..., room_id=None, grids=False)`. `sun_by_room` with no room calls
      `sun_room_day` twice (floor and 1.1 m) for every windowed room in one tool call.
- [ ] **Optional: a name or hint for rooms that aren't what their label says.** The agent told a
      test user "the Office has no windows" because the Floor 1 Office has no window openings in
      the twin. If that's a detection gap, the agent is now saying something false with confidence.

## Details
- Wrapped as agent tools in `services/api/agent/tools.py`: `sun_by_room(day, room_id?)`,
  `sun_report(room_id, start_date, end_date)`, `daylight_at(datetime_iso, room_id?, sky)`.
  They return short text: clock times, hours, and your brightness levels. No lux, no grids.
- `daylight_at` defaults to `sky="typical"` and falls back to `"clear"` on `IntegrationNotConfigured`.
- Not wrapped: `sun_spot`, `daylight_spot`, `sun_at`. They need `x, z`, and the agent never
  handles coordinates (B1). They become useful once a placed furniture item gives a position.
- `quality.context: none` is passed to the user as "an upper bound, ignores neighbors, trees and
  overhangs".
- The merge kept your `export.py` and regenerated `packages/schema/json/`.
