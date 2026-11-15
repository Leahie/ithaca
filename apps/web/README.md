# Ithaca web (Stream A: Web & Phone)

Next.js app for [PLAN.md](../../PLAN.md) sections 7 and 8, built to the contracts in
[WORKSTREAMS.md](../../WORKSTREAMS.md).

| Route | What it is |
|---|---|
| `/` | Front page: what Ithaca is, the home that is open for a visit (from its twin and listing), and the way in |
| `/house/[id]` | The home: Tour (with its sun and shadows), Sound, Neighborhood, Design, the guide's chat dock, family phones, hand control |
| `/join/[token]` | A family member's phone: scan the family code, type a name, get a remote and a guide of your own |
| `/remote/[session]` | Phone controller for one phone (QR pairing) |
| `/me` | Furniture inventory, memory files, per-home decision history |
| `/render/layout/[id]` | Deterministic render for the thumbnail job (section 2.6) |

## Run it

```sh
pnpm install                       # also copies MediaPipe's wasm to public/mediapipe
git lfs pull                       # fixtures/blobs is in Git LFS (the rebuilt house is 19 MB)
cp .env.example apps/web/.env.local
pnpm dev                           # http://localhost:3000
```

With `NEXT_PUBLIC_USE_FIXTURES=1` every tab runs from `fixtures/` with no backend (A6). The
agent is a scripted stand-in, and the desktop and phone talk over a `BroadcastChannel`, so
pairing works between two tabs of one browser. Remove the flag to use the real API
(`API_PROXY_TARGET`, default `http://localhost:8000`) and the WebSocket hub.

A real phone needs: your LAN address in `NEXT_PUBLIC_PUBLIC_ORIGIN` and `DEV_ALLOWED_ORIGINS`,
the real hub (not fixture mode), and HTTPS for the microphone and the gyroscope
(`next dev --experimental-https`).

## Checks

```sh
pnpm typecheck
pnpm test          # one unit test: the sun direction stays within 0.5 degrees of the pvlib reference
pnpm schema:check  # generated TS types are up to date (needs uv)
```

The E2E suite and the other unit tests were removed to keep work fast.

## Mock phone (for Stream B)

Pairs with a session on the real hub and sends any v0 event.

```sh
pnpm --filter @ithaca/web mock-phone new
pnpm --filter @ithaca/web mock-phone --session <id> --token <pair_token> send viewer.setMode '{"mode":"sound"}'
pnpm --filter @ithaca/web mock-phone --session <id> --token <pair_token> listen
pnpm --filter @ithaca/web mock-phone --session <id> --token <pair_token> repl
```

Options: `--api http://localhost:8000`, `--ws`, `--user`, `--client phone|desktop`, `--wait <ms>`,
`--no-join`, `--force` (send a type that is not in schema v0, to test I-G7).

## How the invariants are held

| Invariant | Where |
|---|---|
| A1 one store, two inputs | `src/lib/store/reducer.ts` is the only place viewer state changes. `dispatch` (a click) and `receive` (an event) both call it. |
| A2 phone is a pure controller | The phone uses the same store and rebuilds from `session.state`. The desktop persists `layout.select`. |
| A3 show numbers, do not compute them | Sun hours, dBA and RT60 are read from reports. The one computed value is the sun direction (`src/lib/sun/position.ts`), held within 0.5° of `fixtures/sun/reference.json` by `position.test.ts` (worst case today: 0.05°). |
| A4 audio from `NoiseReport` only | `src/lib/audio/auralizer.ts`: gain from `laeq_dba`, band filters from `bands_db`, impulse response from `rt60_s`. The free values are named in `AURALIZATION`. |
| A5 frame-free placement | `Placement.position` and `yaw_deg` are used as stored in the Tour, the plan preview and the render page. |
| A6 every tab works offline | `src/lib/api/fixture.ts`, `src/lib/realtime/local.ts`, the `/fixtures/[...path]` route. |
| A7 one typed client | `src/lib/api/types.ts` (`ApiClient`), with HTTP and fixture implementations. Types come from `@ithaca/schema`. Unknown events are logged and dropped in `store/session.ts`. |
| A8 streaming agent output | Tokens, action chips and errors: `components/house/AgentDock.tsx`. |
| A9 render determinism | `components/render/LayoutRender.tsx`: fixed camera and light, frames on demand. |

