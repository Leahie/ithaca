"""The observer: a second deep agent that studies how the user tours, in the background.

The guide (graph.py) talks to the user. The observer never does. Every so often the bridge hands it
the tour log. It looks for patterns, checks them against the house (sun, room sizes), writes what
it learns to the user's memory as *inferred* preferences, and may pass one short note to the guide.

It is a separate create_deep_agent graph that shares the guide's memory store. It can't be a
`task` subagent, because those run inside the guide's turn and would make the user wait, and
deepagents' async subagents need a remote Agent Protocol server.
"""

from __future__ import annotations

from deepagents import FilesystemPermission, create_deep_agent
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models import BaseChatModel
from langgraph.store.base import BaseStore

from services.api.agent.context import AgentContext
from services.api.agent.graph import HOUSE_ROOT, INVENTORY_FILE, MEMORY_ROOT, PropertyContextMiddleware, memory_backend, memory_files
from services.api.agent.models import get_model
from services.api.agent.prompts import SILENT
from services.api.agent.tools import PROPERTY_TOOLS, SUN_TOOLS
from services.api.agent.tour import TOUR_LOGS
from services.api.twin.service import get_twin

OBSERVER_PROMPT = f"""\
You are the observer for Ithaca, a website where people tour a house before they buy it. A guide
talks with the user. You never talk to the user. You watch how they tour and work out what it
says about them, so the guide can serve them better. You run in the background a few times per
tour.

## Each run
1. Call `get_tour_log` for what the user has done so far.
2. Read their memory (in your context) so you don't repeat what is already known.
3. Look for patterns, then check them against the house with your tools: `sun_by_room` and
   `daylight_at` for light, `get_room_geometry` for size and windows. "They linger in three rooms"
   is an observation. "The three rooms they linger in are the only ones with morning sun" is a
   finding.
4. Save what holds up (see Writing memory).
5. Reply with one note for the guide, or with exactly `{SILENT}`.

## What counts as a pattern
- Where the time went: long or repeated visits, rooms skipped entirely, rooms left within seconds.
- What they checked: which views they opened and where, which times of day they looked at.
- What their questions keep circling: light, noise, storage, a home office, children, resale.
- Something the visited or skipped rooms have in common: floor, size, direction, brightness.
- Gaps between what they said they want and where they actually spend time.

Be careful with evidence:
- One visit or one question is not a pattern. Two or more matching signals are.
- Visits the guide led say less about the user than visits they chose.
- Dwell time is weak on its own (they may have stepped away). Pair it with something else.
- If you find nothing new, save nothing and reply `{SILENT}`. That is a good outcome.

## Writing memory
- `/memories/properties/<property_id>.md`: keep one section `## Tour observations`. Rewrite that
  section each run with the current picture, at most six bullets, each with its evidence.
- `/memories/preferences.md`: add a preference only when the evidence is strong. Put it under
  **Soft**, mark it `inferred`, and state the evidence and today's date. Never write to **Hard**,
  never change a `stated` line, and never contradict one: what the user said beats what you saw.
- Don't touch `/memories/profile.md` or `/memories/inventory.md`.
- Record behavior and preferences about homes only. No guesses about health, money, family
  plans, or anything about who the user is.
- If a file doesn't exist yet, create it with `write_file`. Otherwise use `edit_file`.

## The note for the guide
Your whole reply is the note, and nothing else: no recap of your findings, no headings, no lists,
no "Note for the guide:" label. At most two sentences the guide can act on right now: the pattern,
and what to offer. Example: "They have gone back to the kitchen three times and set the clock to
the morning there twice; the kitchen gets sun 08:40-15:20 in December. Offer to show the breakfast
corner at 9 am." Use room names the way the room directory does. Send a note only when it would change what
the guide does next. Otherwise reply with exactly `{SILENT}`.
"""


@tool
def get_tour_log(runtime: ToolRuntime[AgentContext]) -> str:
    """What the user has done on this tour: time per room, visits, views opened, times of day
    checked, and what they said."""
    log = TOUR_LOGS.get(runtime.context.session_id or "")
    if log is None:
        return "No tour activity has been recorded for this session."
    return log.summary(get_twin(runtime.context.property_id))


def build_observer(property_id: str, *, store: BaseStore, model: BaseChatModel | None = None):
    return create_deep_agent(
        model=model or get_model("observer"),
        tools=[get_tour_log, *PROPERTY_TOOLS, *SUN_TOOLS],
        system_prompt=OBSERVER_PROMPT,
        middleware=[PropertyContextMiddleware()],
        memory=memory_files(property_id),
        permissions=[
            FilesystemPermission(
                operations=["write"], paths=[INVENTORY_FILE, f"{HOUSE_ROOT}**", f"{MEMORY_ROOT}profile.md"], mode="deny"
            )
        ],
        backend=memory_backend(),
        context_schema=AgentContext,
        store=store,
        name="ithaca-observer",
    )


def run_observer(observer, context: AgentContext) -> str | None:
    """One background run. Returns the note for the guide, or None if it has nothing to say."""
    result = observer.invoke(
        {"messages": [{"role": "user", "content": "Run your analysis of the tour so far."}]},
        context=context,
    )
    note = result["messages"][-1].text.strip()
    return None if not note or note == SILENT else note
