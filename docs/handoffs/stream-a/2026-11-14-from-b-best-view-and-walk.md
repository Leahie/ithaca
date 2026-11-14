---
from: B
to: A
date: 2026-11-14
status: open
source: stream-b/agent-v0
---
# Two viewer features to play: best view of a room, and a walk between rooms

**Why:** in the tour, going to a room sometimes lands facing a wall, and the agent can now answer
"what's the walk from the bedroom to the bathroom?". The server works out where to stand, what to
face, and the route. The viewer has to play them.

## What you need to do
- [ ] **`viewer.goto` with a view.** When the payload has `at_sweep_id` and `look_at`, move to that
      sweep and face that point, instead of picking the sweep nearest the room's middle:
      `Sweep.moveTo(at_sweep_id, { rotation, transition: FLY })`, with the rotation computed from
      the sweep position to `look_at`. If `at_sweep_id` is not in the Showcase sweep list (6 twin
      sweeps aren't), use your nearest-sweep fallback and still face `look_at`.
- [ ] **New event `viewer.walk`.** Step through `sweep_ids` in order with the fly transition, then
      face `look_at` on the last one. Aim for about `duration_s` in total, with a floor of roughly
      0.6 s per step so it reads as walking. A new `viewer.goto`, `viewer.walk` or user click
      cancels a walk in progress. Skip sweeps that aren't in the Showcase.
- [ ] **Set `viewer.room_id` to `to_room_id`** when a walk starts or ends. B added an empty
      `case 'viewer.walk'` in `store/reducer.ts` so the exhaustive switch compiles; replace it.
- [ ] **Use the best view for clicks in the room list too**, so the agent and a click land the same
      way (A1). The server has no REST route for it yet. If you want one, say so and B adds
      `GET /api/properties/{pid}/rooms/{room_id}/view` returning `{at_sweep_id, look_at}`.

## Details

### Shapes (all additive, I-G6; in `packages/schema/events.py` and the regenerated `ts/index.ts`)
```jsonc
// viewer.goto: room_id as before, plus two optional fields
{ "room_id": "peina19…", "at_sweep_id": "0t5k740…", "look_at": {"x": -9.1, "y": 4.0, "z": -7.4} }

// viewer.walk: sent by the agent only
{ "from_room_id": "peina19…", "to_room_id": "tyz3uyd…",
  "sweep_ids": ["…", "…"],            // in order; first is in from_room_id, last in to_room_id
  "look_at": {"x": …, "y": …, "z": …}, // what to face on arrival
  "distance_m": 1.5, "duration_s": 1.3 }
```
`look_at` is a point in the interior frame, at about eye height. It is always inside the room.

### How to try it without an LLM
With `AGENT_PROVIDER=scripted`: "take me to the kitchen" sends `viewer.goto` with a view, and
"show me the walk to the bathroom" sends a `viewer.walk` from a bedroom to its bathroom.

### What the server does
- Best view: the sweep in the room farthest from the room's focus point (its middle, pulled toward
  its windows) that still has a clear line to it. Rooms with no sweep get a plain `room_id`.
- Route: shortest path over sweeps. Two sweeps connect if they are within 3.6 m and either share a
  room with a clear line between them, pass through a recorded door, or are a stair step apart.
- Known gaps for this house: the Floor 1 12.4 m² bedroom and its 6.2 m² bathroom can't be reached
  from the rest of the house in the scan data, so the agent says it can't trace that walk.
