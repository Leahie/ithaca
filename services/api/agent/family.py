"""Family tools: one agent per household member, talking to each other through the house.

Each member's agent runs with that member's AgentContext (user_id = member_id), so it reads and
writes only its own member's memory (B4). Agents meet in two ways, and neither reads anyone
else's namespace:

- the family board: notes an agent chooses to share (`family_post_note`, `family_read_board`);
- asking: `family_ask_member` runs the other member's agent on one question, under that member's
  own context and memory, and returns its answer. The asked agent can't ask anyone in turn.

Both show on the desktop as `family.note`, `family.ask` and `family.answer` events. The helpers
below are shared with the scripted agent, which has no tool calls.

Apart from that, every guide keeps its own person's card on the board of wishes (the Family page:
what each person wants from a home, packages/schema/board.py) with `board_post`, `board_edit` and
`board_remove`, and every agent is shown the whole board on each model call
(`FamilyBoardMiddleware`). That part works with or without a family and is not behind
FAMILY_AGENTS_TALK: a card holds only what a guide chose to post, never a person's memory.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.tools import ToolRuntime, tool

from packages.schema.board import BOARD_SHARED, BoardEntry, BoardKind
from services.api.agent import memory_files as memory_io
from services.api.agent import prompts
from services.api.agent.context import AgentContext
from services.api.integrations.config import env
from services.api.realtime.households import households, is_member, solo_board
from services.api.twin.service import get_twin

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


# --- the board of wishes: helpers -------------------------------------------------------------------

SHORT_ENTRY = 8  # characters of an entry_id the agents are shown, as with layout ids (design_tools.py)
ENTRY_WORDS = 30  # a post longer than this is turned back: the persona asks for 20 words at most
_KINDS = (("must", "Must-haves"), ("like", "Style and likes"), ("avoid", "Avoid"), ("about", "About"))


def board_key(context: AgentContext) -> str:
    """The board this agent reads and writes: its family's, or its user's own when there is none."""
    return context.household_id or solo_board(context.user_id)


def board_person(context: AgentContext) -> str:
    """Whose card this agent keeps: its member's, or the big screen's (a family's shared guide,
    and the guide of a person alone)."""
    return context.user_id if context.household_id and is_member(context.user_id) else BOARD_SHARED


def _short(entry: BoardEntry) -> str:
    return entry.entry_id[:SHORT_ENTRY]


def _remembers(store, context: AgentContext) -> bool:
    """Whether this agent's memory says anything about its person: a line that is not a heading."""
    if store is None:
        return False
    for path in ("profile.md", "preferences.md"):
        text = memory_io.read_file(store, context.memory_user, path) or ""
        if any(line.strip() and not line.lstrip().startswith("#") for line in text.splitlines()):
            return True
    return False


