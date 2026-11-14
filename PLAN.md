# Ithaca: immersive "live here before you buy" platform

## Context
Homebuyers can't easily picture daily life in a house before they buy it: where their furniture fits, how the sun moves through each room, how loud the street is, what the area is like. We are building a website that holds a digital twin of one house (Matterport model `onXQLXhfyQv`, HackPrinceton 2026 org) for one user. A conversational real-estate-agent/designer agent (LangChain **deepagents**) drives the site. It remembers the user's furniture, taste and feedback across properties, so the same twin pipeline and agent memory can later apply to any house.

Data sources: **Matterport** (interior mesh, panoramas, rooms, sweeps, GPS), **Vexcel** (ortho/oblique imagery, DSM elevation, property elements, semantic embeddings; coverage is Houston/LA), **CoStar Data Kit** (residential/apartment listings, listing features, sale comps, commercial properties and occupancy for area "vibe"). A phone acts as a second-screen controller, following the CoStar keynote theme: "AI at the center, multimodal inputs".

Decisions made: long-term architecture (not a hackathon throwaway); **hybrid viewer** (Matterport Showcase for the photoreal tour, plus our own Three.js "Sun Lab" on the downloaded mesh); **hosted APIs** for segmentation and image-to-3D; LLM can be **Claude or OpenRouter free models**, chosen by config.

The repo is empty (no commits), so everything below is greenfield.

---

## Architecture

```
apps/web (Next.js + React Three Fiber + Matterport Showcase SDK)
   ├─ /house/[id]        desktop experience (Tour | Sun Lab | Sound | Neighborhood | Design) + agent dock
   ├─ /remote/[session]  phone controller PWA (QR pairing)
   └─ /me                memory + inventory showcase
        │  REST + WebSocket (typed events)
services/api (Python, FastAPI)
   ├─ agent/        deepagents graph, tools, subagents, model factory
   ├─ integrations/ matterport.py, vexcel.py, costar.py, roads.py (OSM + state DOT AADT)
   ├─ twin/         property ingestion → PropertyTwin (rooms, openings, world transform, DSM context)
   ├─ sim/          sun.py, noise.py, layout_solver.py
   ├─ objects/      image → segment → 3D GLB → classify → inventory
   └─ realtime/     session hub (desktop ⇄ phone ⇄ agent), Redis pub/sub
services/worker (arq jobs: ingestion, 3D generation, thumbnail renders)
packages/schema   shared event + domain schemas (pydantic is the source; generate TS types via datamodel-codegen / json-schema-to-ts)
infra/docker-compose.yml  Postgres+pgvector, Redis, MinIO (S3), api, worker, web
```

### Data model (Postgres)
`users`, `properties` (source ids, address, lat/lon, `interior_to_world` transform), `rooms` (polygon, floor, height, label, floor material), `openings` (windows/doors: world polygon, facade azimuth, glazing type), `assets` (mesh GLB, DSM tile, ortho, obliques, panoramas → S3 keys), `inventory_items` (user_id, label, attributes, dims, glb_key, source_image_key, mask_key, CLIP embedding), `layouts` (user, property, room, variant JSON, status: proposed/chosen/rejected, feedback text), `sessions` (pairing tokens). LangGraph `PostgresStore` + `PostgresSaver` live in the same DB.

---

## Components

### 1. Integrations (`services/api/integrations/`)
- Each client caches every response to Postgres/S3, keyed by request hash. A `fixtures/` record mode lets dev and tests run without spending API quota.
- **matterport.py**: Model API GraphQL (rooms + dimensions, floors, sweeps with positions, georeference, asset downloads: OBJ/MatterPak, skybox panoramas). Showcase SDK key is used only on the frontend.
- **vexcel.py**: `/property/extract` (footprint, roof, trees, pool), `/ortho/extract`, `/oriented/extract` (obliques), DSM elevation tiles, `/embedding/extract` (text semantic search for nearby amenities).
- **costar.py**: Data Kit datasets. For the subject property: residential listing, listing features, sale comps. For the area profile (radius query): apartment listings, commercial properties, occupancy and company detail.
- **roads.py**: Overpass road segments plus TxDOT/Caltrans AADT counts where they exist. Otherwise fall back to default volumes by road class.

### 2. Property Twin ingestion (`twin/`, run as a worker job keyed only by property id, so it generalizes)
1. Pull Matterport rooms, sweeps and mesh. Convert OBJ → GLB with trimesh + gltfpack, decimated (Draco/meshopt).
2. **World alignment.** Start from Matterport GPS. Refine heading and offset with 2D ICP: match the Matterport floor outline against the Vexcel building footprint. Store `interior_to_world` (ENU). This is the indoor/outdoor alignment problem Matterport called out.
3. **Openings.** v1: an annotation mode in the web app where windows and doors are marked by clicking on the mesh (fine for one house). v2: an open-vocabulary detector ("window") on skybox faces, projected to the mesh with depth.
4. **Exterior context.** Clip a 150–200 m DSM around the parcel, build a heightfield mesh, and drape the Vexcel ortho over it. In the Sun Lab it casts shadows and shows the outdoor view.
5. **Area profile.** Combine CoStar radius queries with Vexcel semantic searches ("grocery store", "park", "school", "nightlife") into a markdown `area_profile` the agent can read.

