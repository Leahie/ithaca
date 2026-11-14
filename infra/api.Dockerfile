# Python image for the API, the migrations and (later) the worker. Build context: the repo root.
# Only requirement files are sent to the build (infra/api.Dockerfile.dockerignore), so the
# dependency layer rebuilds only when a requirements file changes. Compose mounts the code at /repo.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1

COPY . /tmp/req
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install $(find /tmp/req -name 'requirements*.txt' -printf '-r %p ') \
    && rm -rf /tmp/req

WORKDIR /repo