def board_for_agent(context: AgentContext, store=None, designer: bool = False) -> str | None:
    """The whole board as an agent is shown it, read from the database now: each person's entries
    by kind, then by room of this house. A guide sees each entry's short id and is told which card
    is its own. store: the memory store, to tell a guide that its card is empty although it knows
    things. designer: for the interior designer, who only reads: no ids, and nothing when the
    board is empty."""
    db = households()
    key, me = board_key(context), board_person(context)
    entries = db.board_entries(key, context.property_id)
    if designer and not entries:
        return None
    people = db.board_people(key)
    family = len(people) > 1 or bool(context.household_id)
    rooms: dict[str, str] = {}
    if any(e.kind == "room" for e in entries):
        try:
            rooms = prompts.room_short_names(get_twin(context.property_id))
        except Exception:  # no twin for this house: the wishes are listed under the room's id
            log.warning("no room names for the board of %s", context.property_id)

    def listed(found: list[BoardEntry]) -> str:
        return "; ".join(e.text if designer else f"[{_short(e)}] {e.text}" for e in found)

    if designer:
        lines = [
            "## The family board",
            "What each person wants from a home, in their own words. The guide's brief may leave some out: go by the "
            "board. A must-have is a hard rule wherever it bears on the room you are arranging. Meet the wishes for that "
            "room, and say in your answer where two people's wishes for it conflict.",
        ]
    else:
        lines = [
            "## The family board",
            "What each person wants from a home, read from the board for this reply. Each entry is `[id] text`: the id "
            "is what `board_edit` and `board_remove` take.",
        ]
    for pid, name in people:
        mine = pid == me and not designer
        if not family:
            title = "The buyer" if designer else "Your person"
        elif pid == BOARD_SHARED:
            title = f"{name}, the whole family (said to the shared guide on the big screen)" + (": your card" if mine else "")
        else:
            title = name + (" (your person)" if mine else "")
        lines.append(f"### {title}")
        own = [e for e in entries if e.person_id == pid]
        for kind, label in _KINDS:
            if found := [e for e in own if e.kind == kind]:
                lines.append(f"{label}: {listed(found)}")
        wishes: dict[str, list[BoardEntry]] = {}
        for e in own:
            if e.kind == "room" and e.room_id:
                wishes.setdefault(e.room_id, []).append(e)
        for room_id, found in wishes.items():
            where = rooms.get(room_id, room_id) + (f" (room_id {room_id})" if designer else "")
            lines.append(f"{where}: {listed(found)}")
        if own:
            continue
        if mine and _remembers(store, context):
            lines.append(
                "(nothing yet) This card is yours to keep, and it is empty although your memory files say what they want. "
                "Put what you already know on it now with `board_post`, one short fact per call: must-haves, taste, "
                "dislikes, wishes for rooms of this house. Leave out what was said in confidence, and health or money details."
            )
        else:
            lines.append("(nothing yet)")
    return "\n".join(lines)


def _own_entry(context: AgentContext, ref: str) -> BoardEntry | str:
    """One entry of this agent's own card by the short id it was shown (or the whole id); else what
    to tell the agent."""
    ref = str(ref).strip().strip("[]`# ").lower()
    hits = [e for e in households().board_entries(board_key(context)) if e.entry_id.startswith(ref)] if len(ref) >= 6 else []
    if len(hits) != 1:
        return f"Error: no entry {ref!r} on the board. Use the id in brackets before an entry on the family board."
    if hits[0].person_id != board_person(context):
        return "Error: that entry is on someone else's card: only they and their guide change it. Tell your person whose wish it is."
    return hits[0]


def _too_long(text: str) -> str | None:
    words = len(text.split())
    if words <= ENTRY_WORDS:
        return None
    return f"Error: that is {words} words. An entry is one short fact, 20 words at most: post each fact on its own."


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


@tool
def board_post(kind: BoardKind, text: str, runtime: Runtime, room_id: str | None = None) -> str:
    """Write one thing your person wants on their card of the family board, where the whole family
    and every guide reads it. Only what they want from a home: not your notes or verdict on this
    house.

    kind: "must" a must-have or hard rule; "like" style and taste; "avoid" a dislike; "about" who
    they are and how they live, only what bears on a home; "room" a wish for one room of this
    house: what they want in it, or how they want it designed.
    text: one short fact, 20 words at most, in their own words where you can.
    room_id: for kind "room" only: the room, from the room directory.
    Never post what they told you in confidence, nor health or money details."""
    ctx = runtime.context
    if kind == "room":
        if not room_id:
            return 'Error: a "room" entry needs the room_id of the room it is for, from the room directory.'
        if not any(r.room_id == room_id for r in get_twin(ctx.property_id).rooms):
            return f"Error: unknown room_id {room_id!r}. Use a room_id from the room directory."
    elif room_id:
        return 'Error: only a "room" entry takes a room_id. Post it as kind "room", or leave room_id out.'
    if error := _too_long(text):
        return error
    person = board_person(ctx)
    try:
        entry = households().add_entry(board_key(ctx), person, kind, text, "guide", room_id, ctx.property_id)
    except ValueError as e:
        return f"Error: {e}."
    _emit(runtime, "board.changed", {"person_id": person})
    return f"On the board as [{_short(entry)}]: {entry.text}"


