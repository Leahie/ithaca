---
from: B
to: A
date: 2026-11-14
status: open
source: stream-b/agent-v0
---
# The API server, session WebSocket and scripted agent are ready to build against

**Why:** B's Day 1 mocks for A now exist: a real hub, the event schema, and a fake agent that needs
no API key. You can build the agent dock, the viewer reactions and the mock phone against them.

## What you need to do
- [ ] **Generate your TS types from `packages/schema/json/*.schema.json`** (events, session, twin)
      into `packages/schema/ts/`. `events.schema.json` has an `x-event-types` map: event type ->
      payload model and who may send it. B regenerates the JSON with
      `python -m packages.schema.export`; you own the JSON -> TS step (I-G5).
- [ ] **Connect the desktop** as described below, and build `mock-phone.ts` against the phone flow.
- [ ] **Drop your own events by `id`.** The hub echoes every event to the sender too (B9). Set
      `id` on what you send; the server keeps it.
- [ ] **Rebuild viewer state from `session.state`**, which is always the first message after
      connecting (also after a reconnect).

## Details

### Run it
```bash
uv venv --python 3.12 .venv
uv pip install -p .venv -r services/api/requirements-b.txt -r services/api/twin/requirements.txt
AGENT_PROVIDER=scripted .venv/bin/uvicorn services.api.main:app --reload --port 8000
curl localhost:8000/api/health     # {"ok": true, "agent_provider": "scripted"}
```
No keys needed. CORS is open for dev.

### Desktop flow
1. `POST /api/sessions` with body `{"property_id": "onXQLXhfyQv"}` and header `X-User-Id`
   (default `demo`) -> `{session_id, pair_token, expires_at, client_token}`.
2. Open `ws://localhost:8000/api/ws/sessions/{session_id}?client=desktop&token={client_token}`.
3. First messages: `session.state`, `session.joined`, then the agent's greeting turn.
4. Show `session_id` + `pair_token` as the QR code. The pair token works once and for 10 minutes.
   `POST /api/sessions/{session_id}/pair` gives a fresh one.

### Phone flow
1. `POST /api/sessions/{session_id}/join` with `{"pair_token": "…"}` -> `{ok, client_token}`
   (403 if the token is wrong, used or expired).
2. Open the same WebSocket URL with `client=phone&token={client_token}`. Keep the client token to
   reconnect.

A wrong token or client kind is refused before the socket opens (HTTP 403 on the upgrade).

### Sending
Send `{"type": "...", "payload": {...}, "id": "<uuid>"}`. The server fills in `v`, `ts`,
`session_id` and `source`. Unknown types and bad payloads are dropped and logged; the connection
stays open. `chat.user`, and `voice.transcript` with `final: true`, start an agent turn.

### Scripted agent prompts
| Say | You get |
|---|---|
| "take me to the kitchen" (any room label) | `viewer.goto {room_id}` |
| "show me December morning" | `viewer.setMode sun`, `viewer.setTime 2026-12-21T08:00:00-06:00` |
| "show layouts" | `viewer.setMode design`, `design.showVariants` with ids `00000000-0000-4000-8000-00000000000{1,2,3}` |

Each turn: `agent.turn started`, `agent.action` chips, the UI events, `agent.token` deltas,
`agent.message`, `agent.turn ended`. The layout ids have no `GET /layouts` behind them yet.

### Additions to the v0 contract (all additive, I-G6)
- `Session.client_token`, and `client_token` in the join response.
- `POST /sessions` takes `property_id`. New route `POST /sessions/{sid}/pair`.
- Event `agent.turn {status}`.
- `sound.setScenario.scenario` is an untyped object until C ships `NoiseScenario`.

### Not built yet
Redis (one API process only for now), `POST /uploads`, inventory, layouts, memory routes,
`job.progress`.
