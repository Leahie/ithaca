# Ithaca: work split for three people

This file splits [PLAN.md](PLAN.md) into three workstreams. Each person can work on their own, against fixed contracts and mocks, and merge at a few planned milestones. PLAN.md is still the source for *what* we build. This file defines *who* builds which part and *which rules must stay true* so the parts fit together.

| Stream | Owner | Short name | Main language |
|---|---|---|---|
| **A. Web & Phone** | _tbd_ | `web` | TypeScript (Next.js, R3F, Showcase SDK, Web Audio) |
| **B. Agent, Realtime & Objects** | _tbd_ | `agent` | Python (FastAPI, deepagents, arq, fal.ai) |
| **C. Twin, Data & Simulation** | _tbd_ | `twin` | Python (integrations, trimesh, pvlib, noise) |

How it works:
1. **Day 0 (together, ~half a day):** merge the repo skeleton and **schema v0** (section 2) exactly as written here. After this, nobody waits on anybody.
2. **Each stream works against mocks** that the other streams ship by the end of Day 1 (section 3).
3. **Milestones M0–M6** (section 7) are the only points where real integration is needed. Phases are integration targets, not a work order. Work ahead on fixtures when you can.

---

## 1. Global invariants (everyone, always)

These rules apply to all code. If you must break one, open an issue first and get agreement from the other two owners.

### 1.1 Ownership and merging
- **I-G1. Directory ownership.** Every path has one owner (table below). A PR that changes a path you do not own needs a review from its owner.
- **I-G2. `main` is always green.** CI runs unit tests, fixture-replay integration tests and the schema codegen check. Nobody merges on red.
- **I-G3. Schema changes need every consumer's review.** A change in `packages/schema` needs approval from each stream that reads that type (see section 2).
- **I-G4. Migrations.** Each stream writes Alembic migrations only for its own tables. Rebase before merge so the chain stays linear. Never edit a migration that is already on `main`.

| Path | Owner |
|---|---|
| `apps/web/**` (all routes, incl. `/remote`, `/me`, `/render`) | A |
| `apps/web/e2e/**` (Playwright harness) | A |
| `services/api/main.py`, `services/api/agent/**`, `services/api/realtime/**`, `services/api/objects/**`, `services/api/routers/{sessions,inventory,layouts,memory,objects,uploads}.py` | B |
| `services/api/sim/layout_solver.py` | B |
| `services/worker/**` (arq runner, job registry, Playwright thumbnail job) | B |
| `packages/schema/` tooling (codegen, CI check) and `events.py`, `inventory.py`, `layout.py`, `session.py` | B |
| `services/api/integrations/**`, `services/api/twin/**`, `services/api/sim/{sun,noise}.py`, `services/api/routers/properties.py` | C |
| `packages/schema/twin.py`, `reports.py` | C |
| `infra/**` (docker-compose, Postgres, MinIO), Alembic setup, `fixtures/` layout and record/replay helper | C |
| `.env.example` | shared: each stream adds its own keys |

### 1.2 Contracts and types
- **I-G5. One source of truth for types.** Pydantic models in `packages/schema` are the source. TS types are generated into `packages/schema/ts/` and committed. CI fails if they are stale. Nobody hand-writes a TS type for a server payload.
- **I-G6. Additive changes only.** You can add an optional field at any time. To remove or rename a field or event, add the new one, support both for one milestone, then remove the old one. The event envelope carries `v`. Increase it only for a breaking change.
- **I-G7. Unknown is not an error.** Clients and the hub ignore unknown event types and unknown fields, and log them. They never throw.

