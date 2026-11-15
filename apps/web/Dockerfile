# Web image for docker compose (M0). Build context: the repo root.
#   docker build -f apps/web/Dockerfile -t ithaca-web .
#
# NEXT_PUBLIC_* values are compiled into the client bundle, so they are build args.
# In fixture mode, mount the fixtures folder: -v ./fixtures:/repo/fixtures:ro
FROM node:24-slim AS base
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH NEXT_TELEMETRY_DISABLED=1
RUN corepack enable
WORKDIR /repo

FROM base AS deps
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
COPY apps/web/scripts/copy-assets.mjs apps/web/scripts/
COPY packages/schema/ts/package.json packages/schema/ts/
RUN pnpm install --frozen-lockfile

FROM deps AS build
ARG NEXT_PUBLIC_USE_FIXTURES=""
ARG NEXT_PUBLIC_API_BASE="/api"
ARG NEXT_PUBLIC_WS_BASE=""
ARG NEXT_PUBLIC_DEFAULT_PROPERTY_ID=""
ARG NEXT_PUBLIC_USER_ID="demo"
ARG NEXT_PUBLIC_PUBLIC_ORIGIN=""
ARG NEXT_PUBLIC_FEATURES="all"
ARG NEXT_PUBLIC_SPEECH_PROVIDER="webspeech"
ARG NEXT_PUBLIC_HAND_MODEL_URL=""
ARG API_PROXY_TARGET="http://api:8000"
ENV NEXT_PUBLIC_USE_FIXTURES=$NEXT_PUBLIC_USE_FIXTURES \
    NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE \
    NEXT_PUBLIC_WS_BASE=$NEXT_PUBLIC_WS_BASE \
    NEXT_PUBLIC_DEFAULT_PROPERTY_ID=$NEXT_PUBLIC_DEFAULT_PROPERTY_ID \
    NEXT_PUBLIC_USER_ID=$NEXT_PUBLIC_USER_ID \
    NEXT_PUBLIC_PUBLIC_ORIGIN=$NEXT_PUBLIC_PUBLIC_ORIGIN \
    NEXT_PUBLIC_FEATURES=$NEXT_PUBLIC_FEATURES \
    NEXT_PUBLIC_SPEECH_PROVIDER=$NEXT_PUBLIC_SPEECH_PROVIDER \
    NEXT_PUBLIC_HAND_MODEL_URL=$NEXT_PUBLIC_HAND_MODEL_URL \
    API_PROXY_TARGET=$API_PROXY_TARGET
COPY packages/schema/ts packages/schema/ts
COPY apps/web apps/web
RUN pnpm --filter @ithaca/web build

FROM base AS run
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /repo /repo
WORKDIR /repo/apps/web
EXPOSE 3000
CMD ["pnpm", "exec", "next", "start"]
