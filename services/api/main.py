"""The API process. Owner: B.

    uvicorn services.api.main:app --reload --port 8000

Stream C's property routes (twin, sun, daylight, ...) are mounted from routers/properties.py.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.api.agent.provider import get_observer, get_turn_runner, provider_name
from services.api.realtime.agent_bridge import AgentBridge
from services.api.realtime.hub import Hub
from services.api.routers import households, properties, sessions

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.hub = Hub()
    app.state.bridge = AgentBridge(app.state.hub, get_turn_runner(), observe=get_observer())
    yield


app = FastAPI(title="Ithaca API", lifespan=lifespan)
# Dev: the web app runs on another port. Tighten when there is real auth.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(sessions.router, prefix="/api")
app.include_router(households.router, prefix="/api")
app.include_router(properties.router, prefix="/api")
# The WebSocket is also served without the prefix (/ws/sessions/{sid}), which is the path in
# WORKSTREAMS.md 2.4 and the one the web app connects to directly on port 8000.
app.add_api_websocket_route("/ws/sessions/{session_id}", sessions.session_socket)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "agent_provider": provider_name(), "observer": get_observer() is not None}
