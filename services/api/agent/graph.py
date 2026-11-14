"""The Ithaca agent graph: create_deep_agent wired with our prompt, tools and memory.

Files the agent writes go to one of two places (PLAN.md section 3):
- /memories/**  -> StoreBackend, namespace (context.memory_user,): the person's, or the family's for a family's shared guide (B4).
- anything else -> StateBackend. Scratch space that lives only in the current thread.

Memory files are loaded into the system prompt at the start of each thread by deepagents'
`memory=` option. /memories/inventory.md is derived from the DB, so the agent can't write it (B3).

`store` and `checkpointer` are injected, so the REPL can use SQLite and the API can use Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from deepagents.middleware._utils import append_to_system_message
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from services.api.agent import prompts
from services.api.agent.context import AgentContext
from services.api.agent.family import FamilyContextMiddleware, family_tools
from services.api.agent.models import get_model
from services.api.agent.design_tools import (
    catalog_add,
    catalog_search,
    catalog_sets,
    catalog_suggest,
    delete_from_inventory,
    design_apply_layout,
    design_in_background,
    furnish_with_set,
    design_show_variants,
    inventory_search,
    propose_layouts,
    remove_from_room,
)
from services.api.agent.tools import AREA_TOOLS, PROPERTY_TOOLS, SOUND_TOOLS, SUN_TOOLS, UI_TOOLS, sun_by_room
from services.api.twin.service import get_twin

MEMORY_ROOT = "/memories/"
INVENTORY_FILE = f"{MEMORY_ROOT}inventory.md"


def memory_files(property_id: str) -> list[str]:
    return [
        f"{MEMORY_ROOT}profile.md",
        f"{MEMORY_ROOT}preferences.md",
        INVENTORY_FILE,
        f"{MEMORY_ROOT}properties/{property_id}.md",
    ]


def memory_namespace(user_id: str) -> tuple[str, ...]:
    return (user_id,)


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
    backend = CompositeBackend(
        default=StateBackend(),
        routes={MEMORY_ROOT: StoreBackend(namespace=lambda rt: memory_namespace(rt.context.memory_user))},
    )
    return create_deep_agent(
        model=model or get_model("main"),
        tools=[*PROPERTY_TOOLS, *SUN_TOOLS, *AREA_TOOLS, *SOUND_TOOLS, inventory_search, *UI_TOOLS, design_in_background, design_show_variants, design_apply_layout, catalog_sets, furnish_with_set, catalog_search, catalog_suggest, catalog_add, remove_from_room, delete_from_inventory, *family_tools()],
        system_prompt=prompts.main_persona(subagents=()),  # no helpers through `task`: the designer runs in the background
        middleware=[PropertyContextMiddleware(), FamilyContextMiddleware()],
        memory=memory_files(property_id),
        permissions=[FilesystemPermission(operations=["write"], paths=[INVENTORY_FILE], mode="deny")],
        backend=backend,
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
        middleware=[PropertyContextMiddleware()],
        context_schema=AgentContext,
        store=store,
        name="designer",
    )


def run_designer(designer, context: AgentContext, room_id: str, brief: str) -> str:
    """One background run. Returns the designer's last words (the layout_ids and what it managed)."""
    result = designer.invoke({"messages": [{"role": "user", "content": f"room_id: {room_id}\n{brief}"}]}, context=context)
    return result["messages"][-1].text.strip()
