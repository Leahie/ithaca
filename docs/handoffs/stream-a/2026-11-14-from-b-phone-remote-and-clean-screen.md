---
from: B
to: A
date: 2026-11-14
status: open
source: main @ 77cbf98
---
# Phone remote reshaped, clean big screen, own floor plan (changes in your files)

**Why:** the team's sketches make the phone the only control and the laptop a window onto the
house. These touch Stream A files, so read before you edit them.

## What changed
- `components/remote/RemoteController.tsx`: five screens (Move, Rooms, Guide, Furniture, Light) in
  place of the mode tabs. Hold-to-talk is on every screen. The phone no longer switches the big
  screen's mode, except "Walkthrough / Sunlight" on the Light screen.
- `components/house/HouseExperience.tsx`: while `peers.phone` is true (tour or sun mode), the top
  bar, `RoomRail` and `AgentDock` are hidden and `CleanOverlay` shows the room, time and the
  guide's reply as a caption. "Controls" brings them back.
- `components/house/HousePlan.tsx` (new): the app's floor plan from the twin (`PlanSvg`,
  `FloorTabs`, `useFloor`), in the tour and on the phone. Matterport's own floor-plan button is
  off (`fp: '0'` in `lib/showcase/viewer.ts`) because its labels cannot be corrected.
- Events, additive, phone only: `viewer.step {direction}` (handled in `TourTab`) and
  `remote.view {view}` (stored as `remoteView` in the session store).
- `lib/config.ts` + `next.config.ts`: the session socket uses the page's own origin; Next proxies
  `/ws` to the API. `scripts/phone-tunnel.sh` gives a phone an HTTPS address.
- `lib/showcase/rooms.ts`: `floorSweeps` keeps "go to a room" off staircases.

## What you need to do
- [ ] Try it on a real phone and in the bundle viewer; none of this has been seen in a browser.
- [ ] Sun mode ignores `viewer.step` and `viewer.look` from the arrows: decide if the Sun Lab
      camera should follow them.
