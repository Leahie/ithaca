---
from: B
to: A
date: 2026-11-14
status: open
source: stream-b/agent-v0
---
# Layout fixes from a live test: no tab switch, a floor-plan pin, shared room names

**Why:** the user tried furniture placement in the app. Four things felt wrong: being thrown into
the Design tab, a sofa sunk into the floor, the agent picking the wrong "Living Room 2", and no way
to point at a spot. B fixed its half of each.

## What you need to do
- [ ] **Don't switch tabs on `design.showVariants`.** The agent no longer sends
      `viewer.setMode design` with it. It now sends, in this order: `design.showVariants`
      (the options), `layout.select {layout_id}` with `source: "agent"` (option 1, to place in the
      current view), `viewer.goto` (the room). Stay in the view the user is in, place the selected
      layout's furniture there, and show the options as a small strip or badge ("1 of 3") rather
      than a page change. "Next option" from the user comes back as another agent `layout.select`.
- [ ] **A pin on the floor plan.** Let the user pick a piece and tap a spot on the plan, then call
      `POST /api/layouts/drop` with `{property_id, item_id, x, z, floor_id}` (interior frame; add
      `layout_id` to put it into the layout on screen, or `room_id` if you know the room). The
      server finds the room, moves the piece to the nearest spot that breaks no rule (backed
      against the wall if the pin is by one) and returns the `LayoutVariant`. `409` with
      `detail.violations` means nothing fits within 2 m of the pin.
- [ ] **Keep your room names as they are.** The agent now uses the same names as
      `src/lib/rooms.ts` ("Living Room 2", "Bedroom 3"), plus floor and size. If you change that
      naming rule, tell B: `services/api/agent/prompts.py` `room_short_names` mirrors it.
- [ ] **Place furniture at `Placement.position.y` as given.** It is no longer `room.floor_y`; see
      the floor note below. Nothing to change if you already use the stored pose (A5).

## Details
- **Sunk furniture.** `Room.floor_y` is 0.3-0.4 m too low in the big Floor 1 rooms of this house
  (Living Room 1, Kitchen 1, Office, Bathroom 1, Bedroom 1). B now takes the floor from the scan
  camera heights in the room and writes that into `position.y`. Layouts saved earlier were
  corrected in place. If your Sun Lab or render page draws a floor from `room.floor_y`, furniture
  will now float above that drawn floor in those rooms; C has a note about the twin value.
- **Layouts that block the way through.** The solver now keeps the straight lines between a
  room's doors clear, puts large pieces on walls by default, and sends the largest piece to a
  different wall for each option. It returns fewer than 3 options when the room has fewer good
  walls, with the reason in the agent's message.
- B fixed one type error in `apps/web/scripts/fixtures/build.ts` (`windows_open` is now required in
  C's `NoiseScenario`), so `pnpm typecheck` passes on the merged branch.
