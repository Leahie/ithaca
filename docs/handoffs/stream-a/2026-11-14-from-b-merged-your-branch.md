---
from: B
to: A
date: 2026-11-14
status: open
source: stream-b/agent-v0 (merge of stream-a-web @ 0787159)
---
# B merged `stream-a-web`: one schema now, and three small changes to connect to the real server

**Why:** A and B each wrote `packages/schema/{events,session}.py`, and A and C each wrote
`reports.py`. B merged all three branches and reconciled them by owner (I-G1). Your web app does
not connect to the real API yet; three client changes fix that.

## What you need to do
- [ ] **Merge `stream-b/agent-v0`** (it contains `stream-c/twin-v0` too). Take B's
      `packages/schema/` as is. `ts/index.ts` is regenerated: every type name you had is still
      there; new ones are added (list below).
- [ ] **Send the property when creating a session.** `POST /api/sessions` needs the body
      `{"property_id": "..."}`. `http.ts` `createSession` sends no body today, which returns 422.
- [ ] **Use `client_token` on the WebSocket, not `pair_token`.** `POST /sessions` returns
      `client_token` for the desktop. `POST /sessions/{sid}/join` returns `{ok, client_token}` for
      the phone. In `SessionProvider.tsx` pass that to `WsTransport`. The pair token is single use
      (B10), so it can't also be the reconnect credential.
- [ ] **Read the earlier notes in this folder.** Your branch started before `docs/handoffs/`
      existed: `from-b-agent-events`, `from-b-hub-and-scripted-agent`, `from-b-tour-observer`,
      and C's two notes.
- [ ] **Run codegen from the repo root** if `pnpm schema:codegen` fails: `codegen.py` now imports
      `packages.schema.*` (C's `reports.py` uses absolute imports). It puts the repo root on
      `sys.path` itself, so `cd packages/schema && python codegen.py` still works with pydantic
      installed.

## Details

### How the schema was reconciled
| File | Kept | Notes |
|---|---|---|
| `events.py`, `session.py` | B's, renamed to **your** class names (`ViewerGotoPayload`, `EventEnvelope`, …) | So your imports keep working. `EVENT_PAYLOADS` and `UI_TOOL_EVENTS` are still exported. |
| `reports.py`, `fixtures/sun/reference.json` | C's | C owns them. C's `SunReport.heatmap` is required-but-nullable. |
| `inventory.py`, `layout.py`, `common.py`, `codegen.py`, `ts/` | yours | Thank you; B takes over owning them. |
| `.env.example` | yours + B's section | |

### New in `ts/index.ts`
`AgentTurnPayload` (event `agent.turn`), `CreateSessionRequest`, `JoinResult`, `Session.client_token`,
and C's sun/daylight types (`SunMoment`, `SunRoomDay`, `SunSpot`, `DaylightMoment`, `DaylightSpot`, …).

### Server facts
- `ViewerState.datetime` is always set (the property's local time when the session starts).
- The WebSocket answers on both `/ws/sessions/{sid}` and `/api/ws/sessions/{sid}`.
- The scripted agent's "show layouts" now sends the ids from `fixtures/layouts/<pid>.json`.
- B has no `GET /inventory`, `GET /layouts`, `/memory` or `/uploads` routes yet. Keep those on
  fixtures.
- There are two schema exports for now: your `codegen.py` (TS) and `export.py` (JSON Schema, which
  C's notes mention). TS is the one that counts for you.
