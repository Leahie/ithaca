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
git lfs pull                       # fixture GLBs are in Git LFS
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
pnpm test          # unit tests, incl. the WebSocket transport and mock phone against a test hub
pnpm e2e           # Playwright, fixture mode. Starts its own dev server on port 3210.
pnpm schema:check  # generated TS types are up to date (needs uv)
```

- Next allows one dev server per folder. If `pnpm dev` is running, point the tests at it:
  `E2E_PORT=3000 pnpm e2e`.
- No bundled browser? `PLAYWRIGHT_CHROMIUM_PATH=/usr/bin/chromium pnpm e2e`.
- Full-stack mode (web, api and worker already up): `E2E_BASE_URL=http://localhost:3000 pnpm e2e`.

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
| A1 one store, two inputs | `src/lib/store/reducer.ts` is the only place viewer state changes. `dispatch` (a click) and `receive` (an event) both call it. Tested in `session.test.ts` and `e2e/agent.spec.ts`. |
| A2 phone is a pure controller | The phone uses the same store and rebuilds from `session.state`. The desktop persists `layout.select`. `e2e/phone.spec.ts` kills the phone. |
| A3 show numbers, do not compute them | Sun hours, dBA and RT60 are read from reports. The one computed value is the sun direction (`src/lib/sun/position.ts`), held within 0.5° of `fixtures/sun/reference.json` by `position.test.ts` (worst case today: 0.05°). |
| A4 audio from `NoiseReport` only | `src/lib/audio/auralizer.ts`: gain from `laeq_dba`, band filters from `bands_db`, impulse response from `rt60_s`. The free values are named in `AURALIZATION`. |
| A5 frame-free placement | `Placement.position` and `yaw_deg` are used as stored in the Tour, the Sun Lab, the plan preview and the render page. |
| A6 every tab works offline | `src/lib/api/fixture.ts`, `src/lib/realtime/local.ts`, the `/fixtures/[...path]` route. |
| A7 one typed client | `src/lib/api/types.ts` (`ApiClient`), with HTTP and fixture implementations. Types come from `@ithaca/schema`. Unknown events are logged and dropped in `store/session.ts`. |
| A8 streaming agent output | Tokens, action chips and errors: `components/house/AgentDock.tsx`. |
| A9 render determinism | `components/render/LayoutRender.tsx`: fixed camera and light, frames on demand. `e2e/render.spec.ts` compares two PNGs. |

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

**For Stream C**

1. Frames. Model API coordinates are Z up; the interior frame is `(x, z, -y)`. The Tour tab
   compares twin sweeps with the SDK's at runtime ("Frame check"). For the fixture twin: 90 of 96
   sweeps are in the Showcase and all 90 match within 1 mm, so I-G9 holds for this model. The
   other 6 are not navigable in the Showcase.
2. `context_glb` is taken to be in the interior frame, like `interior_glb`.
3. The ortho image has no georeference in the schema, so the Neighborhood tab shows it as a
   picture and draws places on a separate distance map. An optional `ortho_bounds` would let
   them share one map.
4. `PUT /openings` sends the full list. New openings have ids `ann-<uuid>` and a provisional
   `facade_azimuth_deg` (outward normal through `interior_to_world`).
5. The Sun Lab asks for a whole calendar year per room and reads `heatmap.hours[row][col]`
   with rows along +z and columns along +x from `origin_xz`.
6. Light enters through windows, sliders and glazed doors: the shadow pass skips the mesh
   inside each such opening (`src/lib/sunlab/portals.ts`). A plain door blocks light.
7. A Draco-compressed GLB needs a decoder, which drei loads from a CDN. meshopt needs nothing.

## Not verified yet

- **The Showcase in a headless browser.** The SDK connects and returns model data, but the
  bundle stays in its loading phase under headless Chromium here, also on Matterport's own
  `showcase.html`. `Sweep.moveTo`, `Camera.rotate` and furniture placement in the Showcase are
  written against the SDK types and need a check in a normal browser. The matching E2E test
  skips itself when the Showcase does not start.
- **A real phone**, and full-stack mode: there is no backend on this branch yet.
- **Second property** (M6): nothing in `src/` names a property id, but only one twin exists.
- Speech: only the Web Speech provider exists behind `SpeechProvider`.
