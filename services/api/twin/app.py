"""Stream C's routes on their own, for working without B's app (services/api/main.py).

    uvicorn services.api.twin.app:app --reload --port 8000

Same paths as the full API: everything under /api. Once B's main.py includes
routers.properties, use that instead.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.api.routers import panos, properties

app = FastAPI(title="Ithaca API (Stream C routes)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(properties.router, prefix="/api")
app.include_router(panos.router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "routes": "stream-c"}