### 1.3 Units and frames (the most common source of integration bugs)
- **I-G8. Meters, degrees, seconds.** Every API and stored value uses meters, degrees, and seconds or ISO 8601 time. Radians exist only inside renderer and math internals.
- **I-G9. Interior frame = Matterport Showcase SDK frame.** Right-handed, **Y up**, meters. All room polygons, openings, sweeps and furniture poses are stored in this frame. So the frontend can place items in Showcase and in the Sun Lab with no transform.
- **I-G10. World frame = local ENU.** X east, Y north, Z up, meters. The origin is the property's (lat, lon, ground elevation). `interior_to_world` is a 4×4 matrix in **column-major** order (the same order as three.js `Matrix4.fromArray`). Only sun, DSM and noise code uses the world frame.
- **I-G11. Compass angles** (sun azimuth, facade azimuth) are degrees **clockwise from true north**, in [0, 360). A facade azimuth is the direction of the opening's **outward** normal.
- **I-G12. Yaw** is the rotation about +Y in degrees, positive counter-clockwise when you look down from above. Yaw 0 means the item's front (+Z in item space) points along interior +Z.
- **I-G13. Time.** Datetimes are ISO 8601 strings **with an offset**, in the property's IANA timezone (`PropertyTwin.timezone`). "Hour" in reports means local hour 0–23.
- **I-G14. Sound.** Levels are dB. A-weighted totals are labeled `dba`. Octave bands are always the 8-element array `[63, 125, 250, 500, 1000, 2000, 4000, 8000]` Hz, in that order.
- **I-G15. 3D assets** are glTF 2.0 binary (`.glb`) in meters with Y up. Furniture GLBs also have front = +Z and the origin at the bottom center of the bounding box.

### 1.4 Identity
- **I-G16. `property_id` is an opaque string.** In v1 it equals the Matterport model id (`onXQLXhfyQv`). Code must read Matterport ids from `PropertyTwin.sources.matterport_model_id` and never assume the two are the same.
- **I-G17. Room, opening and sweep ids are stable across re-ingestion.** Memory files and saved layouts refer to them. Derive them from the Matterport source ids, not from row order.
- **I-G18. Everything user-scoped is keyed by `user_id` from day one.** v1 has one user, sent as the `X-User-Id` header (default `demo`). There is no code path that reads data without a user id.
- **I-G19. No property-specific code.** `onXQLXhfyQv` appears only in fixtures, tests, seed scripts and config.

### 1.5 External services and config
- **I-G20. CI never calls a live API.** Every external client (Matterport, Vexcel, CoStar, Overpass, fal.ai, LLMs in non-eval tests) supports `INTEGRATION_MODE=live|record|replay`. CI uses `replay`. Text fixtures go under `fixtures/`. Binary fixtures (GLB, DSM, images) go in Git LFS under `fixtures/blobs/`.
- **I-G21. A missing key disables a feature. It never stops startup.** The feature returns a clear "not configured" error and the UI shows it.
- **I-G22. Secrets stay on their side.** The Showcase SDK key is frontend-only. All other API keys are backend-only.

---

## 2. Schema v0 (merge on Day 0)

These are the shapes. Field names are binding. Extend only per I-G6. The consumer columns say whose review a change needs (I-G3).

### 2.1 Twin and reports: owner C; consumers A, B

```python
class PropertyTwin:
    property_id: str
    version: int                      # bumps only when ingestion output changes
    sources: {matterport_model_id: str, vexcel_parcel_id: str | None, costar_listing_id: str | None}
    address: str
    lat: float; lon: float
    timezone: str                     # IANA, e.g. "America/Chicago"
    interior_to_world: list[float]    # 16 floats, column-major (I-G10)
    floors: list[{floor_id: str, index: int, elevation_m: float}]
    rooms: list[Room]
    openings: list[Opening]
    sweeps: list[{sweep_id: str, floor_id: str, room_id: str | None, position: Vec3}]
    assets: {interior_glb: str, context_glb: str | None, ortho: str | None, obliques: list[str]}  # URLs, resolved by the API
    quality: {alignment: "icp" | "gps_only",
              openings: "annotated" | "detected" | "none",
              context: "dsm" | "none",
              traffic: "aadt" | "class_default" | "none"}

class Room:
    room_id: str; floor_id: str; label: str
    polygon: list[tuple[float, float]]   # (x, z) in interior frame, CCW seen from above, not closed
    floor_y: float; height_m: float
    floor_material: str                  # key in the shared absorption table

class Opening:
    opening_id: str; room_id: str
    kind: "window" | "door" | "slider"
    polygon: list[Vec3]                  # interior frame
    facade_azimuth_deg: float            # I-G11
    glazing: "none" | "single" | "double" | "triple"
    stc: int | None

class SunReport:
    room_id: str; start: date; end: date
    daily: list[{date: date, direct_sun_hours: float}]
    heatmap: {cell_m: float, origin_xz: tuple[float, float], hours: list[list[float]]} | None

class NoiseScenario:
    glazing: "single" | "double" | "triple" | None   # None = as built
    add_carpet: bool = False
    include_furniture: bool = True

class NoiseReport:
    room_id: str; scenario: NoiseScenario
    hourly: list[{hour: int, laeq_dba: float, bands_db: list[float]}]   # 24 entries, 8 bands (I-G14)
    rt60_s: float
    sources: list[{name: str, contribution_dba: float}]

class AreaProfile:
    markdown: str
    amenities: list[{name: str, category: str, lat: float, lon: float, distance_m: float, source: "vexcel" | "costar" | "osm"}]
    comps: list[{address: str, price: int, sqft: int | None, beds: int | None, sold_date: date | None, distance_m: float}]
```

