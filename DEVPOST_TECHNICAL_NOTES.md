# Ithaca: technical specifics for the Devpost (from reading the repo)

Everything below was read from the code. File paths are given so you can double-check a claim before
it goes in the writeup. Items marked **CHECK** are things your draft says that the repo doesn't back up
(or backs up differently).

## Scale (for a one-line brag)
- ~52k lines across the repo (excluding data and fixtures): Python backend (FastAPI), Next.js 16 / React 19 / Three.js 0.184 frontend.
- 155 Python tests + 33 TypeScript tests. Engines are pure functions, so they're testable and replayable from recorded fixtures.
- Three parallel workstreams (A web, B agent, C twin/data/simulation) coordinating through written handoff notes (`docs/handoffs/`).

---

## 1. Digital twin of a real house (Matterport + point cloud + Vexcel)
- **Matterport Model API (GraphQL)**: floors, rooms, wall graph, openings, scan positions (sweeps), georeference, skybox panoramas, MatterPak mesh and point cloud (`integrations/matterport.py`, `twin/ingest.py`).
- `twin/ingest.py` builds a `PropertyTwin` keyed only by property id (deterministic, byte-identical JSON; missing sources degrade a `quality` field instead of failing).
- Room outlines are rebuilt from Matterport's wall graph by finding the faces the edges enclose (`twin/rooms.py`), so wall stubs don't break polygons. Doors/archways are classified; hidden "Void" spaces become stair/floor openings.
- **Point cloud -> real geometry** (`twin/cloud.py`, `twin/reconstruct.py`): the ~46M-point, 1.9 GB MatterPak cloud is streamed out of a remote zip, rotated into our frame and voxel-thinned without touching disk. Matterport's own OBJ is a low-poly "dollhouse" proxy, so we rebuild surfaces with **screened Poisson reconstruction** (Kazhdan & Hoppe 2013): normals from local plane fits, flipped toward the scan position that saw each point, low-density skin trimmed.
- **Photo-guided depth fusion** (`infra/recon/fuse_depth.py`): Prompt Depth Anything (monocular depth prompted by the cloud's metric depth), fitted back to the cloud in windows with a guided filter (He et al. 2013), then fused in a **TSDF** (Curless & Levoy 1996). The cloud is about 1 cm fine; the 4k photos are about 1 mm/pixel.
- **Scan-point navigation graph** (`twin/nav.py`): Matterport doesn't expose which sweeps link, so we rebuild it by ray-casting a body-wide corridor at eye height plus a knee-height ray against the mesh, so you never walk through a wall.
- **Panoramas** (`twin/panos.py`): skybox faces re-cut into GL cube faces, orientation convention verified against point-cloud colors (side-face correlation 0.70 vs <=0.2 for any other). Defurnished (empty-room) panoramas are merged with the furnished 4k faces: a hybrid 4k face where the 2k defurnished image replaces only the regions that differ.
- **Photo-projected rendering** (Sun Lab): at a scan point the camera only rotates and each mesh fragment samples that sweep's skybox, so the photo is exact; the mesh supplies depth and shadows, with daylight modulating the photo.
- **Gaussian splat outside** (`twin/splat.py`): Matterport's aerial drone splat (~6M Gaussians, ~5.8M rendered through Spark with a level-of-detail tree). Aligned to the interior frame with a similarity transform (Umeyama 1991): roofs onto the house outline in 2D, ground height from the point cloud, then ICP with scale. The house is cut out of the splat since the scan is the house.
- **COLMAP export for splat training** (`twin/gs_export.py`): each sweep's skybox becomes six 90-degree pinhole cameras with poses taken straight from the twin (no structure-from-motion), plus the point cloud as the initial points. We ran 2DGS training on a DGX Spark (`infra/recon/spark_train.sh`).
- **Vexcel**: DSM (digital surface model, float32 GeoTIFF, ~7.5 cm pixels) and property/footprint endpoints (`integrations/vexcel.py`). Used in `twin/structure.py` to find the roof height offset (DSM height minus interior y, from open ground cells the outdoor sweeps scanned) and align the building envelope. **CHECK**: the sun engine docstring says neighbors/trees from the DSM are *not* modeled yet, so don't claim neighbor shadows from Vexcel unless that changed elsewhere.

## 2. Light simulation
- **Sun** (`sim/sun.py`): pvlib solar position; ray from each floor sample point (floor / seated / standing height) toward the sun, tested against the room's real exterior windows and sliders, with L-shaped-room line-of-sight handling. Gives direct-sun intervals for any spot, room and date range, plus a mean-hours-per-day heatmap.
- **Daylight** (`sim/daylight.py`), lux at any time and weather:
  - Sky: **Perez all-weather model** (Perez, Seals & Michalsky 1993) on **145 Tregenza patches**.
  - Per-room daylight coefficients (4x4 sub-rays per patch, angle-dependent glass transmission via Rivero's approximation), computed once so any time/weather is a matrix product (the Radiance two-phase idea).
  - Clear sky: Ineichen-Perez with Linke turbidity; **typical-year weather from NSRDB TMY** via pvlib.
  - **Inter-reflection**: BRE split-flux method (Hopkinson, Longmore & Petherbridge 1954), i.e. the "reflective effect" in your draft.
  - Brightness bands from **Useful Daylight Illuminance** (Nabil & Mardaljevic 2005).
  - **Validated against Radiance** (`twin/radiance_check.py`, via pyradiance's `gendaylit` + `rtrace`).
- **CHECK**: your draft says "the sun's direct and reflective effect" with "xxx formula name". The names above are the answer: Perez sky + BRE split-flux for reflection.

## 3. Sound simulation
- **Outdoor noise** (`sim/traffic.py`): **CNOSSOS-EU** road emission (Directive 2002/49/EC), **FTA 2018** rail, **ISO 9613-2** propagation (divergence, air absorption, porous-ground, housing attenuation, plus the single most effective building edge, Maekawa/D_z) using OpenStreetMap building footprints as barriers. Real traffic counts from **TxDOT AADT**, with default volumes by road class elsewhere. Per octave band, per hour, per outside wall. Background from ANSI S12.9-3.
- **Indoor** (`sim/noise.py`): statistical room acoustics as one linear solve. Each room is a diffuse field with Sabine absorption (surfaces, furnishings, air); every path (through walls, round via hall, through the floor, stairwells) couples rooms in one symmetric matrix, so results are reciprocal. Gives Leq, Lden, A-weighted levels and octave bands per room per hour, and levels from a source placed in any room.
- **Soundscape** (`sim/soundscape.py`): beyond traffic, models dogs and horns (SEL-based events), and biophony (birds with dawn chorus, cicadas, crickets, katydids, frogs) on a habitat grid from OSM (water, woods, trees, parks), with species lists from **iNaturalist** and hourly temperature/rain from **Open-Meteo (ERA5)**. Activity depends on sunrise/sunset, season, temperature and, for frogs, rain.
- **Auralization in the browser** (`apps/web/src/lib/audio/`): Web Audio, no recordings. Pink noise through eight octave-band filters set from the report's band levels, master gain from dBA, a synthetic impulse response from the room's RT60. Birds, cicadas, frogs, dogs and passing cars are synthesized from oscillators and noise. Toggle outdoor sources and hear the room change by the hour.

## 4. Photo -> furniture -> room (the CoStar "furnish an empty room" angle)
- `objects/pipeline.py`: photo (or a phone video; `objects/video.py` picks the sharpest frame from each stretch) -> **vision LLM** reads what's in it plus any printed dimensions/price/title -> **SAM 3** (fal.ai) cuts each piece out (local fallback: Grounding DINO + SAM 2.1) -> **SAM 3D Objects** (or TRELLIS) generates a GLB (~$0.02 a piece; live-tested ~2 s segmentation and ~30 s 3D) -> `normalize.py` rescales to meters, Y-up, front = +Z, origin at bottom center, and **rejects any asset that can't be brought within 2 cm of its dimensions**. Each piece succeeds or fails on its own with a reason.
- Fallback providers behind one interface: fal / catalog (free CC0 Poly Haven models sized to the photo's dimensions) / stub boxes.
- **Layout solver** (`sim/layout_solver.py`): the LLM never outputs coordinates. The designer states wishes ("sofa against the longest wall, facing the window"); simulated annealing over (x, z, yaw) with random restarts finds poses. Hard rules are never broken: inside the room, no overlaps, door clearance, no tall pieces in front of windows, and a 0.9 m walkable path between doors. Returns several visibly different layouts (difference measured by mean distance moved, a turn counting as 1 m) and says why if it finds fewer. Scan-derived walking routes feed in. Dragging a piece is validated and rejected with a plain-language reason.
- **CoStar Data Kit** (`integrations/costar.py`, `twin/area.py`): residential listing for the model, listing features, nearby comps, plus OSM amenities, assembled into a markdown area profile the agent reads. **CHECK**: the code I read uses the residential listings, listing features and nearby/comps data. Apartment, commercial and company datasets are in `data/` but I didn't find them wired in, so avoid claiming "all of CoStar's APIs" unless that's true elsewhere.

## 5. The agent (LangChain deepagents)
- `create_deep_agent` (deepagents >= 0.5, LangGraph >= 1.0), default **Claude Sonnet 5.5** via a model factory with per-role overrides and OpenRouter as an option. Same graph runs in a REPL (SQLite) and the API.
- **Not a chatbot: the agent drives the app.** Tools split into:
  - Knowledge: `list_rooms`, `get_room_geometry`, `sun_by_room`, `sun_report`, `daylight_at`, `area_profile`, `area_search`, `sound_heard_from`, `inventory_search`, `catalog_search`.
  - UI actions that emit typed events over the session WebSocket: `viewer_goto`, `viewer_walk`, `viewer_step`, `viewer_turn`, `viewer_zoom`, `viewer_set_mode`, `viewer_set_time`, `sound_place_source`, `design_show_variants`, `design_apply_layout`, `remove_from_room`.
  - Every tool argument is an id or datetime validated against the twin before anything is emitted. The agent never invents coordinates.
- **Background designer agent**: a second `create_deep_agent` that the guide launches with `design_in_background`, so the guide keeps talking and moving you through the house while layouts solve. Its progress streams back as UI events.
- **Observer agent**: a third deep agent that never talks to the user. It reads a log of how you tour (rooms, dwell time, views, what you said), checks patterns against the house (sun, room sizes), writes **inferred** preferences to memory, and may hand the guide a short note. It's a separate graph rather than a subagent so the user never waits on it.
- **Proactive**: the agent bridge (`realtime/agent_bridge.py`) turns events such as "property opened" or "room entered" into agent turns, with a cooldown and busy-skip so it doesn't talk over you. Replies are interruptible and describe what it's doing as it acts.
- **Memory** (deepagents `CompositeBackend`): `/memories/**` goes to a `StoreBackend` namespaced by `user_id`, everything else to per-thread scratch state. Files: profile, preferences, inventory (derived from the DB; the agent is **denied write access** to it), and one notes file per property. Memory persists across threads and across houses.
- **Family isolation** (your "memory separate between families" challenge): each household member has their own agent running under their own `member_id` namespace. Agents cooperate only through a shared **family board** of notes an agent *chooses* to post, or by asking another member's agent a single question (run under that member's context; the asked agent can't ask anyone in turn). Nothing reads another member's namespace.
- **Voice**: Deepgram Aura TTS and Nova-3 streaming STT through the API (key stays server-side), with a browser Web Speech fallback behind one `SpeechProvider` interface. A lot of work went into phone mics: noise gate, no guessing when to open the mic, time limit, iOS audio-context quirks.

## 6. Multimodal input and the second screen
- **Hand tracking**: MediaPipe HandLandmarker in a Web Worker (frames transferred as ImageBitmap, dropped if the worker is busy so rendering never stalls). 1-euro filter plus velocity prediction so the cursor runs at display rate, pinch detection in palm-length units with enter/leave hysteresis. One pinching hand drags the view; two pinching hands zoom. Ported from the team's HoloTouch project.
- **Phone as controller** (`/remote/[session]`, PWA): QR pairing with single-use 10-minute tokens, a session hub that delivers every event in order to every client and replays the latest state on join, a look stick and move pad, time-of-day control, room jump, push-to-talk, furniture arranging and photo upload from the phone. Family members each pair a phone via a reusable family QR.
- All of it travels as typed events defined once in `packages/schema` (pydantic is the source, TypeScript types generated from JSON Schema).

## 7. Engineering practices worth one line
- Every integration client records responses to fixtures and replays them, so CI never hits a live API and other teammates run with no keys.
- Pure-function engines checked against references: pvlib, Radiance, hand-calculated acoustics cases.
- API keys never reach the browser except the Matterport SDK key.

---

## Corrections to the current draft
1. "Using the photos we applied SAM to segment the individual objects and create a 3D model **of the space**": SAM segments *furniture* from photos and SAM 3D builds *objects*. The space itself comes from the Matterport scan: wall graph, point cloud, Poisson reconstruction, depth fusion, plus the aerial Gaussian splat.
2. "Combined [Vexcel and CoStar] data to get the overall floor layout": the floor layout comes from Matterport. Vexcel contributes DSM/footprint alignment, and CoStar the listing, comps and area profile.
3. "Estimate the effect of outside noise on the interior": true, and worth naming: CNOSSOS-EU + ISO 9613-2 outside, a coupled-room statistical acoustics solve inside.
4. Challenge "3D model with gaussian splat and point cloud": concrete version is aligning the drone splat to the interior frame (Umeyama + ICP with scale) and rebuilding surfaces by Poisson, since Matterport's mesh is a low-poly proxy.
5. Possible "Built with" tags: Next.js, React, Three.js, React Three Fiber, Spark (Gaussian splats), Matterport Model API + Showcase SDK, Vexcel, CoStar Data Kit, LangChain deepagents, LangGraph, Claude, FastAPI, Python, pvlib, Radiance, trimesh, Open3D, MediaPipe, Web Audio API, Deepgram, fal.ai (SAM 3, SAM 3D Objects), OpenStreetMap, TxDOT, iNaturalist, Open-Meteo, NSRDB, Postgres, Redis, Docker.