### 3. Agent (`agent/`): deepagents
- `create_deep_agent(model, tools, system_prompt, subagents, backend, memory=[...])` with
  `CompositeBackend(default=StateBackend(), routes={"/memories/": StoreBackend(namespace=(user_id,))})`. Needs deepagents ≥ 0.5.
- Memory files per user: `/memories/profile.md` (household, commute, priorities), `/memories/preferences.md` (style, layout likes/dislikes with reasons), `/memories/inventory.md` (index of owned items; the DB is canonical), `/memories/properties/<id>.md` (notes and verdict per viewed home). The system prompt tells the agent to update these after every piece of feedback.
- **Model factory** `agent/models.py`: `LLM_PROVIDER=anthropic|openrouter`. `ChatAnthropic` (default `claude-sonnet-5-5`, configurable). OpenRouter uses `ChatOpenAI(base_url="https://openrouter.ai/api/v1")` with a `:free` model id. Per-role overrides (main vs. subagent). Keep a whitelist of free models verified to do tool calling. Retry and back off on OpenRouter free-tier rate limits, then fall back to the next whitelisted model.
- **Subagents**: `designer` (layouts and critique), `analyst` (sun/noise numbers), `scout` (CoStar + Vexcel area and comps), `shopper` (inventory gaps → product suggestions; later phase).
- **Domain tools**: `get_property_summary`, `list_rooms`, `get_room_geometry`, `sun_report(room, date_range)`, `noise_report(room, hour, materials)`, `area_search(query)`, `get_comps`, `inventory_search`, `propose_layouts(room, item_ids, intent, n=3)`, `record_feedback`.
- **UI tools** emit events over the session WebSocket, so the agent drives the site: `viewer.goto(room|sweep)`, `viewer.set_time(datetime)`, `viewer.set_mode(tour|sun|sound|neighborhood|design)`, `design.show_variants(layout_ids)`, `sound.set_scenario(...)`.
- Streaming: LangGraph `astream` events → WS. Tokens go to the chat dock; tool events go to UI actions.

