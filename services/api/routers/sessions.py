"""Sessions, pairing and the session WebSocket (WORKSTREAMS.md section 2.4). Owner: B."""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from packages.schema.events import parse_client_event
from packages.schema.session import ClientKind, CreateSessionRequest, JoinRequest, JoinResult, Session
from services.api.agent import memory_files
from services.api.furniture import ledger
from services.api.furniture.store import furniture_store
from services.api.integrations.config import env, fixtures_dir
from services.api.realtime.households import households
from services.api.realtime.hub import Hub
from services.api.twin.service import TwinNotFound, get_twin

log = logging.getLogger(__name__)
router = APIRouter()


def _default_property_id() -> str | None:
    """The property to use when the client names none: DEFAULT_PROPERTY_ID, or the only twin."""
    if env("DEFAULT_PROPERTY_ID"):
        return env("DEFAULT_PROPERTY_ID")
    twins = sorted((fixtures_dir() / "twin").glob("*.json"))
    return twins[0].stem if len(twins) == 1 else None


@router.post("/sessions")
def create_session(
    request: Request, body: CreateSessionRequest | None = None, x_user_id: str = Header("demo")
) -> Session:
    """The desktop starts a session. `pair_token` goes in the QR code for the phone. Either token
    opens the desktop's WebSocket."""
    property_id = body.property_id if body else _default_property_id()
    if property_id is None:
        raise HTTPException(422, 'send {"property_id": "..."}: there is more than one property')
    try:
        twin = get_twin(property_id)
    except TwinNotFound as e:
        raise HTTPException(404, str(e)) from e
    household_id = body.household_id if body else None
    if household_id and households().get(household_id) is None:
        raise HTTPException(404, "unknown household")
    hub: Hub = request.app.state.hub
    # the agent reads inventory.md from memory: make sure it matches the database before the first turn (B3)
    memory_files.sync_inventory(request.app.state.memory_store, x_user_id, furniture_store().list_items(x_user_id))
    session = hub.create_session(user_id=x_user_id, property_id=property_id, timezone_name=twin.timezone, household_id=household_id)
    if household_id:
        # The family's phones join whatever house the desktop has open now.
        households().set_active_session(household_id, session.session_id)
        households().set_host(household_id, x_user_id)
        households().open(household_id, x_user_id)  # its house and memory are the ones on screen
    try:  # the house's furniture register is on file from the moment the house is opened (F11)
        ledger.saved(x_user_id, property_id)
    except Exception:  # the session matters more than the saved copy; the guides read the database anyway
        log.exception("the furniture register of %s could not be saved", property_id)
    return session


@router.post("/sessions/{session_id}/pair")
def new_pair_token(session_id: str, request: Request) -> Session:
    """A fresh pairing token, e.g. when the QR code expired or for another phone."""
    hub: Hub = request.app.state.hub
    if session_id not in hub.rooms:
        raise HTTPException(404, "unknown session")
    return hub.new_pair_token(session_id)


@router.post("/sessions/{session_id}/join")
def join_session(session_id: str, body: JoinRequest, request: Request) -> JoinResult:
    """The phone trades the pairing token from the QR code for its own client_token."""
    hub: Hub = request.app.state.hub
    client_token = hub.redeem_pair_token(session_id, body.pair_token)
    if client_token is None:
        raise HTTPException(403, "pairing token is invalid, used or expired")
    return JoinResult(ok=True, client_token=client_token)


@router.websocket("/ws/sessions/{session_id}")
async def session_socket(ws: WebSocket, session_id: str, client: ClientKind, token: str = "") -> None:
    hub: Hub = ws.app.state.hub
    if not hub.authorize(session_id, client, token):
        await ws.close(code=4401, reason="bad session, client or token")
        return
    await ws.accept()
    member_id = hub.member_for(session_id, token) if client == "phone" else None
    room = hub.rooms[session_id]
    if room.household_id:
        # A desktop that reuses a saved session never calls POST /sessions again, and households
        # made before hosts existed have none: without a host the phones can't place their pieces.
        households().set_host(room.household_id, room.user_id)
        if client == "desktop":
            # The family's phones join the session the big screen is really on. A reloaded page
            # reuses its saved session without POST /sessions, and that may not be the one the
            # household was last pointed at (two were made at once on a first load).
            households().set_active_session(room.household_id, session_id)
            households().open(room.household_id, room.user_id)
    member = households().member(room.household_id, member_id) if member_id and room.household_id else None
    conn = hub.connect(session_id, client, member_id=member_id, name=member.name if member else None)

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
            event = parse_client_event(raw, session_id, source=client, member_id=member_id)
            if isinstance(event, str):
                log.info("dropped event from %s: %s", client, event)  # I-G7: log, never throw
            else:
                hub.publish(event)
    except WebSocketDisconnect:
        pass
    finally:
        sender.cancel()
        hub.disconnect(conn)
