---
from: B
to: A
date: 2026-11-14
status: open
source: stream-b/agent-v0 @ 850c835
---
# How the agent drives the UI, and what it needs from the desktop

**Why:** the agent graph now exists (`services/api/agent/`). It emits UI events and reacts to app
events. The WebSocket hub and `packages/schema/events.py` are not built yet, so nothing here is
on the wire today. Build against the v0 event table in WORKSTREAMS.md section 2.3 with these
points in mind.

## What you need to do
- [ ] **Handle `viewer.goto` with `room_id` only.** The agent never sends `sweep_id`. Pick the
      sweep yourself (recipe in `docs/stream-a-handoff.md`, "Going to a room").
- [ ] **Send `viewer.goto {room_id}` from the desktop when the user walks into a different room**
      in Showcase (source `desktop`). The agent uses this to comment on the room the user is in.
      Send it on room change, not on every sweep.
- [ ] **Show agent text that arrives without a `chat.user` before it.** The agent speaks first
      when a house is opened and sometimes when a room is entered.
- [ ] **Don't wait for an `agent.message` after every user or app event.** The agent may decide to
      say nothing. See "Turn end" below.
- [ ] **Label rooms the way the agent names them:** `label · floor · area`, e.g.
      "Bedroom · Floor 2 · 20.6 m²". Labels repeat (6 bedrooms), so the agent tells
      rooms apart by floor and size.

## Details

### Events the agent emits today
| Event | Payload | Notes |
|---|---|---|
| `viewer.goto` | `{"room_id": "…"}` | Only ids that exist in the twin. |
| `viewer.setMode` | `{"mode": "tour" \| "sun" \| "sound" \| "neighborhood" \| "design"}` | |
| `viewer.setTime` | `{"datetime": "2026-12-21T08:00:00-06:00"}` | Always has the property's UTC offset (I-G13). The agent usually sends `viewer.setMode sun` in the same turn. |

Not emitted yet: `design.showVariants`, `sound.setScenario` (they come with layouts and noise).

### Per turn you will receive
- `agent.token` deltas while the agent writes.
- `agent.action {tool, summary}` for each tool call (also memory edits, e.g. tool `edit_file`).
- The UI events above, in the order the agent made them, before the text that follows them.
- `agent.message` with the final text. **Absent when the agent stays silent.**

### Turn end (proposal, not in schema v0 yet)
Because a turn can end with no `agent.message`, B plans to add an additive event
`agent.turn {status: "started" | "ended"}` when the hub lands. Use it for the typing indicator.
Until then, don't block the UI on a reply. Tell B if you want a different shape.

### App events the agent reacts to
The hub will turn these into agent turns. No new event types for A:
| Agent sees | Comes from |
|---|---|
| property opened | desktop joins a session (`session.joined`) |
| room entered | desktop `viewer.goto {room_id}` (second item above) |
| object ready | `job.progress` done for an object ingest |
| layout chosen / rejected | `layout.select`, `POST /layouts/{id}/feedback` |

### Memory files (for the `/me` editor, REST not built yet)
`profile.md`, `preferences.md` (sections Hard / Soft / Style), `properties/<property_id>.md`,
and `inventory.md`. `inventory.md` is generated from the database: show it read-only.
