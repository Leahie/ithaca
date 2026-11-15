"""Reset everything: delete all of the user's data and start the app from scratch. Owner: B.

    GET  /reset/preview                          -> ResetCounts  (what a reset would delete)
    POST /reset            {"confirm": "reset"}  -> ResetResult  (deletes it; anything else: 400)

Only the big screen may ask. A family member's phone (X-User-Id "m-...") gets 403.

What goes, for everyone:
- furniture.sqlite: every piece, what stands in every house, every layout, every house's log;
- households.sqlite: every family, member, family code and board (every table in the file);
- memory.sqlite: every memory file of every person, family and house;
- threads.sqlite: every conversation;
- sessions.sqlite and the hub: every session, token, remembered place and greeting;
- the files under uploads/, items/, frames/ and segments/, and the log of what the microphone heard;
- what the process holds in memory: the guides' running turns and designer runs, the design
  gate, pending choices and briefs, house plans, tour logs, job statuses.

What stays, because it is not the user's: the house's own twin (data/matterport, fixtures), the
downloaded furniture models (data/catalog, data/library), and the configuration.

Each store empties itself through its own open connection (its `wipe`): the files stay, so the
running API goes on working with empty stores. If one part fails the others still run, and the
answer names the part that failed.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from packages.schema.session import ResetCounts, ResetFailure, ResetRequest, ResetResult
from services.api.agent import design_gate, design_tools, house_plans, provider
from services.api.agent.tour import TOUR_LOGS
from services.api.furniture.store import FurnitureStore, furniture_store
from services.api.jobs import JobRunner
from services.api.objects import files
from services.api.realtime.agent_bridge import AgentBridge
from services.api.realtime.households import HouseholdStore, households
from services.api.realtime.hub import Hub
from services.api.routers import speech

log = logging.getLogger(__name__)
router = APIRouter(tags=["reset"])

# How long a reset waits for a guide's turn, a designer or a photo job in flight to end. Their
# threads cannot be killed; one that is still writing after the delete would leave something behind.
STOP_WAIT_S = 8.0


@dataclass
class Stores:
    """Everything a reset empties. The routes pass the process's own; a test passes temporary
    ones. Nothing here has a default, so nothing is ever emptied because it was left out."""

    hub: Hub
    bridge: AgentBridge | None
    jobs: JobRunner | None
    furniture: FurnitureStore
    households: HouseholdStore
    memory: Any  # langgraph's SqliteStore: the memory files
    conversations: Any  # langgraph's SqliteSaver: the conversations
    files_root: Path  # holds uploads/, items/, frames/, segments/
    speech_log: Path


def _stores(request: Request) -> Stores:
    state = request.app.state
    return Stores(
        hub=state.hub, bridge=state.bridge, jobs=state.jobs, furniture=furniture_store(), households=households(),
        memory=state.memory_store, conversations=provider.conversation_store(), files_root=files.root(), speech_log=speech.SPEECH_LOG,
    )


def _big_screen_only(user_id: str) -> None:
    if user_id.startswith("m-"):  # a family member's id (realtime/households.py, join)
        raise HTTPException(403, "Only the big screen can reset everything.")


def count(stores: Stores) -> ResetCounts:
    """How much there is, in the words of ResetCounts."""
    return ResetCounts(
        **stores.furniture.counts(),
        **stores.households.counts(),
        memory_files=provider.memory_count(stores.memory),
        conversations=provider.conversation_count(stores.conversations),
        files=files.count(stores.files_root),
    )


def _forget_in_memory(stores: Stores) -> None:
    """What the guides keep per conversation and per house, in this process only."""
    if stores.bridge is not None:
        stores.bridge.wipe()  # running turns and designer runs are told to stop
    design_gate.reset()  # rooms being arranged, waiting to be shown, held back
    design_tools.reset()  # questions asked with cards, briefs given to the designer
    house_plans.reset()  # who has plans for which room
    TOUR_LOGS.clear()  # what each visitor did and said
    # Addresses of room photos sent to fal.ai in the last minutes. Only if that provider is loaded:
    # importing it here would pull in its libraries for nothing.
    fal = sys.modules.get("services.api.objects.providers.fal")
    if fal is not None:
        with fal._uploads:
            fal._uploaded.clear()


async def _settle(stores: Stores) -> int:
    """Wait, at most STOP_WAIT_S, for the work that was told to stop. Returns how many pieces of
    it are still running."""
    deadline = time.monotonic() + STOP_WAIT_S
    still = await stores.bridge.settle(STOP_WAIT_S) if stores.bridge is not None else 0
    while stores.jobs is not None and stores.jobs.going() and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
    return still + (stores.jobs.going() if stores.jobs is not None else 0)


async def reset_everything(stores: Stores) -> ResetResult:
    """Delete everything in `stores`. Call from the event loop (the hub and the bridge live there)."""
    deleted: dict[str, int] = {}
    failed: list[ResetFailure] = []

    def part(name: str, work: Callable[[], Any]) -> None:
        """One part of the reset. If it fails the others still run, and the answer says so."""
        try:
            counts = work()
        except Exception as e:
            log.exception("reset: %s failed", name)
            if not any(f.part == name for f in failed):  # sessions and the guides are done twice: said once
                failed.append(ResetFailure(part=name, error=f"{type(e).__name__}: {e}"))
        else:
            if isinstance(counts, dict):
                deleted.update(counts)

    def wipe_files() -> dict[str, int]:
        gone, errors = files.wipe(stores.files_root)
        if errors:
            log.error("reset: %d file(s) could not be deleted: %s", len(errors), "; ".join(errors[:10]))
            failed.append(ResetFailure(part="files", error=f"{len(errors)} file(s) could not be deleted. The first: {errors[0]}"))
        return {"files": gone}

    # The screens first: each is told and hung up on, so nothing new starts while the rest goes.
    part("sessions", stores.hub.wipe)
    part("guide", lambda: _forget_in_memory(stores))
    if stores.jobs is not None:
        part("jobs", stores.jobs.wipe)
    still = await _settle(stores)
    if still:
        log.warning("reset: %d piece(s) of work were still running after %.0f s", still, STOP_WAIT_S)
        failed.append(ResetFailure(
            part="work in flight",
            error=f"{still} piece(s) of work (a guide's turn, a designer or a photo job) did not stop within {STOP_WAIT_S:.0f} seconds. "
                  "What they save from now on is not deleted. Reset again in a minute.",
        ))

    part("furniture", stores.furniture.wipe)
    part("families", stores.households.wipe)
    part("memory", lambda: {"memory_files": provider.wipe_memory(stores.memory)})
    part("conversations", lambda: {"conversations": provider.wipe_conversations(stores.conversations)})
    part("files", wipe_files)
    part("speech log", lambda: stores.speech_log.unlink(missing_ok=True))
    # Once more, last: a session opened while this ran, and what a stopping turn wrote on its way out.
    part("sessions", stores.hub.wipe)
    part("guide", lambda: _forget_in_memory(stores))

    # A part that failed deleted nothing that can be counted: it reads 0.
    result = ResetResult(ok=not failed, deleted=ResetCounts(**(dict.fromkeys(ResetCounts.model_fields, 0) | deleted)), failed=failed)
    log.warning("reset: done, deleted %s%s", result.deleted.model_dump(), f"; FAILED: {[f.part for f in failed]}" if failed else "")
    return result


@router.get("/reset/preview")
def reset_preview(request: Request, x_user_id: str = Header("demo")) -> ResetCounts:
    """What a reset would delete, as counts. Deletes nothing."""
    _big_screen_only(x_user_id)
    return count(_stores(request))


@router.post("/reset")
async def reset(request: Request, x_user_id: str = Header("demo")) -> ResetResult:
    """Delete everything and start from scratch. The body must be {"confirm": "reset"}. Answers
    with what was deleted; `ok` is false and `failed` names the parts if some of it was not."""
    _big_screen_only(x_user_id)
    try:
        confirmed = ResetRequest.model_validate(await request.json()).confirm == "reset"
    except Exception:  # no body, not JSON, not an object, no such field: all the same as not confirmed
        confirmed = False
    if not confirmed:
        raise HTTPException(400, 'Nothing was deleted. Send {"confirm": "reset"} to delete everything.')
    log.warning("reset: everything is being deleted, asked by %s", x_user_id)
    return await reset_everything(_stores(request))
