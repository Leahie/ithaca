"""AGENT_PROVIDER=scripted: a fake agent with no LLM (WORKSTREAMS.md section 3).

Same output shape as runner.stream_turn, so the hub can't tell the difference. It lets Stream A
build the agent dock, action chips and viewer reactions with no API key. Known prompts:

    "take me to the kitchen"  (any room label works)  -> viewer.goto
    "show me December morning"                         -> viewer.setMode sun + viewer.setTime
    "show layouts"                                     -> viewer.setMode design + design.showVariants
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from services.api.agent import prompts
from services.api.agent.context import AgentContext
from services.api.twin.service import get_twin

# The layout fixtures (GET /layouts) will use these ids.
DEMO_LAYOUT_IDS = [
    "00000000-0000-4000-8000-000000000001",
    "00000000-0000-4000-8000-000000000002",
    "00000000-0000-4000-8000-000000000003",
]
TOKEN_DELAY_S = 0.04


def _say(text: str) -> Iterator[tuple[Any, ...]]:
    for i, word in enumerate(text.split(" ")):
        time.sleep(TOKEN_DELAY_S)
        yield ("token", word if i == 0 else " " + word)
    yield ("message", text)


def stream_turn_scripted(text: str, context: AgentContext, thread_id: str) -> Iterator[tuple[Any, ...]]:
    twin = get_twin(context.property_id)
    said = text.lower()

    if '<event type="room_entered">' in said:
        return  # stays silent
    if '<event type="property_opened">' in said:
        yield from _say(
            f"Welcome to {twin.address.split(',')[0]}. I'm the scripted stand-in for the guide. "
            "Try: take me to the kitchen, show me December morning, or show layouts."
        )
        return

    if "december" in said:
        when = datetime(datetime.now().year, 12, 21, 8, 0, tzinfo=ZoneInfo(twin.timezone)).isoformat()
        yield ("action", "viewer_set_mode", {"mode": "sun"})
        yield ("ui", {"type": "viewer.setMode", "payload": {"mode": "sun"}})
        yield ("action", "viewer_set_time", {"datetime_iso": when})
        yield ("ui", {"type": "viewer.setTime", "payload": {"datetime": when}})
        yield from _say("Here is the house at 8 in the morning on December 21, the shortest day of the year.")
        return

    if "layout" in said:
        yield ("action", "viewer_set_mode", {"mode": "design"})
        yield ("ui", {"type": "viewer.setMode", "payload": {"mode": "design"}})
        yield ("action", "design_show_variants", {"layout_ids": DEMO_LAYOUT_IDS})
        yield ("ui", {"type": "design.showVariants", "payload": {"layout_ids": DEMO_LAYOUT_IDS}})
        yield from _say("Here are three ways to arrange the room. Pick one, or tell me what to change.")
        return

    # "take me to the <room label>": the largest room whose label is mentioned
    question = said.rsplit("/>", 1)[-1]  # ignore the <viewer .../> line the hub adds
    rooms = [r for r in twin.rooms if r.label.lower() in question]
    if rooms:
        room = max(rooms, key=lambda r: r.area_m2 or 0)
        yield ("action", "viewer_goto", {"room_id": room.room_id})
        yield ("ui", {"type": "viewer.goto", "payload": {"room_id": room.room_id}})
        yield from _say(f"Let me take you to the {room.label.lower()}. This is {prompts.room_display_names(twin)[room.room_id]}.")
        return

    yield from _say("I'm the scripted agent. Try: take me to the kitchen, show me December morning, or show layouts.")
