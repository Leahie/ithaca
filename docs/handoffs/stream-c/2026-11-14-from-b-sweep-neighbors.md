---
from: B
to: C
date: 2026-11-14
status: open
source: stream-b/agent-v0
---
# Request: sweep neighbors in the twin, for walking routes

**Why:** the agent can now walk the camera between rooms (`services/api/agent/views.py`). It
builds the route by guessing which sweeps connect, from distance, room outlines and doors. Matterport
already knows which sweeps connect.

## What you need to do
- [ ] **Optional: add `Sweep.neighbor_ids: list[str] | None`** (additive) from Matterport's own
      sweep graph, if the Model API exposes it. B will use it in place of the guess.
- [ ] **Optional: flag sweeps the Showcase doesn't have.** A found 6 twin sweeps (labels 27, 56,
      57, 63, 81, 87) that the viewer can't move to. A `Sweep.enabled: bool` would let routes skip them.

## Details
- Today two rooms are unreachable in the guessed graph: the Floor 1 12.4 m² bedroom
  (`a5f9age4zzxewres6iqux2emc`) and its 6.2 m² bathroom. Their only doors lead to each other.
- Steps between adjoining rooms with no recorded door are allowed at 3x cost and reported to the
  user as approximate. Missing interior doors in the twin show up as these.
