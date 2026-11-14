---
from: B
to: A
date: 2026-11-14
status: open
source: stream-b/agent-v0
---
# Inventory, layout and memory routes are live; the agent now makes real layouts

**Why:** the Design tab and `/me` no longer need fixtures. Asking the agent to lay out a room
produces solved layouts in the database and a `design.showVariants` event.

## What you need to do
- [ ] **Turn on `design` and `me`** (`NEXT_PUBLIC_FEATURES`) against the real API and check these
      routes with your client. They follow your README readings:
      `GET /inventory`, `GET/DELETE /inventory/{id}`, `GET /layouts?property_id&room_id`,
      `GET /layouts/{id}`, `POST /layouts/{id}/select`, `POST /layouts/{id}/feedback {feedback}`,
      `GET /memory` (relative paths, e.g. `properties/<pid>.md`), `GET/PUT/DELETE /memory/{path}`
      (text/plain).
- [ ] **Send `layout.select {layout_id}` when the user picks a card** (as well as the REST call).
      The event is what tells the agent; it then asks why and saves the reason to memory.
- [ ] **On `design.showVariants`, fetch the ids with `GET /layouts/{id}`.** They are new layouts,
      not in any list you fetched earlier. The agent sends `viewer.setMode design` just before.
- [ ] **Show `LayoutVariant.rationale` on the card.** It says in words where the pieces are
      ("sofa on the south side, facing north"), which wishes were met, and which were not.
- [ ] **Treat `inventory.md` as read-only in `/me`.** `PUT /memory/inventory.md` returns 409.

## Details
- Layouts come from `services/api/sim/layout_solver.py`. Every saved layout has all pieces inside
  the room outline, not overlapping, clear of doors and windows, with a walking path (B2). You
  don't need to check.
- `Placement.position.y` equals `room.floor_y`; `yaw_deg` follows I-G12. Same frame as your
  fixtures.
- `thumbnail_url` is always null for now: there is no render job yet. Draw the card from the
  placements (your `PlanPreview`).
- A new user starts with the three sample items from `fixtures/inventory/demo.json`, under their
  own `user_id`. Sample item GLB urls still point at `/fixtures/blobs/inventory/…`.
- The hand-placed layouts in `fixtures/layouts/` still resolve by id for user `demo`, but don't
  appear in `GET /layouts` lists.
- A layout request takes about 20 seconds end to end with the real agent (two LLM round trips
  and the solver). `agent.action` chips arrive along the way: "Asking the designer",
  "Showing layouts".
- Not built: `POST /uploads`, `POST /objects/ingest`, `job.progress`. Adding furniture from a photo
  is the next piece.
