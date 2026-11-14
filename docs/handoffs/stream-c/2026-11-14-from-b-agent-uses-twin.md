---
from: B
to: C
date: 2026-11-14
status: done
source: stream-b/agent-v0 @ 850c835
resolved: stream-c/twin-v0 @ 338958f (reply: docs/handoffs/stream-b/2026-11-14-from-c-sun-daylight-and-exterior.md)
---
# What the agent relies on in the twin, and three requests

**Why:** the agent graph (`services/api/agent/`) is built on `stream-c/twin-v0`. It calls
`get_twin()` on every model call and builds its room directory and tools from the twin.

## What you need to do
- [x] **Add `trimesh` to `services/api/twin/requirements.txt`.** In a fresh venv,
      `test_mesh.py` and `test_ingest_replay.py` fail at import (`No module named 'trimesh'`), which
      stops `pytest services` for everyone.
- [x] **Say whether a door leads outside.** Today an `Opening` with `kind: "door"` and
      `other_room_id: null` can be an exterior door or a door into unlabeled space, so the agent
      can't say "this door opens to the yard". Request: an optional `Opening.exterior: bool | None`
      (additive, I-G6). Until then the agent calls these "outside or unlabeled space".
- [x] **Keep these stable, B imports them:** `services.api.twin.service.get_twin`,
      `services.api.integrations.config.{env, IntegrationNotConfigured, REPO_ROOT}`. Tell B before
      renaming or moving them.
- [x] **For the section 2.5 functions that don't exist yet** (`sun_report`, `noise_report`,
      `area_profile`, `area_search`, `get_comps`): when the data isn't available, raise
      `IntegrationNotConfigured` (I-G21). B's tools turn that into "I can't measure that yet".

## Details

### What the agent reads from `PropertyTwin`
- `rooms[].label`, `floor_id`, `area_m2`, `floor_material`, `polygon`, `height_m`, `floor_y`
- `openings[].kind`, `room_id`, `other_room_id`, `polygon` (corner order bottom-start, bottom-end,
  top-end, top-start, as in your handoff), `facade_azimuth_deg`, `glazing`
- `floors[].label` / `index`, `address`, `timezone`, `quality`

`quality` becomes caveats in the agent's prompt (C3): `openings: detected`, `context: none`,
`traffic: none | class_default`, `alignment: gps_only` each add one sentence. If you add a value,
tell B so the agent can explain it.

### Performance
`get_twin()` takes about 0.3 ms for this house, which is fine to call per model call. If it ever
does I/O beyond reading the fixture (database, network), cache it by `property_id` + `version`.

### Environment
B's tests run in a Python 3.12 venv (`.venv`, `uv`). B's dependencies are in
`services/api/requirements-b.txt`. B added its keys to `.env.example`
(`LLM_PROVIDER`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, `AGENT_MODEL`).
