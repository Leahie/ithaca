---
from: B
to: A
date: 2026-11-14
status: open
source: stream-b/agent-v0
---
# The agent now learns from how the user tours: publish what the user does

**Why:** a background "observer" agent reads a log of the tour (time per room, views opened, times
of day checked) and writes inferred preferences to the user's memory. The log is built only from
events on the session WebSocket, so it sees only what the desktop publishes.

## What you need to do
- [ ] **Publish user actions as events, not only agent-driven ones** (this is A1, one store two
      inputs): `viewer.goto {room_id}` when the user changes room, `viewer.setMode` when they
      switch tab, `viewer.setTime` when they scrub the time. If a click changes the view without
      an event, the observer never learns about it.
- [ ] **`viewer.goto` on room change only**, not on every sweep inside a room. Time in a room is
      measured from one `viewer.goto` to the next.
- [ ] **In `/me`, show the "Tour observations" section** of `properties/<property_id>.md` and
      mark preference lines that contain `inferred`, so users can see and delete what was learned
      by watching. The agent tells users on their first visit that it does this.

## Details
- No new event types. After enough activity the guide may speak unprompted with a suggestion
  (a normal `agent.turn` with `agent.token` / `agent.message`).
- C's property routes are now served by the same server as the session routes:
  `uvicorn services.api.main:app`. See C's note `2026-11-14-from-c-rest-endpoints.md`.
- `GET /api/health` reports `"observer": true|false`. It is off with the scripted agent.
