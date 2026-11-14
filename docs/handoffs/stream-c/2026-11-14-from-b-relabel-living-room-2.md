---
from: B
to: C
date: 2026-11-14
status: open
source: fix/layout-solver-sense
---
# Relabel one room: "Living Room" `tt6gm2a0463x9wmnx7rn6gugc` is a bedroom

**Why:** the user toured the house and says the room the app calls "Living Room 2" is a bedroom,
not a living room. The label comes from Matterport's own room labelling, which the twin copies.

## What you need to do
- [ ] **Change the label of `tt6gm2a0463x9wmnx7rn6gugc` to `Bedroom`** in the twin for
      `onXQLXhfyQv` (Floor 1, 34.4 m², one floor-to-ceiling window on the west wall, doors to
      Bathroom 1, to the hallway and to Living Room 1). The user says it is not the primary
      bedroom, so plain `Bedroom`.
- [ ] **Keep the `room_id`.** Saved layouts and the user's memory files refer to it (I-G17).
- [ ] **Make it a correction that survives re-ingestion,** not an edit to the fixture JSON alone:
      for example a per-property overrides file that ingestion applies after reading Matterport's
      labels, next to how opening annotations are kept. Other labels may need correcting too
      (see below).
- [ ] **Tell A and B when it lands.** The app and the agent number repeated labels from the ground
      floor up and from the largest room down, so this renumbers rooms: this room becomes
      "Bedroom 1" (it is the largest bedroom on Floor 1), the current Bedroom 1 to 6 each move up
      by one, and "Living Room 3" (upstairs) becomes "Living Room 2". Names in chat history and in
      memory notes written before the change will be off by one.

## Details
- The same glass was also recorded as a `window` with its sill 2 cm above the floor (opening on the
  west wall, 1.43 m wide, top at 2.42 m). If Matterport has it as a door or slider, `kind:
  "slider"` would be more accurate. B's layout solver already treats glass that reaches the floor
  as a door, so nothing breaks either way.
- Other labels worth a look while you are there: the Floor 2 "Living Room" (84.9 m², the landing
  and loft over the stairs, as your first handoff says) and the 0.9 m² "Other".
- Until this lands, the agent accepts the user's word: if they say what a room really is, it
  records that in their notes for the house and uses their name for it.
