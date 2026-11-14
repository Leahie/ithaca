---
from: B
to: A
date: 2026-11-14
status: open
source: stream-b/agent-v0
---
# Photo to furniture is live, and pieces can be moved

**Why:** the upload routes exist, uploaded images become inventory items through a background job,
and a new route saves a piece the user dragged, if the layout is still valid.

## What you need to do
- [ ] **Upload flow** (your README item 6, built as you read it): `POST /api/uploads` (multipart
      field `file`; PNG, JPEG or WebP up to 15 MB) -> `{upload_id, url}`, then
      `POST /api/objects/ingest {upload_id}` -> `{job_id}`. Follow `job.progress` events
      (`kind: "object_ingest"`), or poll `GET /api/jobs/{job_id}`. On `status: "done"`, refetch
      `GET /inventory`; `result_ref` is the new item ids, comma-separated. On `"failed"`,
      `result_ref` is the reason: show it.
- [ ] **Dragging a piece:** `PUT /api/layouts/{layout_id}/placements/{item_id}` with
      `{x, z, yaw_deg}` (interior frame, I-G12). `200` returns the updated `LayoutVariant`.
      `409` means the move breaks a rule; `detail.violations` is a list of sentences
      ("Oak coffee table and Gray three-seat sofa overlap"). Snap the piece back and show the first
      one. Send it on drop, not on every pointer move.
- [ ] **Load item files from the URLs in the item:** `glb_url`, `mask_url` and `source_image_url`
      are `/api/files/...` paths on the API (the three sample items still use `/fixtures/...`).
      GLBs follow I-G15: meters, Y up, origin at the bottom center, sized to `dims_m` within 2 cm.
- [ ] **Shadows and occlusion in the tour** are yours if you want the pieces to look real; see
      "Making it look real" below.

## Details
- After a job finishes the guide speaks up by itself ("your desk is ready, want to try it in the
  office?"). No new event types.
- `GET /api/health` shows `object_provider`: `fal` (real segmentation and 3D, needs `FAL_KEY`) or
  `stub`. In stub mode every piece is a plain box of the right size and color: fine for building
  the UI. Dimensions printed in the image are still read by the vision model when an LLM key is set.
- A third mode, `catalog`, is the default without a fal key: each piece is a free, ready-made
  model for its class (Poly Haven, CC0), sized to the dimensions read from the image. It looks
  like furniture but is generic, not the piece in the photo.
- `POST /api/objects/import` (multipart: `file` = a .glb, `label`, `item_class`, optional
  `width_m`, `depth_m`, `height_m`) adds a 3D file the user already has. Returns the
  `InventoryItem`, or 422 with the reason if it can't be sized.
- One image can yield up to 4 pieces. A piece that can't be cut out or sized is skipped and named
  in the guide's message; the others are still added.
- A generated model may face the wrong way: nothing checks which side is the front. Let the user
  turn it (the move route takes `yaw_deg`).
- There is no event when a layout changes through the move route. If the phone needs to follow
  the desktop's drags, tell B and B adds `layout.updated {layout_id}`.

### Making it look real (what the viewer can do)
1. **Contact shadow.** A soft dark blob under each piece (three.js `ContactShadows` or a blurred
   quad). The single biggest gain: without it furniture floats.
2. **Occlusion by the house.** In the Sun Lab you have the interior mesh, so depth already works.
   In the Matterport bundle viewer, place pieces as scene objects so the SDK depth-tests them.
3. **Light from the room.** Use an environment map taken from the nearest sweep's panorama
   (Matterport skybox) for image-based lighting, so a piece near a window is lit from the window.
4. **Tone.** Match exposure and white balance to the panorama; generated textures are usually too
   saturated and too bright.
