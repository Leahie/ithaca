"""The Ithaca agent graph: create_deep_agent wired with our prompt, tools and memory.

Files the agent writes go to one of these places (PLAN.md section 3):
- /memories/**       -> StoreBackend, namespace (context.memory_user,): the person's own, or the
                        family's for a family's shared guide (B4). It outlives sessions and restarts.
- /memories/house/** -> StoreBackend, one namespace per house: the furniture register the app
                        saves each time the house changes (furniture/ledger.py). Read-only.
- anything else      -> StateBackend. Scratch space that lives only in the current thread.

On every model call the agent is shown, read fresh: the property, its family, its person's memory
files (LiveMemoryMiddleware), the family board of what each person wants (FamilyBoardMiddleware,
agent/family.py) and what stands in the house (HouseRegisterMiddleware). Nothing about the person,
the family's wishes or the furniture is answered from a copy made earlier in the conversation.
/memories/inventory.md is derived from the DB, so the agent can't write it (B3).

`store` and `checkpointer` are injected, so the REPL can use SQLite and the API can use Postgres.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from services.api.agent import memory_files as memory_io
from services.api.agent import prompts
from services.api.agent.context import AgentContext
from services.api.agent.family import BOARD_TOOLS, FamilyBoardMiddleware, FamilyContextMiddleware, family_tools
from services.api.agent.models import get_model
from services.api.agent.design_tools import (
    ask_choice,
    catalog_add,
    catalog_search,
    catalog_sets,
    catalog_suggest,
    delete_from_inventory,
    design_apply_layout,
    design_in_background,
    furnish_room,
    furnish_with_set,
    design_show_variants,
    inventory_search,
    place_piece,
    propose_layouts,
    remove_from_room,
)
from services.api.agent.tools import AREA_TOOLS, PROPERTY_TOOLS, SOUND_TOOLS, SUN_TOOLS, UI_TOOLS, sun_by_room
from services.api.furniture import ledger
from services.api.furniture.store import house_key
from services.api.twin.service import get_twin

MEMORY_ROOT = "/memories/"
HOUSE_ROOT = f"{MEMORY_ROOT}house/"
INVENTORY_FILE = f"{MEMORY_ROOT}inventory.md"


def memory_files(property_id: str) -> list[str]:
    """The person's files an agent is shown. inventory.md is not one of them: what they own, and
    where each piece stands, is in the house register, which cannot go stale."""
    return [
        f"{MEMORY_ROOT}profile.md",
        f"{MEMORY_ROOT}preferences.md",
        f"{MEMORY_ROOT}properties/{property_id}.md",
    ]


def memory_namespace(user_id: str) -> tuple[str, ...]:
    return (user_id,)


def memory_backend() -> CompositeBackend:
    """Where an agent's files go (see the top of this file)."""
    return CompositeBackend(
        default=StateBackend(),
        routes={
            HOUSE_ROOT: StoreBackend(namespace=lambda rt: ledger.namespace(house_key(rt.context.furniture_user))),
            MEMORY_ROOT: StoreBackend(namespace=lambda rt: memory_namespace(rt.context.memory_user)),
        },
    )


# What an agent may not write: the two files the app itself keeps.
READ_ONLY = [FilesystemPermission(operations=["write"], paths=[INVENTORY_FILE, f"{HOUSE_ROOT}**"], mode="deny")]


