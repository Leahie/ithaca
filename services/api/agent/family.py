"""Family tools: one agent per household member, talking to each other through the house.

Each member's agent runs with that member's AgentContext (user_id = member_id), so it reads and
writes only its own member's memory (B4). Agents meet in two ways, and neither reads anyone
else's namespace:

- the family board: notes an agent chooses to share (`family_post_note`, `family_read_board`);
- asking: `family_ask_member` runs the other member's agent on one question, under that member's
  own context and memory, and returns its answer. The asked agent can't ask anyone in turn.

Both show on the desktop as `family.note`, `family.ask` and `family.answer` events. The helpers
below are shared with the scripted agent, which has no tool calls.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime, tool

from services.api.agent.context import AgentContext
from services.api.integrations.config import env
from services.api.realtime.households import households

log = logging.getLogger(__name__)

Runtime = ToolRuntime[AgentContext]
# (text, context, thread_id) -> runner tuples; see provider.TurnRunner. Set by the agent bridge.
_runner: Callable[[str, AgentContext, str], Iterator[tuple[Any, ...]]] | None = None
# A fresh thread for the asked agent: it must not inherit the asking turn's LangGraph context.
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="family-ask")
ASK_TIMEOUT_S = 90.0


def agents_talk() -> bool:
    """Whether members' agents may reach each other (the family board, asking another member's
    agent). Off unless FAMILY_AGENTS_TALK=1: each agent then knows only its own person."""
    return env("FAMILY_AGENTS_TALK", "0") == "1"


def set_runner(runner: Callable[[str, AgentContext, str], Iterator[tuple[Any, ...]]]) -> None:
    global _runner
    _runner = runner


def asked_message(asker: str, question: str) -> str:
    return (
        f'<event type="family_question" from="{asker}">{question}</event>\n'
        "Answer for your own person, from what you remember about them, in two or three sentences."
    )


# --- shared helpers (tools and the scripted agent) ----------------------------------------------


def members_text(context: AgentContext) -> str:
    h = households().get(context.household_id or "")
    if h is None:
        return "This session has no family."
    lines = [f"Family: {h.name}"]
    for m in h.members:
        me = " (your person)" if m.member_id == context.user_id else ""
        lines.append(f"- {m.name}{me}")
    return "\n".join(lines)


def post_note(context: AgentContext, text: str, room_id: str | None = None) -> dict:
    """Store a note on the family board; returns the `family.note` payload."""
    return households().add_note(context.household_id or "", context.user_id, text, context.property_id, room_id)


def board_text(context: AgentContext, limit: int = 20) -> str:
    notes = households().notes(context.household_id or "", limit, context.property_id)
    if not notes:
        return "The family board is empty."
    lines = []
    for n in notes:
        who = f"{n['name']}'s agent" if n["name"] else "The guide"
        where = f" (room {n['room_id']})" if n["room_id"] else ""
        lines.append(f"- {who}{where}: {n['text']}")
    return "\n".join(lines)


def ask_member(context: AgentContext, name: str, question: str) -> tuple[dict | None, str]:
    """Run another member's agent on one question. Returns (the family.ask payload or None, the
    answer or an error for the asking agent)."""
    store = households()
    if not context.household_id:
        return None, "Error: this session has no family."
    if not agents_talk():
        return None, "Error: members' agents are kept apart in this setup."
    if context.asked:
        return None, "Error: you were asked a question yourself; answer it instead of asking others."
    other = store.member_by_name(context.household_id, name)
    if other is None:
        return None, f"Error: no family member called {name!r}. Use family_list_members."
    if other.member_id == context.user_id:
        return None, "Error: that is your own person; answer from your own memory."
    if _runner is None:
        return None, "Error: other members' agents can't be reached right now."
    asker = store.member(context.household_id, context.user_id)
    ask = {"ask_id": uuid.uuid4().hex, "from_member_id": context.user_id, "to_member_id": other.member_id, "question": question.strip()}
    their = AgentContext(
        user_id=other.member_id,
        property_id=context.property_id,
        session_id=context.session_id,
        household_id=context.household_id,
        member_name=other.name,
        asked=True,
    )

    def run() -> str:
        final = ""
        thread = f"{context.session_id}:{other.member_id}:asked"
        for kind, *rest in _runner(asked_message(asker.name + "'s agent" if asker else "the family's guide", question), their, thread):  # type: ignore[misc]
            if kind == "message":
                final = rest[0]
            elif kind == "error":
                raise RuntimeError(rest[0])
            # Their tokens and UI events are dropped: the asked agent doesn't move the shared view.
        return final.strip()

    try:
        answer = _pool.submit(run).result(timeout=ASK_TIMEOUT_S)
    except Exception as e:  # a slow or failed answer must not sink the asking turn
        log.warning("family ask failed: %s", e)
        return ask, f"Error: {other.name}'s agent could not answer ({e})."
    return ask, answer or f"{other.name}'s agent had nothing to add."


# --- tools ----------------------------------------------------------------------------------------


def _emit(runtime: Runtime, event_type: str, payload: dict) -> None:
    runtime.stream_writer({"type": event_type, "payload": payload})


@tool
def family_list_members(runtime: Runtime) -> str:
    """Who is in the family looking at this house, and which member is your own person."""
    return members_text(runtime.context)


@tool
def family_post_note(text: str, runtime: Runtime, room_id: str | None = None) -> str:
    """Share something with the rest of the family on the family board: a must-have this house
    meets or breaks, a worry, a room your person loved. Everyone sees it on the big screen and the
    other agents can read it. Keep it to one or two sentences. room_id from the room directory, if
    the note is about one room. Never post something your person told you in confidence."""
    if not runtime.context.household_id:
        return "Error: this session has no family."
    note = post_note(runtime.context, text, room_id)
    _emit(runtime, "family.note", note)
    return "Posted to the family board."


@tool
def family_read_board(runtime: Runtime, limit: int = 20) -> str:
    """The family board for this house: what the other members' agents shared, oldest first."""
    if not runtime.context.household_id:
        return "Error: this session has no family."
    return board_text(runtime.context, limit)


