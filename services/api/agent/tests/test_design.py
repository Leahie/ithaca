"""The furniture chain: guide -> designer subagent -> solver -> saved layouts -> cards on screen."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore

from services.api.agent import views
from services.api.agent.tests.test_graph import PROPERTY, ScriptedModel, call, run
from services.api.furniture import store
from services.api.sim import layout_solver
from services.api.twin.service import get_twin

LIVING_ROOM = "tt6gm2a0463x9wmnx7rn6gugc"


@pytest.fixture
def furniture(monkeypatch):
    fresh = store.FurnitureStore(":memory:")
    monkeypatch.setattr(store, "_default", fresh)
    return fresh


def test_guide_delegates_to_the_designer_and_shows_valid_layouts(furniture):
    sofa, table, _chair = [str(i.item_id) for i in furniture.list_items("alice")]
    saved = lambda: [str(l.layout_id) for l in furniture.list_layouts("alice")]  # noqa: E731
    model = ScriptedModel(
        script=[
            # the guide hands the job over
            call("task", subagent_type="designer", description=f"Lay out room {LIVING_ROOM} with the sofa and coffee table."),
            # the designer: look at the furniture, then ask the solver
            call("inventory_search"),
            call(
                "propose_layouts",
                room_id=LIVING_ROOM,
                item_ids=[sofa, table],
                idea="Sofa wall",
                wishes=[
                    {"kind": "against_wall", "item": sofa, "target": "solid"},
                    {"kind": "in_front_of", "item": table, "target": sofa},
                ],
            ),
            AIMessage(content="Three layouts saved."),
            # back in the guide: show what the designer made
            lambda _messages: call("design_show_variants", layout_ids=saved()),
            AIMessage(content="Here are three ways to set up the living room."),
        ]
    )
    out = run(model, InMemoryStore(), user="alice", thread="t1", text="lay out the living room")

    assert sofa in model.tool_results[0]  # the subagent's tool saw alice's furniture (context reached it)
    layouts = furniture.list_layouts("alice", PROPERTY, LIVING_ROOM)
    assert len(layouts) == 3 and furniture.list_layouts("bob") == []

    twin = get_twin(PROPERTY)
    room = next(r for r in twin.rooms if r.room_id == LIVING_ROOM)
    items = [layout_solver.Item(str(i.item_id), i.label, i.item_class, i.dims_m.w, i.dims_m.d, i.dims_m.h) for i in furniture.list_items("alice")[:2]]
    for layout in layouts:  # B2 holds for what was saved, and pieces sit on this room's floor
        poses = {str(p.item_id): layout_solver.Pose(p.position.x, p.position.z, p.yaw_deg) for p in layout.placements}
        assert layout_solver.violations(room, twin.openings, items, poses) == []
        assert all(p.position.y == views.floor_height(twin, LIVING_ROOM) for p in layout.placements)  # on the real floor

    shown = [e[1] for e in out if e[0] == "ui"]
    # cards for all options, option 1 placed in the room, and the user taken there: no tab switch
    assert [e["type"] for e in shown] == ["design.showVariants", "layout.select", "viewer.goto"]
    assert sorted(shown[0]["payload"]["layout_ids"]) == sorted(str(l.layout_id) for l in layouts)
    assert shown[1]["payload"]["layout_id"] == shown[0]["payload"]["layout_ids"][0] and shown[2]["payload"]["room_id"] == LIVING_ROOM
