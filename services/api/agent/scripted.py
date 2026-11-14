"""AGENT_PROVIDER=scripted: a fake agent with no LLM (WORKSTREAMS.md section 3).

Same output shape as runner.stream_turn, so the hub can't tell the difference. It lets Stream A
build the agent dock, action chips and viewer reactions with no API key. Known prompts:

    "take me to the kitchen"  (any room label works)  -> viewer.goto
    "show me December morning"                         -> viewer.setMode sun + viewer.setTime
    "show layouts"                                     -> viewer.setMode design + design.showVariants
    "show me the walk to the bathroom"                 -> viewer.walk (a bedroom to its bathroom)
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from services.api.agent import prompts, views
from services.api.agent.context import AgentContext
from services.api.integrations.config import fixtures_dir
from services.api.twin.service import get_twin



def demo_layout_ids(property_id: str) -> list[str]:
    """Ids of the hand-placed layouts in fixtures/layouts/<property_id>.json (Stream A's stand-ins)."""
    path = fixtures_dir() / "layouts" / f"{property_id}.json"
    return [row["layout_id"] for row in json.loads(path.read_text(encoding="utf-8"))] if path.exists() else []


TOKEN_DELAY_S = 0.04


def _say(text: str) -> Iterator[tuple[Any, ...]]:
    for i, word in enumerate(text.split(" ")):
        time.sleep(TOKEN_DELAY_S)
        yield ("token", word if i == 0 else " " + word)
    yield ("message", text)


def _family_scripted(text: str, said: str, context: AgentContext) -> Iterator[tuple[Any, ...]] | None:
    """Family prompts, scripted: greetings, answering another agent, "ask Leo about the kitchen",
    "tell the family the yard is small". None when the text is none of these."""
    from services.api.agent import family

    me = context.member_name or "the family"
    plain = text.split("/>\n", 1)[-1].strip()
    if '<event type="member_joined">' in said:
        return _say(f"Hi {me}, I'm your own agent for this house. Ask me anything, or try: ask Leo about the kitchen, or tell the family the yard is small.")
    if '<event type="family_question"' in said:
        return _say(f"Speaking for {me}: I'm the scripted stand-in and haven't learned what {me} needs yet, so I'd ask {me} directly.")
    asked = re.match(r"(?i)ask (\w+)(?:'s agent)?\s*(?:about|whether|if|what|how)?\s*(.*)", plain)
    if asked:
        name, question = asked.group(1), asked.group(2).strip(" ?.") or "what do you think of this house"

        def ask() -> Iterator[tuple[Any, ...]]:
            yield ("action", "family_ask_member", {"name": name, "question": question})
            payload, answer = family.ask_member(context, name, question + "?")
            if payload is not None:
                yield ("ui", {"type": "family.ask", "payload": payload})
                if not answer.startswith("Error:"):
                    ids = {k: payload[k] for k in ("ask_id", "from_member_id", "to_member_id")}
                    yield ("ui", {"type": "family.answer", "payload": {**ids, "answer": answer}})
            yield from _say(answer.removeprefix("Error: ") if answer.startswith("Error:") else f"I asked {name}'s agent. {answer}")

        return ask()
    told = re.match(r"(?i)(?:tell the family|note for the family|post)[:,]?\s*(?:that\s+)?(.+)", plain)
    if told:

        def note() -> Iterator[tuple[Any, ...]]:
            yield ("action", "family_post_note", {})
            payload = family.post_note(context, told.group(1).strip())
            yield ("ui", {"type": "family.note", "payload": payload})
            yield from _say("Posted to the family board.")

        return note()
    return None


def stream_turn_scripted(text: str, context: AgentContext, thread_id: str) -> Iterator[tuple[Any, ...]]:
    twin = get_twin(context.property_id)
    said = text.lower()

    if '<event type="room_entered">' in said:
        return  # stays silent
    if context.household_id:
        family_turn = _family_scripted(text, said, context)
        if family_turn is not None:
            yield from family_turn
            return
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
        layout_ids = demo_layout_ids(context.property_id)
        yield ("action", "design_show_variants", {"layout_ids": layout_ids})
        yield ("ui", {"type": "design.showVariants", "payload": {"layout_ids": layout_ids}})
        yield from _say("Here are three ways to arrange the room. Pick one, or tell me what to change.")
        return

    if "walk" in said:
        # the east bedroom upstairs to its bathroom if they exist, else the first two rooms that connect
        pairs = [(a, b) for a in twin.rooms for b in twin.rooms if a.label == "Bedroom" and b.label == "Bathroom"]
        found = next(((a, b, r) for a, b in pairs if (r := views.route(twin, a.room_id, b.room_id)) and len(r.room_ids) == 2), None)
        if found:
            a, b, r = found
            payload = {
                "from_room_id": a.room_id, "to_room_id": b.room_id, "sweep_ids": r.sweep_ids,
                "look_at": r.look_at.model_dump(), "distance_m": round(r.distance_m, 1), "duration_s": round(r.seconds, 1),
            }
            yield ("action", "viewer_walk", {"from_room_id": a.room_id, "to_room_id": b.room_id})
            yield ("ui", {"type": "viewer.setMode", "payload": {"mode": "tour"}})
            yield ("ui", {"type": "viewer.walk", "payload": payload})
            yield from _say(f"From this bedroom to its bathroom is {r.distance_m:.1f} meters, about {r.seconds:.0f} seconds.")
            return

    # "take me to the <room label>": the largest room whose label is mentioned
    question = said.rsplit("/>", 1)[-1]  # ignore the <viewer .../> line the hub adds
    rooms = [r for r in twin.rooms if r.label.lower() in question]
    if rooms:
        room = max(rooms, key=lambda r: r.area_m2 or 0)
        payload = {"room_id": room.room_id}
        if view := views.best_view(twin, room.room_id):
            payload |= {"at_sweep_id": view.sweep_id, "look_at": view.look_at.model_dump()}
        yield ("action", "viewer_goto", {"room_id": room.room_id})
        yield ("ui", {"type": "viewer.goto", "payload": payload})
        yield from _say(f"Let me take you to the {room.label.lower()}. This is {prompts.room_display_names(twin)[room.room_id]}.")
        return

    yield from _say("I'm the scripted agent. Try: take me to the kitchen, show me December morning, or show layouts.")