@tool
def board_edit(entry: str, text: str, runtime: Runtime) -> str:
    """Change the words of one entry on your person's card: they changed their mind, or you got it
    wrong. Use it instead of posting a second entry that contradicts the first.
    entry: the id in brackets before the entry on the family board. text: the new short fact."""
    found = _own_entry(runtime.context, entry)
    if isinstance(found, str):
        return found
    if error := _too_long(text):
        return error
    try:
        edited = households().edit_entry(found.entry_id, text, "guide")
    except ValueError as e:
        return f"Error: {e}."
    if edited is None:
        return "Error: that entry is no longer on the board."
    _emit(runtime, "board.changed", {"person_id": edited.person_id})
    return f"[{_short(edited)}] now says: {edited.text}"


@tool
def board_remove(entry: str, runtime: Runtime) -> str:
    """Take one entry off your person's card: it no longer holds, or it should not have been posted.
    entry: the id in brackets before the entry on the family board."""
    found = _own_entry(runtime.context, entry)
    if isinstance(found, str):
        return found
    households().delete_entry(found.entry_id)
    _emit(runtime, "board.changed", {"person_id": found.person_id})
    return f"Taken off the board: {found.text}"


# Every guide has these, with or without a family, whether or not agents may talk to each other.
BOARD_TOOLS = [board_post, board_edit, board_remove]
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
            # Kept apart: each agent knows its own person and, of the others, only what their guides
            # chose to put on the family board.
            if ctx.member_name is None:
                text = (
                    "## The family\n"
                    f"The {h.name} family is looking at this house together on the big screen: {others}. You are the shared "
                    "guide. Each member also has their own agent on their phone. You cannot reach those agents or what "
                    "they know, only what they posted on the family board: when a question is about one member's needs "
                    "and the board does not say, ask the person."
                )
            else:
                text = (
                    "## Your family\n"
                    f"You are {ctx.member_name}'s own agent. The {h.name} family is looking at this house together, and "
                    f"each member has their own agent. The others: {others}. Your memory and this conversation are "
                    f"{ctx.member_name}'s alone: you know nothing of what the others told their agents, and they know "
                    "nothing of yours, beyond what each guide posts on the family board.\n"
                    "- Speak for your person, not for the whole family.\n"
                    f"- You are talking with {ctx.member_name}: say \"you\" and \"your\" to them, and \"I\" for yourself. "
                    f"Never refer to {ctx.member_name} by name or as \"they\" in what you say. Only other family members are "
                    "named and spoken of in the third person.\n"
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
            f"- You are talking with {ctx.member_name or 'your person'}: say \"you\" and \"your\" to them, and \"I\" for "
            "yourself. Never refer to them by name or as \"they\". Only other family members are named and spoken of in "
            "the third person.\n"
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


class FamilyBoardMiddleware(AgentMiddleware):
    """Shows the agent the whole family board as it is at this model call, read from the database:
    what every person wants, by kind and by room. A guide posts in the middle of its own turn, and
    the others' guides and hands change the board while it works, so a copy made at the start of a
    conversation would be wrong by the second reply.
    designer: for the interior designer, which arranges rooms everyone shares and writes nothing."""

    def __init__(self, designer: bool = False) -> None:
        super().__init__()
        self.designer = designer

    def _extend(self, request):
        from deepagents.middleware._utils import append_to_system_message

        try:
            text = board_for_agent(request.runtime.context, request.runtime.store, self.designer)
        except Exception:  # the board is a help, not a need: the reply goes on without it
            log.exception("the family board could not be read")
            return request
        return request.override(system_message=append_to_system_message(request.system_message, text)) if text else request

    def wrap_model_call(self, request, handler):
        return handler(self._extend(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._extend(request))