### 2.2 Inventory, layouts, sessions: owner B; consumers A (all), C (none)

```python
class InventoryItem:
    item_id: UUID; user_id: str
    label: str; item_class: str          # from the shared furniture class list
    attributes: {style: str | None, color: str | None, material: str | None}
    dims_m: {w: float, d: float, h: float}   # w along X, d along Z, h along Y, item space
    glb_url: str; source_image_url: str; mask_url: str | None
    price: float | None; product_url: str | None

class Placement:
    item_id: UUID
    position: Vec3                       # interior frame; position.y == room.floor_y
    yaw_deg: float                       # I-G12

class LayoutVariant:
    layout_id: UUID; user_id: str; property_id: str; room_id: str
    placements: list[Placement]
    score: float; rationale: str
    status: "proposed" | "chosen" | "rejected"
    feedback: str | None
    thumbnail_url: str | None            # null until the render job finishes

class Session:
    session_id: str; pair_token: str; expires_at: datetime
```

### 2.3 Event envelope and v0 events: owner B; consumer A

```jsonc
{ "v": 1, "id": "uuid", "ts": "2026-11-14T10:00:00-06:00",
  "session_id": "…", "source": "desktop" | "phone" | "agent" | "server",
  "type": "viewer.goto", "payload": { … } }
```

| Event `type` | Payload | Sent by |
|---|---|---|
| `session.state` | full viewer state snapshot (`mode`, `room_id`, `datetime`, `sound_scenario`, `shown_layout_ids`) | server, on join |
| `session.joined` / `session.left` | `{client: "desktop" \| "phone"}` | server |
| `viewer.goto` | `{room_id?: str, sweep_id?: str}` (exactly one) | agent, phone, desktop |
| `viewer.setTime` | `{datetime: str}` (I-G13) | agent, phone, desktop |
| `viewer.setMode` | `{mode: "tour" \| "sun" \| "sound" \| "neighborhood" \| "design"}` | agent, phone, desktop |
| `viewer.look` | `{yaw_delta_deg: float, pitch_delta_deg: float}` | phone |
| `design.showVariants` | `{layout_ids: UUID[]}` | agent |
| `layout.select` | `{layout_id: UUID}` | phone, desktop |
| `sound.setScenario` | `{room_id: str, scenario: NoiseScenario}` | agent, phone, desktop |
| `capture.upload` | `{upload_id: str}` (file goes through REST first) | phone, desktop |
| `chat.user` | `{text: str}` | desktop, phone |
| `voice.transcript` | `{text: str, final: bool}` | desktop, phone |
| `agent.token` | `{message_id: str, delta: str}` | agent |
| `agent.message` | `{message_id: str, text: str}` (final text) | agent |
| `agent.action` | `{tool: str, summary: str}` (for UI chips) | agent |
| `agent.error` | `{message: str}` | agent |
| `job.progress` | `{job_id: str, kind: str, pct: float, status: "queued" \| "running" \| "done" \| "failed", result_ref?: str}` | server |

Agent UI tools use snake_case names, because some providers reject dots in tool names. Each tool maps to one event: `viewer_goto → viewer.goto`, `viewer_set_time → viewer.setTime`, `viewer_set_mode → viewer.setMode`, `design_show_variants → design.showVariants`, `sound_set_scenario → sound.setScenario`.

### 2.4 REST surface v0

All routes are under `/api` and take `X-User-Id`.

