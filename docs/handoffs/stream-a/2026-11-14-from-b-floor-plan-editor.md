---
from: B
to: A
date: 2026-11-14
status: open
source: fix/layout-solver-sense
---
# A floor-plan editor for placing furniture by hand

**Why:** describing a spot in words is slow and the 3D views are hard to drag in. The user asked
to place furniture by drawing on the floor plan: each piece is a rectangle you drag and turn.
The server side is ready.

## What you need to do
- [ ] **A top-down plan of the current room** (start from `PlanPreview`): the room outline, doors
      and windows, and one rectangle per placed piece, `dims_m.w` by `dims_m.d`, turned by
      `yaw_deg` (I-G12; mark the front edge so the user can see which way it faces).
- [ ] **Drag to move, a handle to turn.** While dragging, move the rectangle freely and follow the
      cursor with a short ease. On drop call
      `PUT /api/layouts/{layout_id}/placements/{item_id}` with `{x, z, yaw_deg}`. `200`: keep it
      (use the returned placement; the server sets `y`). `409`: ease the piece back and show
      `detail.violations[0]`.
- [ ] **Add a piece from the inventory by dropping it on the plan:** `POST /api/layouts/drop` with
      `{property_id, item_id, x, z, layout_id}` (or `floor_id` with no layout to start one). The
      server moves it to the nearest valid spot and backs it against the wall if the drop is by
      one, so the piece may land a little away from the cursor: animate it there.
- [ ] **Show what must stay clear,** so rejections aren't a surprise: shade the floor in front of
      each door and each floor-to-ceiling window (0.9 m deep).
- [ ] **Keep the 3D view in step.** After a successful move, update the same layout in the Tour
      and Sun Lab from the returned `LayoutVariant`.

## Details
- Rules the server enforces on a move or drop: inside the room, no overlap with another piece
  (5 cm gap), clear floor in front of doors and of glass that reaches the floor, tall pieces not in
  front of windows, and a 0.9 m walking path between the ways in. Rugs may lie under other pieces.
- For instant feedback you can pre-check overlap in the browser (two rotated rectangles: the
  separating-axis test), but the server's answer is the one that counts.
- No event is sent when a layout changes this way. If the phone should follow the desktop's edits,
  ask B for `layout.updated {layout_id}`.
- The solver was changed at the same time: glass down to the floor counts as a door, walking routes
  come from the scan's own path network (so open-plan passages are respected), and a piece may not
  stand alone in the open. Agent-made layouts should look more sensible; tell B where they don't.
