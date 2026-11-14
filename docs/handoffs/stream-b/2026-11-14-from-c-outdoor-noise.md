---
from: C
to: B
date: 2026-11-14
status: open
source: stream-c/twin-v0 (outdoor noise)
---
# `noise_report` is live; scenario gains `windows_open`

**Why:** WORKSTREAMS step 5 (the `noise_report` tool and `sound_set_scenario`). The noise engine
now models traffic (real roads, TxDOT counts), rail and background outside, and what reaches each
room. "How loud is the bedroom at night with the windows open?" has a real answer.

## What you need to do
- [ ] **`noise_report` tool:** wrap `service.noise_report(pid, room_id, NoiseScenario(...))`, which
      returns `NoiseReport`. For the agent: `laeq_day_dba` and `laeq_night_dba` inside,
      `outdoor_lden_dba` and `outdoor_lnight_dba` outside, `sources[]` (loudest first, with
      `contribution_dba` inside and `outdoor_dba`) and `caveats[]` (say them). `hourly[h]` gives
      one hour.
- [ ] **Optional tools:** `service.noise_house(pid, hour, scenario)` gives every room at one hour
      ("which bedroom is quietest at 7 am?"). `service.noise_sources(pid)` gives the roads with
      traffic counts ("what's that hum?" leads to the freeway).
- [ ] **`sound.setScenario` event:** `NoiseScenario` now has `windows_open: bool = False` next to
      `glazing`, `add_carpet` and `include_furniture`. The change is additive, so you can type the
      event's `scenario` as `packages.schema.reports.NoiseScenario`.

## Details
- Errors: unknown room raises `KeyError`. There is no 503 any more: if road data is missing, the
  report still comes back (background only) and the `caveats` say so.
- WHO guidance for the agent to compare against is in
  `fixtures/tables/traffic.json` under `guidelines`: bedroom at night 30 dBA and living room by
  day 35 inside; Lden 53 and Lnight 45 outside.
- `NoiseReport.traffic` (`aadt`, `class_default` or `none`) is the per-report version of
  `twin.quality.traffic`. Ingestion doesn't set the twin field yet.