### 4. Object pipeline (`objects/`, hosted)
Upload an image (Amazon screenshot, product photo, or photo of the user's own room; the phone camera works too). Then:
1. A vision LLM classifies the image type. For product screenshots it OCRs dimensions, price and title.
2. Segment with SAM 3 / Grounded-SAM on fal.ai (text prompts from a furniture class list), producing masks and crops.
3. Generate 3D per crop with SAM 3D Objects or TRELLIS on fal.ai, producing a GLB.
4. **Scale normalization**: use OCR'd dimensions if present, otherwise class-size priors (sofa ≈ 2.1 m, etc.). Then canonicalize orientation (front = +Z) and record the bounding box.
5. A vision LLM extracts attributes (class, style, color, material) and we compute a CLIP embedding. Write `inventory_items`, then append to `/memories/inventory.md`.

Providers sit behind a `Segmenter` / `ImageTo3D` interface, so Replicate or local GPU can replace them later.

### 5. Layout generation (`sim/layout_solver.py`)
Don't trust LLM coordinates. Two stages:
1. The designer subagent writes **semantic constraints** as JSON. Examples: `sofa: against_wall(longest), faces(window|tv)`; `rug: under(sofa front legs)`; clearances.
2. The solver (simulated annealing over item poses) scores collisions, wall adjacency, door and window clearance, an 0.9 m circulation path (grid BFS from doors), facing, and the user's remembered preferences. It returns N diverse variants (diversity penalty between them).

Persist the variants. Render thumbnails with a headless R3F render (Playwright worker). The frontend shows variant cards. Choose, or reprompt ("no TV in the bedroom"). Feedback goes to `preferences.md`, so the next property starts from it.

### 6. Simulation engines (`sim/`)
- **Sun (`sun.py` + client)**: sun position from pvlib on the server (validated) and suncalc on the client (realtime). In the Sun Lab: a directional light with cascaded shadow maps over the interior mesh plus the DSM context mesh, so neighbors and trees block light physically. Server precomputes a per-room annual "direct-sun hours" heatmap (ray casts from floor sample points through openings with trimesh/embree) for agent answers and charts.
- **Noise (`noise.py`)**:
  - Road segments with AADT, an hourly traffic profile and speed by class give an emission level (FHWA TNM / CNOSSOS-lite).
  - Propagation per segment to each facade opening: line source −10·log(d), ground absorption, and barrier loss when the DSM ray from road to window is blocked. Result: L_facade per opening per hour.
  - Indoor level: L_in = L_facade − R(window STC/OITC, wall) + 10·log(S/A), with A = Sabine absorption from room materials plus placed furniture and rugs (per-class absorption table).
  - Output dBA per room per hour, by octave band.
  - **Auralization** (Web Audio): a traffic loop with gain set to the level, a band filter for the transmission-loss spectrum, and a convolver with a synthetic impulse response from the room RT60. Toggles: "double-pane windows", "add carpet", "bedroom vs. living room".
- Engines are pure functions of (PropertyTwin, scenario), so they are easy to test.

### 7. Web app (`apps/web`)
- **Tour tab**: Showcase SDK Bundle embed. Inventory GLBs are placed via `Scene.createObjects` + `mp.gltfLoader`, with poses transformed from twin coordinates. The agent moves the camera with `Sweep.moveTo` / `Camera`.
- **Sun Lab tab**: R3F scene with the interior GLB, DSM context and placed furniture. Time and date scrubber. Fly from outside to inside (MatterFlight-style).
- **Sound tab**: room picker, hourly dBA chart, play/compare audio, material toggles.
- **Neighborhood tab**: Vexcel ortho map plus oblique viewer, CoStar comps and listings, amenity search results.
- **Design tab**: variant cards → apply to Tour and Sun Lab.
- **Agent dock** (all tabs): streaming chat, push-to-talk voice, tool-action chips ("moved you to the kitchen").
- **Voice**: a `SpeechProvider` interface. Default is the browser Web Speech API for STT and speechSynthesis for TTS. Deepgram and ElevenLabs are pluggable.
- **/me**: memory files rendered and editable (with delete for privacy). Inventory grid with 3D preview, segmentation mask overlaid on the source photo, class and attributes. Per-property decision history.

### 8. Phone controller (`/remote/[session]`)
- The desktop shows a QR code (session id + short-lived token). The phone joins the WS session through a Redis pub/sub hub.
- Controls: time-of-day and date scrub (with day-loop playback), room jump, look joystick or gyroscope, push-to-talk mic (phone speech goes to the agent), swipe through layout variants, camera capture into the object pipeline, noise scenario toggles.
- Events are typed in `packages/schema`, e.g. `viewer.setTime`, `viewer.goto`, `layout.select`, `capture.upload`, `voice.transcript`.

---

## Phased roadmap
0. **Spike and foundations.** Monorepo, docker-compose, env config, cached integration clients. Verify against the real model:
   - Matterport GraphQL access (rooms, mesh download, georef) for `onXQLXhfyQv`
   - Vexcel coverage at its location
   - which CoStar datasets join to its address
1. **Twin + Tour + Agent v1.** Ingestion steps 1, 2 and 5. Showcase embed. Chat agent with `/memories` on PostgresStore, Claude + OpenRouter factory. UI tools for `goto` and `set_mode`.
2. **Objects + Design.** Hosted segmentation and 3D, inventory, `/me` page, layout solver, variant cards, placement in Showcase, feedback written to memory.
3. **Sun Lab.** Openings annotation, DSM context mesh, realtime shadows, sun-hours precompute, `sun_report` tool.
4. **Phone + voice.** Pairing, controller, speech providers.
5. **Sound.** Roads/AADT, noise engine, auralization, material toggles, `noise_report` tool.
6. **Area + shopper + generalize.** CoStar vibe profile, comps, shopper subagent. Run ingestion on a second property to prove generality. Cross-property memory demo ("your sofa in this new house").

---

## Verification
- **Unit**:
  - sun position vs. NOAA calculator reference values
  - noise formulas vs. hand-calculated cases (doubling distance from a line source = −3 dB; STC change)
  - layout solver invariants (no overlaps, clearances met, all items inside the polygon)
  - ICP alignment against a synthetic rotated footprint
- **Integration**: replay recorded fixtures for Matterport, Vexcel, CoStar and fal, so CI never hits live APIs.
- **Agent evals** (pytest, run against Claude and each whitelisted OpenRouter model):
  1. Thread A: user states preferences and uploads a sofa. Thread B (new thread, same user) asks for a layout. Assert the sofa and preferences are used without restating.
  2. User asks "how sunny is the bedroom in December?". Assert the agent calls `sun_report` and `viewer.set_time`.
  3. The memory namespace is isolated between two user ids.
- **E2E (Playwright)**: load `/house/onXQLXhfyQv`. A mock phone client joins the session and sends `viewer.setTime`; assert the Sun Lab light direction changes. Upload a sample product image; assert a GLB appears in `/me`. `propose_layouts` returns 3 variants; select one; assert the object exists in the Showcase scene.
- **Manual demo script**: speak to the agent from the phone, scrub the sun, play bedroom traffic noise with single- vs. double-pane windows, reprompt a layout, then open a second property and confirm the agent remembers.
