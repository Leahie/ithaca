---
from: B
to: A
date: 2026-11-14
status: open
source: stream-b/agent-v0 (merge of stream-a-web @ 0787159)
---
# B merged `stream-a-web`: one schema now, and the server accepts your connection flow

**Why:** A and B each wrote `packages/schema/{events,session}.py`, and A and C each wrote
`reports.py`. B merged all three branches and reconciled them by owner (I-G1). B then changed the
server to accept the session flow your app already uses (your README, "For Stream B", items 2-3).

## What you need to do
- [ ] **Merge `stream-b/agent-v0`** (it contains `stream-c/twin-v0` too). Take B's
      `packages/schema/` as is. `ts/index.ts` is regenerated: every type name you had is still
      there; new ones are added (list below).
- [ ] **Send the property when creating a session:** `POST /api/sessions` with body
      `{"property_id": "..."}`. Without a body the server uses `DEFAULT_PROPERTY_ID`, or the only
      twin it has. That works for one house and returns 422 once there are two.
- [ ] **Read the earlier notes in this folder.** Your branch started before `docs/handoffs/`
      existed: `from-b-agent-events`, `from-b-hub-and-scripted-agent`, `from-b-tour-observer`,
      and C's two notes.
- [ ] **Handle `agent.turn {status: "started" | "ended"}`** for the typing indicator. A turn can
      end with no `agent.message` (the agent may choose to stay silent).

### Three edits B made in your files (review them when you merge)
- `pnpm-workspace.yaml`: `allowBuilds['@matterport/sdk']: true`. Without it a fresh `pnpm install`
  stops with `ERR_PNPM_IGNORED_BUILDS` (pnpm 12 writes a placeholder line and fails).
- `apps/web/src/lib/store/reducer.ts`: a `case 'agent.turn'` so the exhaustive switch compiles.
  It stores nothing; wire it to the typing indicator as you like.
- `apps/web/src/lib/sun/position.test.ts`: reads C's `fixtures/sun/reference.json`, whose list is
  `cases` (24 entries), not `samples`. The 0.5 degree check is unchanged and passes.

With these, `pnpm typecheck` passes and `pnpm test` passes except the five GLB tests, which need
`git lfs pull`.

### Your README items, answered
| # | Your reading | B's answer |
|---|---|---|
| 2 | Desktop and phone open the WebSocket with `pair_token`; single use = one join | **Accepted and built.** The desktop may use any pairing token issued to its session; the phone may use one after a successful join. `client_token` (returned by create and join) also works. Pairing tokens still expire for joining after 10 minutes. |
| 3 | Send `session.joined` for clients already present | **Built.** A new client gets `session.state`, then one `session.joined` per client already there, then its own. |
| 8 | `voice.transcript` with `final: true` starts a turn | **Built.** An empty final transcript is not filtered yet: don't send one. |
| 1, 4, 5, 6, 9, 11 | Layout, memory, upload routes | Not built yet. B will follow your readings when building them, including `GET /layouts/{id}`. |

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
- The WebSocket answers on both `/ws/sessions/{sid}` (what you use, port 8000) and `/api/ws/sessions/{sid}`.
- The scripted agent's "show layouts" now sends the ids from `fixtures/layouts/<pid>.json`.
- B has no `GET /inventory`, `GET /layouts`, `/memory` or `/uploads` routes yet. Keep those on
  fixtures.
- There are two schema exports for now: your `codegen.py` (TS) and `export.py` (JSON Schema, which
  C's notes mention). TS is the one that counts for you.
