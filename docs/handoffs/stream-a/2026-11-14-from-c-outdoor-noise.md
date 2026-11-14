---
from: C
to: A
date: 2026-11-14
status: open
source: stream-c/twin-v0 (outdoor noise)
---
# Demo: hear traffic and outdoor noise in every room, by hour, windows open or closed

**Why:** Next to the placed sound, the Sound tab plays what the outside sounds like inside. That
covers the real roads around the house (TxDOT counts where they exist), the rail line and
neighbourhood background. It plays at the hour on the Sun Lab clock, in whichever room the tour
is in. The windows open/closed toggle is the clearest difference to hear (about 25 dB).
C computes it (`sim/traffic.py` outside, `sim/noise.py` inside); A plays and draws it.

## What you need to do
- [ ] **Traffic layer:** loop a street-traffic recording (a steady roadside clip, recorded at the
      curb; yours to pick). Per room: gain so the playback level is `laeq_dba` (A4, with the
      Sound tab's `fullScale`); 8 band filters set to `bands_db - outdoor_bands_db` (the house's
      filtering of the outdoor spectrum); convolver from `rt60_s`.
- [ ] **Walk around:** fetch `GET .../noise/house?hour=H` with the toggles. On a sweep change,
      switch to that room's entry in `rooms[]` with a crossfade of about 300 ms. Refetch when the
      hour or a toggle changes; it is cached and fast.
- [ ] **Mix with a placed sound:** play both. The traffic layer is the bed under the
      `sound/field` source.
- [ ] **Toggles:** `windows_open`, `glazing` (single, double, triple; default as built),
      `add_carpet`, `include_furniture`. Hour slider: share the Sun Lab time.
- [ ] **Chart (M5):** `GET .../noise?room_id=` gives 24 `hourly` levels inside and
      `outdoor_hourly` outside the room's most exposed wall. Show `laeq_day_dba`, `laeq_night_dba`,
      `outdoor_lden_dba` and `outdoor_lnight_dba`, with the WHO lines (bedroom at night 30 dBA,
      living room by day 35, outside Lden 53, outside Lnight 45). List `sources[]` (what it's made
      of) and `caveats[]`.
- [ ] **Map (optional):** `GET .../noise/sources` gives each road and railway's `path` as lat/lon
      polylines, with `aadt`, `speed_kmh` and `facade_laeq_dba`. Colour the roads on the context
      map by `facade_laeq_dba`.

## Endpoints (under `/api/properties/{pid}`)
| Route | Returns |
|---|---|
| `GET /noise?room_id[&glazing][&add_carpet][&include_furniture][&windows_open]` | `NoiseReport` |
| `GET /noise/house?hour=0..23[&same toggles]` | `NoiseHouse`: `rooms[]` loudest first, each with `laeq_dba`, `bands_db`, `outdoor_laeq_dba`, `outdoor_bands_db`, `rt60_s` |
| `GET /noise/sources` | `NoiseSources`: roads, rail and background, loudest at the house first |

Hours are property-local. Day means 07-23 and night 23-07. Errors: 404 unknown room, 422 bad hour
or glazing. The first call per property builds the outdoor model (about 4 s, also warmed by
`GET /twin`); after that, about 20 ms.

## Details
- **This house at 5 pm:** 52 to 56 dBA outside the walls. Inside with windows closed: 13 to 20 dBA
  in the main rooms, which is barely audible (the AC would be louder). With windows open: 36 to
  46 dBA. At 3 am it is about 9 dB quieter. Run `python -m services.api.twin.noise_cli onXQLXhfyQv`
  for the tables and `out/twin/noise_{site,floors_17h,day}.png`.
- **Low levels are real:** play them at their true level and say "barely audible". Don't boost
  them (A4). The contrast between windows open and closed is the demo.
- Rooms with no outside wall (hallways, closets) get `outdoor_laeq_dba = 0` and are heard through
  the rest of the house.
- `traffic` is `"aadt"` when any road used a count. The `caveats` say which roads used counts
  and what is assumed (rail train counts, background category, missing OpenStreetMap buildings).