@tool
def family_ask_member(name: str, question: str, runtime: Runtime) -> str:
    """Ask another family member's agent a question, e.g. whether this bedroom would suit them or
    what they need from a kitchen. Their agent answers from what it knows about its own person.
    Use it instead of guessing about someone else. name: as family_list_members gives it."""
    ask, answer = ask_member(runtime.context, name, question)
    if ask is not None:
        _emit(runtime, "family.ask", ask)
        if not answer.startswith("Error:"):
            _emit(runtime, "family.answer", {**{k: ask[k] for k in ("ask_id", "from_member_id", "to_member_id")}, "answer": answer})
    return answer


TALK_TOOLS = [family_post_note, family_read_board, family_ask_member]


def family_tools() -> list:
    """The tools an agent gets: who is in the family, and, only when agents may talk, the rest."""
    return [family_list_members, *(TALK_TOOLS if agents_talk() else [])]


class FamilyContextMiddleware(AgentMiddleware):
    """In a household session, tells the agent whose agent it is and who else is in the family.
    Appended per call, after the static persona, so the persona stays cacheable."""

    def _extend(self, request):
        from deepagents.middleware._utils import append_to_system_message

        ctx: AgentContext = request.runtime.context
        if not ctx.household_id:
            return request
        h = households().get(ctx.household_id)
        if h is None:
            return request
        others = ", ".join(m.name for m in h.members if m.member_id != ctx.user_id) or "nobody else yet"
        if not agents_talk():
            # Kept apart: each agent knows its own person and nothing of what the others were told.
            if ctx.member_name is None:
                text = (
                    "## The family\n"
                    f"The {h.name} family is looking at this house together on the big screen: {others}. You are the shared "
                    "guide. Each member also has their own agent on their phone. You cannot reach those agents or what "
                    "they know: when a question is about one member's needs, ask the person."
                )
            else:
                text = (
                    "## Your family\n"
                    f"You are {ctx.member_name}'s own agent. The {h.name} family is looking at this house together, and "
                    f"each member has their own agent. The others: {others}. Your memory and this conversation are "
                    f"{ctx.member_name}'s alone: you know nothing of what the others told their agents, and they know "
                    "nothing of yours.\n"
                    "- Speak for your person, not for the whole family.\n"
                    "- Everything you say shows on the shared screen, labelled with your person's name."
                )
            return request.override(system_message=append_to_system_message(request.system_message, text))
        if ctx.member_name is None:
            # The session's own agent, on the big screen: a guide for the whole family.
            text = (
                "## The family\n"
                f"The {h.name} family is looking at this house together on the big screen: {others}. Each member also has "
                "their own agent on their phone, which knows that person. You are the shared guide.\n"
                "- When a question is about what one member needs, ask their agent with family_ask_member.\n"
                "- Read family_read_board before summing up how the house suits the family, and say where members' "
                "wishes conflict."
            )
            return request.override(system_message=append_to_system_message(request.system_message, text))
        text = (
            "## Your family\n"
            f"You are {ctx.member_name or 'this member'}'s own agent, one of several: the {h.name} family is looking at this "
            f"house together, and each member has their own agent. The others: {others}. Your memory files are "
            f"{ctx.member_name or 'your person'}'s alone.\n"
            "- Speak for your person, not for the whole family.\n"
            "- When a question touches someone else's needs, ask their agent with family_ask_member instead of guessing.\n"
            "- When you find something the family should weigh together (a must-have met or broken, a conflict between "
            "members' wishes), post it with family_post_note, in one or two sentences. Read family_read_board before "
            "giving a verdict on the house.\n"
            "- Everything you say shows on the shared screen, labelled with your person's name."
        )
        if ctx.asked:
            text += "\n- This turn is a question from another member's agent: answer it briefly; don't move the view or ask anyone."
        return request.override(system_message=append_to_system_message(request.system_message, text))

    def wrap_model_call(self, request, handler):
        return handler(self._extend(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._extend(request))
