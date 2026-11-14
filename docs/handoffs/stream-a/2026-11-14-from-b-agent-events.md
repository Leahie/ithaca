---
from: B
to: A
date: 2026-11-14
status: open
source: stream-b/agent-v0 @ 850c835
---
# How the agent drives the UI, and what it needs from the desktop

**Why:** the agent graph exists (`services/api/agent/`). It emits UI events and reacts to app
events. These are the rules of that conversation. How to run and connect to the server is in
`2026-11-14-from-b-hub-and-scripted-agent.md`.

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

### Turn end
Because a turn can end with no `agent.message`, every turn is bracketed by
`agent.turn {status: "started" | "ended"}` (additive to schema v0, in `packages/schema/events.py`).
Use it for the typing indicator.

### App events the agent reacts to
The server turns these into agent turns. No new event types for A:
| Agent sees | Comes from |
|---|---|
| property opened | desktop joins a session for the first time (built) |
| room entered | desktop or phone `viewer.goto {room_id}` to a different room (built; at most one comment per minute) |
| object ready | `job.progress` done for an object ingest (not built) |
| layout chosen / rejected | `layout.select`, `POST /layouts/{id}/feedback` (not built) |

### Memory files (for the `/me` editor, REST not built yet)
`profile.md`, `preferences.md` (sections Hard / Soft / Style), `properties/<property_id>.md`,
and `inventory.md`. `inventory.md` is generated from the database: show it read-only.