## Decisions and contract gaps to confirm

These came up while building against schema v0. Each is the choice this app makes today.

**For Stream B**

1. The render page needs one layout by id. REST v0 has no such route; the client calls
   `GET /layouts/{id}`.
2. Pairing. The desktop opens the WebSocket with `token=<pair_token>` from `POST /sessions`.
   The phone calls `POST /sessions/{id}/join {pair_token}`, then opens the WebSocket with the
   same token. "Single-use" (B10) is read as: one successful join.
3. Presence. A client that joins late cannot know who is already there. The app treats any
   event from a client as proof of presence. Sending `session.joined` for the clients already
   in the session, on join, would make it exact.
4. `GET /memory` is read as `string[]` of paths under `/memories/`. File bodies are `text/plain`.
5. `POST /layouts/{id}/feedback` body is `{feedback}`. The same text goes to the agent as `chat.user`.
6. Upload flow: `POST /uploads`, then `POST /objects/ingest`, then the client emits
   `capture.upload`. Progress is read from `job.progress`; `status: "done"` refetches inventory
   and layouts.
7. `viewer.look`: `yaw_delta_deg > 0` looks right, `pitch_delta_deg > 0` looks up.
8. `voice.transcript` with `final: true` starts an agent turn. Interim transcripts go out at
   most 4 times a second. An empty final transcript means the user cancelled.
9. `session.state` has no chosen layout. The app restores it from `LayoutVariant.status == "chosen"`.
10. `Vec3` is `{x, y, z}`.
11. `GET /noise` also gets `include_furniture` (the scenario has it; the route table does not).

**For Stream C** (reply to `docs/stream-a-handoff.md`, branch `stream-c/twin-v0`, merged here)

What C asked A for (handoff section 6):

1. **Frame check (C6): passed** (while the Tour was the Matterport Showcase; it now draws the
   rebuilt house in the interior frame itself, so there is nothing to compare). The Tour tab
   compared `mpSdk.Sweep.data` with `twin.sweeps` at runtime. For twin v2 of this model: the Showcase `sid` equals
   `sweep_id`, 90 of the 96 twin sweeps are in the Showcase, and all 90 positions agree within
   1 mm. I-G9 holds; furniture poses can be used as they are.
   The other 6 twin sweeps (labels 27, 56, 57, 63, 81, 87) are not in the Showcase sweep list,
   and 5 of them have a `room_id`. `Sweep.moveTo` cannot go to them. If one is the best sweep
   for a room, the Tour falls back to the nearest Showcase sweep.
2. **The meshopt GLB loads** with drei's `useGLTF` and no extra setup (about 15 s under software
   WebGL in CI). `fixtures.test.ts` checks that it lies in the interior frame around the rooms.
   The materials have `baseColorFactor` 0.4, which darkens the photo textures.
3. **Annotation UI**: gone with the Sun Lab page, which held it. The API still takes a full
   `Opening[]` with `PUT /openings`.

How the web app uses the twin:

- **Asset keys.** In fixture mode a path such as `blobs/<pid>/interior.glb` becomes
  `/fixtures/blobs/<pid>/interior.glb`. A value that is already a URL is used as it is.
- **Repeated room labels.** `src/lib/rooms.ts` names rooms for display: a label used once stays
  ("Office"); a repeated one is numbered from the ground floor up and from the largest room down
  ("Bedroom 1" .. "Bedroom 6"). The room list also shows the area. Everything keys on `room_id`.