| Route | Returns | Owner |
|---|---|---|
| `GET /properties/{pid}/twin` | `PropertyTwin` | C |
| `PUT /properties/{pid}/openings` | `Opening[]` (annotation mode saves here) | C |
| `GET /properties/{pid}/sun?room_id&start&end` | `SunReport` | C |
| `GET /properties/{pid}/noise?room_id&glazing&add_carpet` | `NoiseReport` | C |
| `GET /properties/{pid}/area` | `AreaProfile` | C |
| `POST /properties/{pid}/ingest` | `{job_id}` | C |
| `POST /sessions` · `POST /sessions/{sid}/join` | `Session` · `{ok}` | B |
| `WS /ws/sessions/{sid}?client=desktop\|phone&token=` | event stream | B |
| `POST /uploads` (multipart) | `{upload_id, url}` | B |
| `POST /objects/ingest {upload_id}` | `{job_id}` | B |
| `GET /inventory` · `GET/DELETE /inventory/{id}` | `InventoryItem[]` · `InventoryItem` | B |
| `GET /layouts?property_id&room_id` · `POST /layouts/{id}/select` · `POST /layouts/{id}/feedback` | `LayoutVariant[]` · `LayoutVariant` | B |
| `GET/PUT/DELETE /memory/{path}` · `GET /memory` | file text · file list | B |

### 2.5 In-process Python interface C → B

The agent tools in B call these functions and never call `integrations/*` directly. C owns their behavior. B owns the tool wrappers and their prompts.

```python
# services/api/twin/service.py
def get_twin(property_id: str) -> PropertyTwin
def sun_report(property_id: str, room_id: str, start: date, end: date) -> SunReport
def noise_report(property_id: str, room_id: str, scenario: NoiseScenario) -> NoiseReport
def area_profile(property_id: str) -> AreaProfile
def area_search(property_id: str, query: str, limit: int = 10) -> list[Amenity]
def get_comps(property_id: str) -> list[Comp]
```

### 2.6 Render page contract A → B
- A provides `GET /render/layout/{layout_id}` in `apps/web`. It renders the room and the placements with a fixed camera. When the frame is final, it sets `window.__RENDER_READY__ = true`.
- B's worker opens this page with Playwright at 800×600, waits for the flag (timeout 20 s), takes a screenshot, uploads it and sets `LayoutVariant.thumbnail_url`.

---

## 3. Mocks each stream ships by end of Day 1

These mocks let the other two streams start at once. Each mock must pass schema validation. A mock can stay in the repo behind a flag after the real thing exists.

| From | Mock | Lets |
|---|---|---|
| C | `fixtures/twin/onXQLXhfyQv.json`: a valid `PropertyTwin`. Hand-made rooms are acceptable at first. | A, B |
| C | `fixtures/blobs/onXQLXhfyQv/interior.glb` (real Matterport export if available, else a box house that matches the fixture rooms) | A |
| C | `twin/service.py` with all section 2.5 functions returning deterministic fixture data when `TWIN_MODE=fixture` | B |
| B | WS hub that works for real (in-memory if Redis is not up yet) plus `AGENT_PROVIDER=scripted`: a fake agent that streams tokens and emits fixed UI events for a set of known prompts ("take me to the kitchen", "show me December morning", "show layouts") | A |
| B | `fixtures/inventory/demo.json` with 3 items and their GLBs; `GET /layouts` returns 3 hand-placed valid variants for one room | A |
| A | `apps/web/scripts/mock-phone.ts`: a CLI that pairs with a session and sends any v0 event from the command line | B |
| A | `/render/layout/[id]` stub that draws boxes for the placements and sets the ready flag | B |

---

## 4. Stream A: Web & Phone

