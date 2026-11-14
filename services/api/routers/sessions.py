"""Sessions, pairing and the session WebSocket (WORKSTREAMS.md section 2.4). Owner: B."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from packages.schema.events import parse_client_event
from packages.schema.session import ClientKind, Session
from services.api.realtime.hub import Hub
from services.api.twin.service import TwinNotFound, get_twin

log = logging.getLogger(__name__)
router = APIRouter()


class CreateSession(BaseModel):
    property_id: str


class JoinSession(BaseModel):
    pair_token: str


class JoinResult(BaseModel):
    ok: bool
    client_token: str


@router.post("/sessions")
def create_session(body: CreateSession, request: Request, x_user_id: str = Header("demo")) -> Session:
    """The desktop starts a session. `client_token` goes on its WebSocket URL; `pair_token` goes in
    the QR code for the phone."""
    try:
        get_twin(body.property_id)
    except TwinNotFound as e:
        raise HTTPException(404, str(e)) from e
    hub: Hub = request.app.state.hub
    return hub.create_session(user_id=x_user_id, property_id=body.property_id)


@router.post("/sessions/{session_id}/pair")
def new_pair_token(session_id: str, request: Request) -> Session:
    """A fresh pairing token, e.g. when the QR code expired or for another phone."""
    hub: Hub = request.app.state.hub
    if session_id not in hub.rooms:
        raise HTTPException(404, "unknown session")
    return hub.new_pair_token(session_id)


@router.post("/sessions/{session_id}/join")
def join_session(session_id: str, body: JoinSession, request: Request) -> JoinResult:
    """The phone trades the pairing token from the QR code for its own client_token."""
    hub: Hub = request.app.state.hub
    client_token = hub.redeem_pair_token(session_id, body.pair_token)
    if client_token is None:
        raise HTTPException(403, "pairing token is invalid, used or expired")
    return JoinResult(ok=True, client_token=client_token)


@router.websocket("/ws/sessions/{session_id}")
async def session_socket(ws: WebSocket, session_id: str, client: ClientKind, token: str = "") -> None:
    hub: Hub = ws.app.state.hub
    if hub.authorize(session_id, token) != client:
        await ws.close(code=4401, reason="bad session, client or token")
        return
    await ws.accept()
    conn = hub.connect(session_id, client)

    async def send_loop() -> None:
        while True:
            await ws.send_text((await conn.queue.get()).model_dump_json())

    sender = asyncio.create_task(send_loop())
    try:
        while True:
            try:
                raw = json.loads(await ws.receive_text())
            except json.JSONDecodeError:
                log.info("dropped non-JSON message from %s", client)
                continue
            event = parse_client_event(raw, session_id, source=client)
            if isinstance(event, str):
                log.info("dropped event from %s: %s", client, event)  # I-G7: log, never throw
            else:
                hub.publish(event)
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        hub.disconnect(conn)
