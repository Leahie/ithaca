---
from: C
to: B
date: 2026-11-14
status: done
source: stream-c/twin-v0 @ 0ae4b16
resolved: stream-b/agent-v0 (Events sound.placeSource / sound.clearSource and ViewerState.sound_source as proposed. Agent tools sound_heard_from (wraps sound_field, source at the room's middle) and sound_place_source (emits the event + viewer.setMode sound).)
---
# Event and tool for "place a sound in the house"

**Why:** The demo: the phone places a sound (preset or the user's recording) on the floor plan,
and the desktop plays what it sounds like in whichever room the tour is in. C computes the
acoustics (`twin.service.sound_field`); the event schema is yours.

## What you need to do
- [x] Add events (proposed shape; rename freely):
      `sound.placeSource` `{room_id: str, x: float, z: float, preset_id: str, level_dba?: float,
      upload_id?: str}` sent by phone, desktop or agent; `sound.clearSource` `{}`.
- [x] Add `sound_source` (the same payload, or null) to `ViewerState`, so a joining client gets it.
- [x] Optional agent tools: a domain tool wrapping `service.sound_field(pid, room_id, x, z,
      preset_id, level_dba, doors, include_furniture, listener)`, which returns `SoundField`, for
      "how loud is a crying baby in the nursery from the master bedroom?"; and a UI tool
      `sound_place_source` that emits the event.

## Details
- `service.sound_presets()` lists the presets. `service.acoustics().loudness` holds the loudness
  choices for a user's own recording (use `preset_id="recording"` with `level_dba`).
- For the agent, `rooms[]` is every room sorted loudest first, with `laeq_dba`. `doors` is
  `"closed"` or `"open"` (hinged doors only).
- Uploads: A records the clip on the phone and uploads it through your uploads route. C only
  needs the loudness, never the audio.
