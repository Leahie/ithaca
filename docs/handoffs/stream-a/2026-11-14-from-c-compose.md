---
from: C
to: A
date: 2026-11-14
status: open
source: stream-c/twin-v0
---
# docker compose builds your web image

**Why:** `compose.yaml` at the repo root (milestone M0) builds `apps/web/Dockerfile` as the `web`
service next to the API, Postgres, Redis and S3 storage. It uses your Dockerfile unchanged.

## What you need to do
- [ ] **`apps/web/Dockerfile` does not build on `stream-a-web` @ 0787159.** `pnpm install
      --frozen-lockfile` stops with `ERR_PNPM_IGNORED_BUILDS: Ignored build scripts:
      @matterport/sdk@1.6.2`. Add `'@matterport/sdk': true` (or `false` if its script isn't
      needed) under `allowBuilds` in `pnpm-workspace.yaml`. Local installs likely passed because the
      build was approved once by hand. Check: `docker build -f apps/web/Dockerfile .`
- [ ] **Add build ARGs for the settings you want in the compose image.** Compose can only pass
      what the Dockerfile declares. Today these reach the image: `NEXT_PUBLIC_USE_FIXTURES`,
      `NEXT_PUBLIC_API_BASE`, `NEXT_PUBLIC_WS_BASE`, `NEXT_PUBLIC_MATTERPORT_SDK_KEY`,
      `NEXT_PUBLIC_DEFAULT_PROPERTY_ID`, `NEXT_PUBLIC_USER_ID`, `NEXT_PUBLIC_PUBLIC_ORIGIN`,
      `API_PROXY_TARGET`. Not yet: `NEXT_PUBLIC_FEATURES`, `NEXT_PUBLIC_MATTERPORT_VIEWER`,
      `NEXT_PUBLIC_MATTERPORT_SPLATS`, `NEXT_PUBLIC_SPEECH_PROVIDER`, `NEXT_PUBLIC_HAND_MODEL_URL`.
      Once you add an ARG, add the same line under `web.build.args` in `compose.yaml`
      (`NAME: ${NAME:-default}`), or tell C.

## Details
- **Where values come from:** compose reads the **root** `.env` for these build args, not
  `apps/web/.env.local`. They are compiled in, so after a change: `docker compose up --build web`.
- **Fixture mode:** `NEXT_PUBLIC_USE_FIXTURES=1` (the `.env.example` default) gives the M0
  fixture-mode check. Leave it empty for the full stack; then `/api` is proxied to
  `API_PROXY_TARGET=http://api:8000` inside the compose network.
- **WebSocket:** the API is published on host port 8000, which is where your client connects when
  `NEXT_PUBLIC_WS_BASE` is empty. If someone moves it with `API_PORT`, they set
  `NEXT_PUBLIC_WS_BASE=ws://localhost:<port>`.
- **Fixtures** are mounted read-only at `/repo/fixtures`, as your Dockerfile comment asks.
- **Ports:** web on 3000 (`WEB_PORT` moves it).
