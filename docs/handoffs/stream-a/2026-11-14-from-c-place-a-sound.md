---
from: C
to: A
date: 2026-11-14
status: open
source: stream-c/twin-v0 @ 0ae4b16
---
# Demo: place a sound in the house, walk around and hear it

**Why:** The user places a sound on the floor plan from the phone (a TV, a crying baby, or their
own recording), then walks through the tour and hears how loud and muffled it is in each room.
C computes it from the real walls, doors and stairwell (`sim/noise.py`, statistical room acoustics);
A plays it.

## What you need to do
- [ ] **Phone:** tap the floor plan to place the source. You need `room_id` (point in
      `room.polygon`) and interior `(x, z)`. Pick a preset from `GET .../sound/presets`, or record
      a clip (MediaRecorder, upload through B's uploads) and pick its loudness from
      `loudness_dba` (`preset=recording&level_dba=<choice>`). Send B's `sound.placeSource`
      event (requested from B), so the desktop and agent see it.
- [ ] **Desktop:** fetch `GET .../sound/field` once per placement (and per doors toggle). As the
      tour moves, take the current sweep's `room_id` (`twin.sweeps`) and switch to that room's
      entry in `rooms[]`. Crossfade over about 300 ms.
- [ ] **Audio graph:** clip, then 8 band filters (lowshelf at 63 Hz, peaking at 125 to 4000 Hz
      with Q about 1.4, highshelf at 8 kHz) set to that room's `gain_db`, then dry and wet paths
      (wet = convolver with a synthetic exponential-decay IR of the room's `rt60_s`), then output.
      Another room: mostly wet. Source room: call again with `listener_room_id/x/z` = the sweep's
      position and use `listener.gain_db` and `listener.direct_fraction` as the dry share.
- [ ] **Toggle:** doors `closed` / `open` (hinged doors only; passages and the stairwell are
      always open).

## Endpoints (under `/api/properties/{pid}`)
| Route | Returns |
|---|---|
| `GET /sound/presets` | `SoundCatalog`: `presets[]` (`preset_id`, `label`, `level_dba` at 1 m, `bands_db`), `loudness_dba` choices |
| `GET /sound/field?room_id&x&z[&preset=tv][&level_dba][&doors=closed][&include_furniture=true][&listener_room_id&listener_x&listener_z]` | `SoundField` |

`SoundField.rooms[]` lists every room, loudest first: `laeq_dba`, `bands_db`, `gain_db` (band
level minus the source's level at 1 m), `rt60_s`, `rt60_bands_s`. Bands are the I-G14 eight.
Errors: 404 unknown room or preset, 422 point outside the room (or a partial listener).
After the first call, about 20 ms, cached per twin version.

## Details
- **Calibrating loudness:** treat the clip as recorded about 1 m from the source. Gains are
  relative to that, so one playback calibration (the Sound tab's `fullScale`) works for every
  room.
- **Typical numbers for this house:** a TV at 70 dBA in the big living room is 60 to 64 dBA in the
  open kitchen and office, 54 dBA upstairs through the stairwell, 46 dBA behind the bathroom
  door, and 21 to 31 dBA in the bedrooms. Run `python -m services.api.twin.sound_cli onXQLXhfyQv`
  for the table and per-floor maps (`out/twin/sound_*.png`).
- **The far-west bedroom and bathroom are a separate building:** they show as silent (0 to 7 dBA).
- **Twin is now v5:** `walls[]` (centerlines with `exterior` and `assembly`), `floor_openings[]`
  (stairwells), `Opening.leaf` (true = hinged door, false = open passage). Walls are handy for
  drawing the floor plan.
- Wall and door materials are inferred from Matterport wall thickness (`quality.walls =
  "inferred"`). Values are in `fixtures/tables/acoustics.json`.
