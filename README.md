# Ithaca

Live in a home before you buy it. Ithaca holds a digital twin of a house and lets a buyer, and
their family, find out what living there would be like:

- **Walk through it**: every room at eye height, rebuilt from the scan and the floor plan, with
  the outside of the house and the block around it.
- **Watch the sun**: any day of the year, any hour, with real shadows from the neighbours' houses
  and trees; sun hours per room.
- **Hear it**: the highway, the street, birds and frogs by hour and season; windows open or
  closed, carpet or not; a TV placed in one room, heard from another.
- **Know the neighbourhood**: the listing next to comparable homes, and what is nearby.
- **Move your furniture in**: photograph a piece and it is rebuilt in 3D at its real size; arrange
  rooms by hand (mouse, phone, or your hands in front of the webcam), or keep a layout the guide
  proposes.
- **Bring the family**: one QR code on the big screen. Each person's phone becomes a remote with
  its own guide, which remembers what that person needs and compares notes with the others.

It is one web app (`apps/web`, Next.js) on one API (`services/api`, FastAPI): the twin and its
simulations, the guide (an LLM agent with tools and per-person memory), the session hub that
keeps the big screen, the phones and the guides in step, and the photo-to-furniture pipeline.

## Run it

```sh
pnpm install
git lfs pull                 # the house model and textures
cp .env.example .env         # then add keys: see below
pnpm dev:all                 # API on :8000, web on :3000, both reachable from phones on your Wi-Fi
```

Open <http://localhost:3000>, then **Step inside**. To connect phones, press **Family** on the
house page and scan the code. `scripts/dev.sh` prints the addresses and what to do if a phone
cannot reach the page. On Windows: `pnpm dev:win`.

If a phone cannot reach this computer (a firewall here, or Wi-Fi that keeps devices apart, such
as eduroam or venue Wi-Fi), run `pnpm dev:all:tunnel` instead (Windows: `pnpm dev:tunnel`). It
needs `cloudflared`, and points the QR code at a public HTTPS address that works from any
network and lets the phone use its microphone. Anyone who has that address can reach the app
while it runs.

Keys in `.env` (each is optional; a missing key turns that part down, never the app off):

| Key | Without it |
|---|---|
| `ANTHROPIC_API_KEY` (or `OPENROUTER_API_KEY`) | The guide is a scripted stand-in |
| `DEEPGRAM_API_KEY` | Speech uses the browser's own voice and recognition |
| `FAL_KEY` | A photo becomes a ready-made catalog model of that kind of piece, not a 3D model of yours |
| `MATTERPORT_*`, `VEXCEL_API_KEY` | The committed twin is used as it is; no re-ingestion, no scan photos |

Offline, with no API at all: set `NEXT_PUBLIC_USE_FIXTURES=1` in `apps/web/.env.local` and run
`pnpm dev`. Every view runs from `fixtures/`, with a scripted guide.

With Docker: `docker compose up` (web, API, Postgres, Redis, S3 storage).

## Where things are

| Path | What |
|---|---|
| `apps/web` | The site: front page, house page, phone remote, `/me`. See its [README](apps/web/README.md) |
| `services/api` | The API: `twin/` and `sim/` (sun, daylight, noise, layout solver), `agent/`, `realtime/` (hub, households), `objects/` (photo to furniture), `furniture/` |
| `packages/schema` | Shared types: pydantic models, with generated TypeScript |
| `fixtures` | The committed twin, reports and 3D assets for one house |
| `docs` | Design language (`style.md`), the furniture rules (`specs/furniture.md`), cross-team notes |

`PLAN.md` is the original plan; `WORKSTREAMS.md` is the contract between the three workstreams.

## Checks

```sh
pnpm typecheck
pnpm test
pnpm schema:check     # generated TypeScript types match the pydantic models (needs uv)
```

Sun, sound and layout figures are simulations from the scan and public data: estimates, not
measurements.
