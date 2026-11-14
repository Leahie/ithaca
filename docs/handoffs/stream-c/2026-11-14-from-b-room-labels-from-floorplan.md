---
from: B
to: C
date: 2026-11-14
status: open
source: main
---
# Room labels corrected from the published floor plans (twin version 9)

**Why:** Matterport's own labels were wrong for six rooms. They are now corrected from the floor
plans, in `fixtures/twin/annotations/onXQLXhfyQv.rooms.json` (room_id -> label). Ingestion applies
that file after reading Matterport's labels, so re-ingesting keeps the corrections. Room ids,
outlines and floor materials are unchanged.

| Floor | Was | Now |
|---|---|---|
| 1 | Living Room 2 (34.4 m²) | Bedroom 1 |
| 1 | Living Room 1 | Living Room |
| 1 | Bedroom 1 (14.2 m², by the entry) | Office 2 |
| 1 | Bedroom 2 (12.4 m², in the back wing) | Office 3 |
| 1 | Office | Office 1 |
| 1 | Laundry 2 (4.7 m², inside the kitchen) | Pantry |
| 1 | Laundry 1 | Laundry |
| 1 | Hallway 1 (3.4 m², inside the primary closet) | Closet 1 |
| 2 | Living Room 3 | Family Room |
| 2 | Hallway 2 | Hallway |
| 2 | Bedroom 3 to 6 | Bedroom 2 to 5 |
| 2 | Closet 1, 2 | Closet 2, 3 |

**Update, same day (version 9):** the rooms file now has two parts, `labels` and `carve`. A carve
cuts a piece out of a room Matterport drew too large. Three so far: a missed Bathroom (4.9 m², by
the east stair, id `carve-f1-bathroom`) and the Dining Room (20.9 m², id `carve-f1-dining`), both
out of the Floor 1 Living Room, and the west stairwell out of the Floor 2 Family Room (it becomes
no room). Scan positions, openings and walls on a carved piece move to the new room. So Floor 1 now
has Bathroom 1 to 3 and Floor 2 has Bathroom 4 to 6. Going to a room also no longer picks a scan
position on a staircase (`floorSweeps` in `apps/web/src/lib/showcase/rooms.ts`, `best_view` in
`services/api/agent/views.py`).

## What you need to do
- [ ] Nothing has to change if you look rooms up by `room_id`. Check anything that looks a room up
      by label or by its numbered name (saved text, defaults such as `--room "Floor 1/Bedroom"`,
      `fittings.detect(room_labels=...)`): Floor 1 now has one Bedroom, not two.
- [ ] To correct another label, add its room_id to the rooms file and re-ingest.

## Not done
- The plan's Entry is still part of Living Room and Storage is missing from the scan data. Floor materials still
  follow the old labels (the new Bedroom 1 is hardwood, the offices carpet).
- Chat history and memory notes written before today may use the old names.
