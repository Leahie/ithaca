---
from: C
to: B
date: 2026-11-14
status: open
source: stream-c/twin-v0
---
# docker compose, Postgres, Redis, S3 storage and Alembic are up

**Why:** the Phase 0 infra (WORKSTREAMS.md section 6, milestone M0) exists now. `docker compose up`
starts Postgres with pgvector, Redis, S3 storage, a one-shot `init` step, the API and the web app.
Nothing in B's code has to change today; this is what you can rely on when you add tables, move
the hub to Redis or write the worker.

## What you need to do
- [ ] **Add your env keys from below to `.env.example`** on your branch (shared file), or take ours
      at the next merge.
- [ ] **New tables:** add Alembic revisions under `infra/migrations/versions/`, on top of the
      current head (`0001_base` today). One linear chain for everyone (I-G4):
      `alembic -c infra/alembic.ini revision -m "b: inventory items"`.
- [ ] **The arq worker:** tell C the module path (or add it yourself, C reviews `compose.yaml`).
      It becomes one more service on the same image: `<<: *python`, `environment: <<: *backend-env`,
      `command: arq <your WorkerSettings path>`, `depends_on: init`.

## Details
- **Run:** `docker compose up -d init` starts only the data services and runs migrations, for when
  you run uvicorn on the host. `docker compose up` runs everything. The API service runs
  `uvicorn services.api.main:app --reload` with the repo mounted at `/repo`, so edits reload.
- **Python deps:** the image (`infra/api.Dockerfile`) installs every `services/**/requirements*.txt`
  plus `infra/requirements.txt`, so `services/api/requirements-b.txt` is picked up as is. After you
  add a dependency, run `docker compose build api`.
- **Env names** (values for code on the host; inside compose the hosts are `postgres`, `redis`, `s3`):
  - `DATABASE_URL=postgresql+psycopg://ithaca:ithaca@localhost:5432/ithaca`. This is SQLAlchemy's
    form. LangGraph's `PostgresStore` / `PostgresSaver` take plain libpq URLs: drop the `+psycopg`.
  - `REDIS_URL=redis://localhost:6379/0`
  - `S3_ENDPOINT_URL=http://localhost:9000`, `S3_PUBLIC_URL` (base URL the browser uses),
    `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET=ithaca`.
- **Object storage is RustFS, not MinIO.** MinIO no longer publishes its Docker images (pulls from
  Docker Hub and quay.io are denied). RustFS speaks the same S3 API. Use boto3 with path-style
  addressing (`Config(s3={"addressing_style": "path"})`); `infra/s3_init.py` is a working example.
  The bucket is private: hand the browser presigned URLs.
- **Postgres** is `pgvector/pgvector:pg17`; `0001_base` enables the `vector` extension (0.8.7) for
  your CLIP embeddings.
- **Not changed:** the hub is still in memory and memory/checkpoints still use your SQLite setup.
  Moving them is your call.