class _PerCall(AgentMiddleware):
    """Appends text to the system prompt on every model call. Subclasses say what (`text`)."""

    def text(self, request) -> str | None:
        raise NotImplementedError

    def _extend(self, request):
        text = self.text(request)
        return request.override(system_message=append_to_system_message(request.system_message, text)) if text else request

    def wrap_model_call(self, request, handler):
        return handler(self._extend(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._extend(request))


class LiveMemoryMiddleware(_PerCall):
    """Shows the agent its person's memory files as they are at this model call.

    deepagents' own `memory=` reads the files once per thread and keeps that copy in the thread's
    saved state. A thread here is one person in one house and lasts across visits and restarts, so
    the copy went stale after the first turn: what the person changed on the /me page, what the
    observer wrote, and the agent's own edits once the history was summarised, were never shown
    again. Reading the store on each call is three small lookups."""

    def text(self, request) -> str:
        ctx: AgentContext = request.runtime.context
        store = request.runtime.store
        sections = []
        for path in memory_files(ctx.property_id):
            content = memory_io.read_file(store, ctx.memory_user, path.removeprefix(MEMORY_ROOT)) if store is not None else None
            if content and content.strip():
                sections.append(f"{path}\n\n{content.rstrip()}")
        body = "\n\n".join(sections) or "(Nothing is saved about this person yet.)"
        return (
            f"<agent_memory>\n{body}\n</agent_memory>\n"
            "These are this person's memory files as they are right now, read for this reply. A file that is not shown "
            "does not exist yet: create it with `write_file`. Change an existing one with `edit_file`."
        )


class HouseRegisterMiddleware(_PerCall):
    """Shows the agent what stands in the house at this model call, read from the database: every
    piece by its number, the room and spot it is in, whose it is and who put it there, and the
    last changes anyone made (furniture/ledger.py). Every agent that furnishes the house reads the
    same register, so none of them answers from what it remembers."""

    def __init__(self, closing: str | None = None) -> None:
        """closing: words to end the instructions with, after the register. They are the last
        thing the model reads before it answers, which is where a rule about the answer holds best."""
        super().__init__()
        self.closing = closing

    def text(self, request) -> str:
        ctx: AgentContext = request.runtime.context
        register = ledger.for_agent(ctx.furniture_user, ctx.property_id, ctx.user_id)
        return f"{register}\n\n{self.closing}" if self.closing else register


class PropertyContextMiddleware(AgentMiddleware):
    """Appends the current property's context (rooms, local time, caveats) on every model call."""

    def _extend(self, request):
        twin = get_twin(request.runtime.context.property_id)
        # Rounded to the hour so the system prompt stays stable for prompt caching.
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        context = prompts.render_property_context(twin, now)
        return request.override(system_message=append_to_system_message(request.system_message, context))

    def wrap_model_call(self, request, handler):
        return handler(self._extend(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._extend(request))


def build_agent(
    property_id: str,
    *,
    store: BaseStore,
    checkpointer: BaseCheckpointSaver | None = None,
    model: BaseChatModel | None = None,
):
    """One graph per property, because the memory file list includes that property's notes.

    Run it with `context=AgentContext(...)` and `config={"configurable": {"thread_id": ...}}`.
    """
    return create_deep_agent(
        model=model or get_model("main"),
        tools=[
            *PROPERTY_TOOLS, *SUN_TOOLS, *AREA_TOOLS, *SOUND_TOOLS, inventory_search, *UI_TOOLS, ask_choice,
            design_in_background, design_show_variants, design_apply_layout, furnish_room, catalog_sets, furnish_with_set, catalog_search,
            catalog_suggest, catalog_add, place_piece, remove_from_room, delete_from_inventory, *family_tools(),
            *BOARD_TOOLS,  # every guide keeps its own person's card on the family board
        ],
        system_prompt=prompts.main_persona(subagents=()),  # no helpers through `task`: the designer runs in the background
        # In this order: what changes least comes first, so more of the prompt stays cached.
        middleware=[
            PropertyContextMiddleware(), FamilyContextMiddleware(), LiveMemoryMiddleware(), FamilyBoardMiddleware(),
            HouseRegisterMiddleware(prompts.BEFORE_YOU_ANSWER),
        ],
        permissions=READ_ONLY,
        backend=memory_backend(),
        context_schema=AgentContext,
        store=store,
        checkpointer=checkpointer,
        name="ithaca",
    )


def build_designer(property_id: str, *, store: BaseStore, model: BaseChatModel | None = None):
    """The interior designer as its own agent. The session bridge runs it in the background when
    the guide calls `design_in_background`, so the guide stays free to talk and move meanwhile.
    It has no memory of its own: the guide's brief carries what matters."""
    return create_deep_agent(
        model=model or get_model("designer"),
        tools=[inventory_search, *PROPERTY_TOOLS, sun_by_room, propose_layouts],
        system_prompt=prompts.DESIGNER_PROMPT,
        # The board: the rooms are shared, so the designer reads what everyone wants, not only the brief.
        middleware=[PropertyContextMiddleware(), FamilyBoardMiddleware(designer=True), HouseRegisterMiddleware()],
        context_schema=AgentContext,
        store=store,
        name="designer",
    )


def run_designer(designer, context: AgentContext, room_id: str, brief: str, on_progress: Callable[[str, Any], None] | None = None) -> str:
    """One background run. Returns the designer's last words (the layout_ids and what it managed).
    `on_progress` hears it as it works: ("thought", text) for what it says before each step, and
    ("ui", {"type", "payload"}) for what its tools show (the layouts it has solved so far)."""
    last = ""
    for mode, data in designer.stream(
        {"messages": [{"role": "user", "content": f"room_id: {room_id}\n{brief}"}]}, context=context, stream_mode=["updates", "custom"]
    ):
        if mode == "custom":
            if on_progress:
                on_progress("ui", data)
            continue
        for update in data.values():
            for msg in (update or {}).get("messages", []) if isinstance(update, dict) else []:
                if not isinstance(msg, AIMessage):
                    continue
                if not msg.tool_calls:
                    last = msg.text.strip()
                elif msg.text.strip() and on_progress:
                    on_progress("thought", msg.text.strip())
    return last
