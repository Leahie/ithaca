"""The Ithaca agent graph: create_deep_agent wired with our prompt, tools and memory.

Files the agent writes go to one of two places (PLAN.md section 3):
- /memories/**  -> StoreBackend, namespace (user_id,). Persistent across threads and houses (B4).
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
from services.api.agent.family import FAMILY_TOOLS, FamilyContextMiddleware
from services.api.agent.models import get_model
from services.api.agent.design_tools import (
    catalog_add,
    catalog_search,
    delete_from_inventory,
    design_apply_layout,
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
        routes={MEMORY_ROOT: StoreBackend(namespace=lambda rt: memory_namespace(rt.context.user_id))},
    )
    designer = {
        "name": "designer",
        "description": (
            "Interior designer. Arranges the user's own furniture in one room and returns saved layout_ids. "
            "Give it the room_id, the pieces to use, what the user wants from the room, and their preferences."
        ),
        "system_prompt": prompts.DESIGNER_PROMPT,
        "tools": [inventory_search, *PROPERTY_TOOLS, sun_by_room, propose_layouts],
        "middleware": [PropertyContextMiddleware()],
        **({"model": get_model("designer")} if model is None else {}),
    }
    return create_deep_agent(
        model=model or get_model("main"),
        tools=[*PROPERTY_TOOLS, *SUN_TOOLS, *AREA_TOOLS, *SOUND_TOOLS, inventory_search, *UI_TOOLS, design_show_variants, design_apply_layout, catalog_search, catalog_add, remove_from_room, delete_from_inventory, *FAMILY_TOOLS],
        system_prompt=prompts.main_persona(subagents=("designer",)),  # analyst and scout don't exist yet
        subagents=[designer],
        middleware=[PropertyContextMiddleware(), FamilyContextMiddleware()],
        memory=memory_files(property_id),
        permissions=[FilesystemPermission(operations=["write"], paths=[INVENTORY_FILE], mode="deny")],
        backend=backend,
        context_schema=AgentContext,
        store=store,
        checkpointer=checkpointer,
        name="ithaca",
    )
