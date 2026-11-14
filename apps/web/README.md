# Ithaca web (Stream A: Web & Phone)

Next.js app for [PLAN.md](../../PLAN.md) sections 7 and 8, built to the contracts in
[WORKSTREAMS.md](../../WORKSTREAMS.md).

| Route | What it is |
|---|---|
| `/house/[id]` | Desktop experience: Tour, Sun Lab, Sound, Neighborhood, Design, and the agent dock |
| `/remote/[session]` | Phone controller (QR pairing) |
| `/me` | Furniture inventory, memory files, per-home decision history |
| `/render/layout/[id]` | Deterministic render for the thumbnail job (section 2.6) |

## Run it

```sh
pnpm install                       # also copies the Matterport bundle to public/matterport-assets
git lfs pull                       # fixtures/blobs is in Git LFS (the house mesh is 18 MB)
cp .env.example apps/web/.env.local   # then set NEXT_PUBLIC_MATTERPORT_SDK_KEY
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
pnpm --filter @ithaca/web mock-phone --session <id> --token <pair_token> send viewer.setMode '{"mode":"sun"}'
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
| A5 frame-free placement | `Placement.position` and `yaw_deg` are used as stored in the Tour, the Sun Lab, the plan preview and the render page. |
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

1. **Frame check (C6): passes.** The Tour tab compares `mpSdk.Sweep.data` with `twin.sweeps` at
   runtime and shows the result as a badge. For twin v2 of this model: the Showcase `sid` equals
   `sweep_id`, 90 of the 96 twin sweeps are in the Showcase, and all 90 positions agree within
   1 mm. I-G9 holds; furniture poses can be used as they are.
   The other 6 twin sweeps (labels 27, 56, 57, 63, 81, 87) are not in the Showcase sweep list,
   and 5 of them have a `room_id`. `Sweep.moveTo` cannot go to them. If one is the best sweep
   for a room, the Tour falls back to the nearest Showcase sweep.
2. **The meshopt GLB loads** with drei's `useGLTF` and no extra setup (about 15 s under software
   WebGL in CI). `fixtures.test.ts` checks that it lies in the interior frame around the rooms.
   The materials have `baseColorFactor` 0.4, which darkens the photo textures; the Sun Lab
   ignores the factor on textured materials.
3. **Annotation UI** starts from the detected openings, lets the user add and delete, and saves
   the full `Opening[]` with `PUT /openings`. New openings have ids `ann-<uuid>` and a provisional
   `facade_azimuth_deg` (outward normal through `interior_to_world`).

How the web app uses the twin:

- **Asset keys.** In fixture mode a path such as `blobs/<pid>/interior.glb` becomes
  `/fixtures/blobs/<pid>/interior.glb`. A value that is already a URL is used as it is.
- **Repeated room labels.** `src/lib/rooms.ts` names rooms for display: a label used once stays
  ("Office"); a repeated one is numbered from the ground floor up and from the largest room down
  ("Bedroom 1" .. "Bedroom 6"). The room list also shows the area. Everything keys on `room_id`.
- **No interior mesh** (`interior_glb: null`): the Sun Lab draws rooms from their outlines.
  **No context mesh:** it draws a plain ground and shows a "No surroundings" badge.
- **Sun direction** uses suncalc 2.x, whose azimuth is already in degrees clockwise from north.
  The recipe in the handoff is for suncalc 1.x (radians, from south). `position.test.ts` holds
  the result within 0.5 degrees of `fixtures/sun/reference.json` (today: 0.05). That file is
  written by `scripts/fixtures/sun_reference.py` until C ships its own.
- **Light through openings.** Windows, sliders and glazed doors let sun in: the shadow pass skips
  the mesh inside each such opening (`src/lib/sunlab/portals.ts`). A plain door blocks light.
- **Sun report shape.** The Sun Lab asks for a whole calendar year per room and reads
  `heatmap.hours[row][col]` with rows along +z and columns along +x from `origin_xz`.

Still open for C:

1. `context_glb` is taken to be in the interior frame, like `interior_glb`.
2. The ortho image has no georeference in the schema, so the Neighborhood tab would show it as a
   picture beside a separate distance map. An optional `ortho_bounds` would let them share a map.
3. `services/api/twin/requirements.txt` does not list `trimesh`, which `mesh.py` imports; the
   tests pass once it is installed.
4. A Draco-compressed GLB would need a decoder that drei loads from a CDN. meshopt needs nothing.

## What the house page shows

By default the house page has two views, the Matterport tour and the Sun Lab, with the room
list beside them. Everything else is built and tested but switched off. `NEXT_PUBLIC_FEATURES` turns parts on:

| Value | Adds |
|---|---|
| `tour` (default) | The Matterport tour and the room list |
| `sun` (default) | The Sun Lab: the scanned interior under the moving sun |
| `sound`, `neighborhood`, `design` | That view, as a tab |
| `agent` | The chat dock |
| `phone` | QR pairing for the phone controller |
| `me` | The link to `/me` |
| `all` | Everything |

Example: `NEXT_PUBLIC_FEATURES=tour,sun,agent`.

## The Tour: indoors and outdoors

The Tour shows the space with one of two Matterport viewers (`NEXT_PUBLIC_MATTERPORT_VIEWER`):

- **`embed` (default):** Matterport's hosted viewer in an iframe, driven by the SDK for Embeds
  (`@matterport/sdk`). It is always the current viewer, so it shows everything the model has,
  including the exterior capture. The room list's **Outside** entry goes there (the viewer's
  dollhouse mode, drawn with the exterior capture): drag to orbit the building, scroll to zoom.
  A room on the list takes you back in. This viewer cannot add 3D objects.
- **`bundle`:** the Bundle SDK from `@matterport/webcomponent`. It can add 3D objects
  (`Scene.createObjects`), which the Design view needs to place furniture in the tour. The newest
  bundle on npm (26.5.4) cannot load exterior splat captures and would wait for them forever, so
  `src/lib/showcase/splatShim.ts` answers its `GetSplats` request with an empty list. The tour
  then starts, without the outside view. Nothing in the vendor bundle is changed.
  `NEXT_PUBLIC_MATTERPORT_SPLATS=1` turns the shim off once a newer bundle is installed.

"Outside" is not an event in schema v0 (`viewer.goto` names a room or a sweep), so it is local
to the tab for now (`src/lib/store/tour.ts`).

## Not verified yet

- **A real phone**, and full-stack mode: there is no backend on this branch yet.
- **Second property** (M6): nothing in `src/` names a property id, but only one twin exists.
- Speech: only the Web Speech provider exists behind `SpeechProvider`.