- **The Tour draws the rebuilt house** (`assets.house`, a `house.json` with `house.glb`, the
  `light_*.png` lightmaps and `context.glb`/`context.jpg` beside it), made by the
  `3d_rendering_matterport` project's `pnpm house all` and copied into
  `fixtures/blobs/<pid>/house/`. The renderer is ported from that project (`src/lib/house/`):
  baked sky and lamp light, the sun added on top with one 4096 px shadow map of the house, its
  lot and the block around it (neighbors' houses, trees), and the sun a sunlit room throws back.
  North for the sun is the one measured on Vexcel's survey (`house.json` `orientation.north`),
  not the twin's `interior_to_world`, which carries Matterport's heading (6.5 degrees off).
  Furniture is lit the same way and casts shadows. `interior_glb` and `context_glb` are no
  longer drawn there.
- **No rebuilt house** (`house: null`): the Tour draws rooms from their outlines on a plain
  ground, and the sun's shadow map lets light through the twin's openings.
- **Sun direction** uses suncalc 2.x, whose azimuth is already in degrees clockwise from north.
  The recipe in the handoff is for suncalc 1.x (radians, from south). `position.test.ts` holds
  the result within 0.5 degrees of `fixtures/sun/reference.json` (today: 0.05). That file is
  written by `scripts/fixtures/sun_reference.py` until C ships its own.
- **Light through openings.** The rebuilt house has real holes and glass. For rooms drawn from
  their outlines, windows, sliders and glazed doors let sun in: the shadow pass skips the walls
  inside each such opening (`src/lib/house/sun.ts`). A plain door blocks light.
- **Sun report shape.** A sun report covers a date range per room; `heatmap.hours[row][col]` has
  rows along +z and columns along +x from `origin_xz`. No view draws it since the Sun Lab page
  was removed; the guide reads the reports through its tools.

Still open for C:

1. `context_glb` is taken to be in the interior frame, like `interior_glb`.
2. The ortho image has no georeference in the schema, so the Neighborhood tab would show it as a
   picture beside a separate distance map. An optional `ortho_bounds` would let them share a map.
3. `services/api/twin/requirements.txt` does not list `trimesh`, which `mesh.py` imports; the
   tests pass once it is installed.
4. A Draco-compressed GLB would need a decoder that drei loads from a CDN. meshopt needs nothing.

## What the house page shows

Everything, by default: all five views, the guide, family phones, hand control and the link to
`/me`. `NEXT_PUBLIC_FEATURES` cuts it down to a comma-separated list of parts (a kiosk, a demo of
one view):

| Value | Part |
|---|---|
| `tour` | The Tour (walk through the rebuilt house) and the room list |
| `sound` | The Sound view, and the sound and time bar in the Tour |
| `neighborhood`, `design` | That view, as a tab |
| `agent` | The guide: the chat dock, and its voice |
| `phone` | QR pairing for one phone controller (used when `family` is off) |
| `me` | The link to `/me` |
| `hands` | Hand control: the **Hands** button in the header |
| `family` | One QR code for a whole family; each phone gets its own agent |
| `all` (default) | Everything |

Example: `NEXT_PUBLIC_FEATURES=tour,sound,agent`.

How the parts share one screen (`src/components/house/HouseExperience.tsx`, `src/lib/store/guide.ts`):

- **Alone, the screen is the whole app**: top bar with the views, room list, chat dock. **With a
  phone connected, the phone is the remote** and the big screen shows only the house (the "clean"
  screen), with the guide speaking. "Controls" on the house and "Hide controls" in the top bar
  switch between the two by hand, and that choice then stays.
- **The guide's voice belongs to the screen**, not to one view: it speaks on every view, and tells
  the phones when it has finished (`voice.spoken`) so they can open their microphone. It is on
  when a phone is connected or the screen is clean, off beside the chat, until the user switches it
  ("Voice on" on the house, "Voice replies" in the dock: one switch).
- **A phone that walks or arranges furniture brings the big screen back to the Tour** from whatever
  view the guide or someone at the screen left open; a room picked on the phone does the same from
  Neighborhood and Design.
- **The Design view follows the furniture rules** (`docs/specs/furniture.md`): "Try it in the room"
  draws a proposal (`layout.select`), "Keep it" makes it the room's furniture (`layout.confirm`).
- **The Sound view also plays a sound placed in the house** (`sound.placeSource`, from the view or
  from the guide): how loud it is in every room, doors open or closed, from `GET .../sound/field`.
- **`/me` joins the house's own session** (`src/components/session/DesktopSession.tsx`), so going
  there and back does not leave the family's phones behind in an old session.

## Family phones and agents

The house page's **Family** button (`family` in `NEXT_PUBLIC_FEATURES`, on by default) shows one QR code for a
whole family (reusable for 24 hours). Each person scans it on their phone and types their name; the
phone becomes their remote for whatever house the big screen has open, with **their own agent**:
its own memory of what they need (B4), its own conversation. The agents compare notes on a shared
family board and ask each other ("ask Leo about the kitchen"), and the big screen's agent dock
shows everything, labelled by person. A phone that scans again comes back as the same person.

The **Family** tab (people icon, after Design) is a board of what each person wants from a home:
one card per person, plus one for the big screen. Each person's guide writes on their card as it
learns (must-haves, taste, dislikes, wishes for rooms of this house), and anyone at the big screen
can add or delete a line by hand. A person with no family has a board with one card.

Real phones need the real API (the hub and the agents), and a way to reach this computer.
`pnpm dev:all` (repo root) starts both, opens a tunnel for the phones and points the QR code at
it (`pnpm dev:all:local` keeps to your Wi-Fi). By hand, on your Wi-Fi:

```sh
uvicorn services.api.main:app --host 0.0.0.0 --port 8000      # repo root
NEXT_PUBLIC_USE_FIXTURES=0 \
  NEXT_PUBLIC_PUBLIC_ORIGIN=http://<this-computer's-LAN-IP>:3000 \
  DEV_ALLOWED_ORIGINS=<LAN-IP> pnpm dev -H 0.0.0.0              # apps/web
```

Without an LLM key the agents are scripted (try "ask Leo about the kitchen" or "tell the family the
yard is small"). Households live in `data/agent/households.sqlite`. Push-to-talk on a phone needs
HTTPS; typed chat works over plain HTTP.

## Hand control

The **Hands** button turns on the webcam and follows the user's hands with MediaPipe, in the
browser (`src/lib/hands/`, after HoloTouch). Nothing leaves the machine.

- In the Tour, hands arrange furniture (the phone moves the camera): see
  `docs/testing/hands-furniture.md`.

A ring follows each hand over the view (sky blue left, coral right) and tightens as the
fingers close; a small camera picture in the corner shows what is tracked. The wasm is copied to
`public/mediapipe` at install; the model is fetched from Google's model storage the first time
(`NEXT_PUBLIC_HAND_MODEL_URL` points elsewhere).

## The Tour: indoors and outdoors

The Tour walks through the rebuilt house (`src/components/house3d/`), with its baked light and the
sun of the session's moment: the time bar under the view, the phone's time scrubber and the guide
all move it. There is no separate sun page.
It replaced the Matterport Showcase viewer: no SDK key, and furniture is drawn by our own renderer.
The camera (`src/components/tour/TourCamera.tsx`, walking ported from 3d_rendering_matterport):

- **Inside**, at eye height: drag to look around, scroll to zoom, W A S D or the arrows to walk
  (Shift runs). The camera stops at walls and climbs the stairs. A click on a spot goes to the scan
  position nearest it. `viewer.goto`, `viewer.walk`, `viewer.step`, `viewer.look` and
  `viewer.zoom` move it between scan positions as before; walking into another room is a
  `viewer.goto` with the scan position nearest you, so the session follows.
- **Outside** (the room list's **Outside** entry): around the house without its roof; drag to
  turn it, scroll to come nearer. A click on a room goes in there.
- **Above a room**, while arranging furniture or showing a layout: looking down, with the storeys
  above the room hidden.

"Outside" is not an event in schema v0 (`viewer.goto` names a room or a sweep), so it is local
to the tab for now (`src/lib/store/tour.ts`).

## Not verified yet

- **A real phone**, and full-stack mode: there is no backend on this branch yet.
- **Second property** (M6): nothing in `src/` names a property id, but only one twin exists.
- Speech: with `NEXT_PUBLIC_SPEECH_PROVIDER=deepgram` (and `DEEPGRAM_API_KEY` in the API's
  `.env`), Deepgram speaks and transcribes push-to-talk: the microphone streams 16-bit PCM to
  `/ws/speech/listen`, which relays it to Deepgram. Without the key, or in fixture mode, the
  browser's own speech takes over.