**Scope (from PLAN.md sections 7 and 8, plus the client half of sections 2.3 and 6):** the Next.js app; Tour tab with Showcase SDK (embed, `Sweep.moveTo`, item placement through `Scene.createObjects` + `gltfLoader`); Sun Lab (R3F scene, interior GLB plus DSM context, suncalc light, CSM shadows, time scrubber, outside-to-inside flight); openings **annotation UI** (data goes to C's endpoint); Sound tab (hourly chart, Web Audio auralization, toggles); Neighborhood tab; Design tab (variant cards, apply); agent dock (streaming chat, action chips, push-to-talk); `SpeechProvider` (Web Speech default); `/me`; `/remote/[session]` phone PWA with QR pairing; `/render/layout`; Playwright E2E harness.

**Provides:** the user-facing app, the render page (section 2.6), the mock phone, E2E tests.
**Consumes:** schema TS types; REST + WS from B; twin, reports and assets from C.

### Invariants
- **A1. One store, two inputs.** Viewer state (mode, room, datetime, scenario, shown layouts) lives in one client store. A click and an incoming event dispatch the **same** action. So everything the agent or phone can do, a click can also do, and the reverse.
- **A2. The desktop is the viewer authority. The phone is a pure controller.** The phone holds no state that the desktop does not also hold. After a reconnect, the phone rebuilds its view from `session.state`. Killing the phone at any moment must not break the desktop.
- **A3. The frontend shows numbers. It does not compute them.** Sun hours, dBA and RT60 come from C's reports. The one exception is the realtime sun direction (suncalc). It must match C's pvlib reference fixture within **0.5°** (shared test file `fixtures/sun/reference.json`).
- **A4. Auralization is driven only by `NoiseReport`.** Gain comes from `laeq_dba`. The filter comes from `bands_db`. The reverb impulse response comes from `rt60_s`. There are no hidden constants in the audio graph that change what the user hears.
- **A5. Placement is frame-free.** Items are placed with the `Placement.position` and `yaw_deg` values as they are (I-G9, I-G12). There is no per-view offset. If an item looks wrong in Showcase but right in the Sun Lab (or the reverse), that is a bug in a frame, not something to patch locally.
- **A6. Every tab works offline.** With `NEXT_PUBLIC_USE_FIXTURES=1`, every tab renders from fixtures with no backend running. E2E tests run in both fixture mode and full-stack mode.
- **A7. All server calls go through one typed client** generated from the schema (I-G5). Unknown events are logged and dropped (I-G7).
- **A8. Agent output is shown as it streams.** Tokens appear as they arrive. Each `agent.action` shows a chip. An `agent.error` is shown to the user, never swallowed.
- **A9. Render determinism.** `/render/layout` uses a fixed camera, a fixed light and no animation, so the same layout gives the same PNG.

### Work per phase
| Phase | A delivers |
|---|---|
| 0 | Next.js skeleton, Showcase embed with SDK key, typed API client from codegen, fixture mode, mock phone, render stub |
| 1 | `/house/[id]` shell + tabs, Tour moves on `viewer.goto`, agent dock on the scripted agent, then on the real one |
| 2 | `/me` (memory editor, inventory grid, 3D preview, mask overlay), upload flow, Design tab cards, placement in Showcase, real render page |
| 3 | Sun Lab scene, scrubber, openings annotation mode → `PUT /openings`, sun-hours heatmap overlay |
| 4 | QR pairing, `/remote` controls (time, rooms, look, variants swipe, camera capture, PTT), `SpeechProvider` |
| 5 | Sound tab, auralization, scenario toggles |
| 6 | Neighborhood tab (ortho map, obliques, comps, amenities), second-property smoke test |

---

## 5. Stream B: Agent, Realtime & Objects

**Scope (PLAN.md sections 3, 4 and 5, plus `realtime/` and the worker):** FastAPI app shell and routers; deepagents graph with `CompositeBackend` + `StoreBackend` memories; model factory (Anthropic / OpenRouter, per-role overrides, free-model whitelist, retry and fallback); subagents (designer, analyst, scout, shopper later); domain tools (wrapping section 2.5) and UI tools (section 2.3 mapping); streaming to WS; session hub on Redis; uploads; object pipeline (classify/OCR, segment, image-to-3D, scale and orient normalization, attributes, CLIP); inventory; layout solver and the designer constraint format; layouts persistence and feedback to memory; arq worker and the thumbnail job; agent evals.

**Provides:** sessions, the WS hub, all agent behavior, inventory, layouts, memory API, the job system.
**Consumes:** section 2.5 functions from C; the render page from A.

### Invariants
- **B1. The agent speaks in ids, never coordinates.** UI tools take `room_id`, `sweep_id`, `layout_id` and datetimes only. Coordinates come only from the solver.
- **B2. The solver never returns an invalid layout.** Every returned variant meets all hard constraints: each item footprint is fully inside `Room.polygon`, there are no overlaps, door swing and window clearance are respected, and a 0.9 m circulation path from each door exists. If fewer than N valid variants exist, it returns fewer, with a reason. The frontend trusts this and does no checks of its own. These are the solver's unit tests.
- **B3. The DB is canonical for inventory.** `/memories/inventory.md` is a derived view and can be rebuilt from the DB at any time. The agent may write to `profile.md`, `preferences.md` and `properties/<id>.md` freely.
- **B4. Memory isolation.** The store namespace is always `(user_id,)`. No tool, route or subagent reads another user's namespace (eval 3).
- **B5. Provider-agnostic agent.** Every agent code path works on Anthropic and on each whitelisted OpenRouter model. A model joins the whitelist only after it passes the eval suite. Rate-limit fallback never changes tool schemas.
- **B6. Normalized objects only.** An item is written to `inventory_items` only after its GLB meets I-G15 and its bounding box matches `dims_m` within 2 cm. If scale or orientation fails, the job fails with a message. It does not write a bad item.
- **B7. Providers stay behind interfaces.** fal.ai, Replicate and similar types never leave `objects/providers/`. The rest of the code sees `Segmenter` and `ImageTo3D` only.
- **B8. Anything slow is a job.** Any operation that can take more than ~2 s runs as an arq job and reports `job.progress`. HTTP handlers return a `job_id`.
- **B9. Hub semantics.** Events within one session are delivered in order to every connected client, including the sender (clients drop their own by `id`). The hub keeps the latest viewer state and sends `session.state` on join. It does not persist event history.
- **B10. Pairing tokens** are single-use, expire in 10 minutes, and grant access to one session only.

### Work per phase
| Phase | B delivers |
|---|---|
| 0 | FastAPI shell, schema package + codegen CI, Redis hub, scripted agent, model factory with both providers verified, inventory/layout fixtures |
| 1 | Real deepagents graph, `/memories` on PostgresStore, `get_property_summary` / `list_rooms` / `get_room_geometry`, `viewer_goto` / `viewer_set_mode`, streaming, evals 1 and 3 scaffold |
| 2 | Upload → object pipeline → inventory; layout solver; designer subagent; `propose_layouts`; thumbnail job; feedback → `preferences.md`; eval 1 passing |
| 3 | `sun_report` tool, analyst subagent, `viewer_set_time`; eval 2 passing |
| 4 | Phone pairing endpoints, `voice.transcript` → agent turn |
| 5 | `noise_report` tool, `sound_set_scenario` |
| 6 | Scout subagent (`area_search`, `get_comps`), shopper subagent, cross-property memory demo |

---

## 6. Stream C: Twin, Data & Simulation

**Scope (PLAN.md sections 1, 2 and 6, minus the layout solver):** infra (docker-compose, Postgres + pgvector, MinIO, Alembic, record/replay helper); integration clients (Matterport, Vexcel, CoStar, roads/AADT) with caching; ingestion job (mesh → GLB, world alignment with ICP, openings storage and the v2 detector, DSM context mesh, area profile); `twin/service.py`; sun engine (pvlib, ray-cast sun-hours heatmap); noise engine (emission, propagation, transmission, Sabine, octave bands, RT60); property routes; reference tests.

**Provides:** `PropertyTwin`, all reports, assets, `twin/service.py`, infra.
**Consumes:** the openings annotation UI from A (through `PUT /openings`); the job runner from B (C registers its jobs in B's arq registry).

### Invariants
- **C1. Ingestion is idempotent and keyed by `property_id` only.** Re-running it on the same cached inputs gives byte-identical twin JSON and keeps the same `version`. `version` increases only if the output changes.
- **C2. Ids are stable** (I-G17). A re-ingest after a Matterport change keeps the ids of rooms that still exist.
- **C3. The twin is always valid, even when degraded.** If Vexcel has no coverage, ICP fails, there are no openings or there is no AADT, the twin is still produced. The gap is recorded in `quality`, and every report still has its full schema shape. The agent reads `quality` to add caveats. A missing data source never makes ingestion fail.
- **C4. The engines are pure.** `sun.py` and `noise.py` are functions of `(PropertyTwin, inputs)`. They do no I/O and give the same output for the same input. Caching (keyed by `twin.version`, `room_id` and inputs) sits outside the engines.
- **C5. Numbers are checked against references.** Sun position matches NOAA within 0.5° (and C maintains `fixtures/sun/reference.json` for A3). Noise passes hand-calculated cases (line-source distance doubling = −3 dB; an STC change moves L_in by the expected amount; adding carpet lowers RT60). A change that breaks a reference test does not merge.
- **C6. Frame correctness is tested.** A test round-trips a known point: interior → world → interior. A Phase 0 spike checks that Model API sweep positions equal Showcase SDK `Sweep.data` positions for `onXQLXhfyQv` (this is what makes I-G9 true). ICP is tested on a synthetic rotated footprint.
- **C7. Every external call goes through the cache** (I-G20). Fixtures for `onXQLXhfyQv` are recorded once and committed, so A and B never need Matterport, Vexcel or CoStar keys.
- **C8. Report latency.** `sun_report` and `noise_report` for one room return in under 2 s from cache (the heatmaps are precomputed at ingestion). If a value is not cached yet, the call starts a job and does not block the agent.
- **C9. Asset budget.** `context_glb` ≤ 10 MB after Draco/meshopt. `interior_glb` is full resolution (original 2048 px scan textures, no quantization; about 80 MB): render quality over load time.

### Work per phase
| Phase | C delivers |
|---|---|
| 0 | docker-compose, Alembic, record/replay helper, Day 1 twin fixture + GLB, `twin/service.py` fixture mode. Spikes: Matterport GraphQL (rooms, mesh, georef), frame check (C6), Vexcel coverage, CoStar join to the address. Write the spike results in `docs/spike-0.md`. |
| 1 | Ingestion steps 1, 2, 5: real rooms/sweeps/mesh, ICP alignment, area profile v1; `GET /twin` serves real data |
| 2 | Room geometry quality for the solver (clean polygons, door positions from openings or Matterport), absorption table shared with B's furniture classes |
| 3 | Openings storage, DSM context mesh, `sun.py` + heatmap precompute, `GET /sun` |
| 4 | Buffer: harden ingestion, open-vocabulary opening detector (v2) |
| 5 | `roads.py`, `noise.py`, `GET /noise` |
| 6 | Full area profile (CoStar + Vexcel embeddings), comps, run ingestion on a second property |

---

## 7. Integration milestones

Each milestone is a short demo that all three run together. Before a milestone, each stream switches its mocks off for the pieces involved. A milestone is met only when its check passes with mocks off.

| Milestone | Demo / check | Needs |
|---|---|---|
| **M0**: foundations | `docker compose up` starts web, api, worker, Postgres, Redis and MinIO. All tabs render in fixture mode. The mock phone sends `viewer.setMode` and the desktop changes tab. | Day 0 schema + all section 3 mocks |
| **M1**: tour + agent | "Take me to the kitchen" (typed) → real agent → `viewer.goto` → Showcase moves, on the real twin. | A1, B1, C1 |
| **M2**: objects + design | Upload a sofa screenshot → GLB in `/me` → "lay out the living room" → 3 variant cards with thumbnails → select → the sofa is in Showcase. Eval 1 passes. | A5, B2, B6, section 2.6 |
| **M3**: sun | Annotate windows → "how sunny is the bedroom in December?" → agent calls `sun_report` and `viewer_set_time` → Sun Lab light matches. Eval 2 passes. | A3, C5, C6 |
| **M4**: phone + voice | Pair a phone by QR, speak a question, scrub time from the phone, swipe variants. | A2, B9, B10 |
| **M5**: sound | Play bedroom traffic, single- vs. double-pane, add carpet; numbers in chat equal the numbers on the chart. | A4, C4, C5 |
| **M6**: generalize | Ingest a second property with no code change (I-G19). Agent says "your sofa from the other house fits on this wall". | C1, C3, B3 |

---

## 8. Hand-offs that cross streams

The few places where two streams touch the same feature, with the owner of each half:

| Feature | A builds | B builds | C builds |
|---|---|---|---|
| Openings | annotation UI (click on mesh) | none | storage, `facade_azimuth_deg`, detector v2 |
| Sun Lab | renderer, suncalc, scrubber | `viewer_set_time` tool | context GLB, pvlib reference, heatmap |
| Sound | chart + Web Audio | `noise_report` / `sound_set_scenario` tools | noise engine |
| Layouts | cards, placement, render page | solver, designer, thumbnail job, persistence | room polygons + door positions |
| Furniture absorption | none | furniture class list | absorption value per class (same keys as B's list, in `fixtures/tables/`) |
| Phone voice | Web Speech on the phone, `voice.transcript` | transcript → agent turn | none |
| Neighborhood | map, obliques, comps UI | scout subagent | `AreaProfile`, ortho/oblique assets |

When a hand-off is late, the consuming stream keeps using the section 3 mock and does not build a temporary copy of the other stream's part.
