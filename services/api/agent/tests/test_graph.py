"""Graph wiring, run with a scripted fake model (no API key, no network)."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from services.api.agent.context import AgentContext
from services.api.agent.graph import INVENTORY_FILE, build_agent
from services.api.agent.runner import stream_turn

PROPERTY = "onXQLXhfyQv"


class ScriptedModel(BaseChatModel):
    """Replays `script` one message per call and records the system prompt of each call."""

    script: list[AIMessage]
    system_prompts: list[str] = []
    tool_results: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.system_prompts.append(messages[0].text)
        if messages[-1].type == "tool":
            self.tool_results.append(messages[-1].text)
        return ChatResult(generations=[ChatGeneration(message=self.script.pop(0))])


def call(name: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{name}"}])


def run(model: ScriptedModel, store, user: str, thread: str, text: str = "hi"):
    agent = build_agent(PROPERTY, store=store, checkpointer=InMemorySaver(), model=model)
    return list(stream_turn(agent, text, AgentContext(user_id=user, property_id=PROPERTY), thread))


def test_memory_persists_across_threads_and_is_isolated_per_user():
    """Scaffold of evals 1 and 3 (B4), plus read-only inventory (B3)."""
    store = InMemoryStore()
    pref = "Piano must sit on an inside wall: 'sun warps the soundboard'"

    writer = ScriptedModel(
        script=[
            call("write_file", file_path="/memories/preferences.md", content=f"## Hard\n- {pref}\n"),
            call("write_file", file_path=INVENTORY_FILE, content="hacked"),
            AIMessage(content="Noted."),
        ]
    )
    run(writer, store, user="alice", thread="t1")
    assert "permission denied" in writer.tool_results[-1].lower()  # inventory.md is read-only

    alice = ScriptedModel(script=[AIMessage(content="ok")])
    run(alice, store, user="alice", thread="t2")  # new thread, same user
    assert pref in alice.system_prompts[0]
    assert "Room directory" in alice.system_prompts[0]  # property context is appended too

    bob = ScriptedModel(script=[AIMessage(content="ok")])
    run(bob, store, user="bob", thread="t3")
    assert pref not in bob.system_prompts[0]


def test_ui_tool_emits_event_only_for_real_rooms():
    """B1: the agent speaks in ids, and bad ids never reach the UI."""
    from services.api.twin.service import get_twin

    room_id = get_twin(PROPERTY).rooms[0].room_id
    model = ScriptedModel(
        script=[call("viewer_goto", room_id="made-up"), call("viewer_goto", room_id=room_id), AIMessage(content="[silent]")]
    )
    out = run(model, InMemoryStore(), user="alice", thread="t1")
    assert [e for e in out if e[0] == "ui"] == [("ui", {"type": "viewer.goto", "payload": {"room_id": room_id}})]
    assert not [e for e in out if e[0] in ("token", "message")]  # "[silent]" is dropped
