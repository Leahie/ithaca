"""The furniture chain: guide -> designer in the background -> solver -> saved layouts -> cards on screen."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore

from services.api.agent import views
from services.api.agent import design_gate, house_plans
from services.api.agent.context import AgentContext
from services.api.agent.design_tools import BACKGROUND_DESIGN
from services.api.agent.graph import build_designer, run_designer
from services.api.agent.runner import event_message
from services.api.agent.tests.test_graph import PROPERTY, ScriptedModel, call, run
from services.api.furniture import store
from services.api.sim import layout_solver
from services.api.twin.service import get_twin

LIVING_ROOM = "tt6gm2a0463x9wmnx7rn6gugc"


@pytest.fixture(autouse=True)
def open_gate():
    design_gate.reset()
    house_plans.reset()
    yield
    design_gate.reset()
    house_plans.reset()


@pytest.fixture
def furniture(monkeypatch):
    fresh = store.FurnitureStore(":memory:")
    monkeypatch.setattr(store, "_default", fresh)
    return fresh


def test_guide_starts_the_designer_in_the_background_then_shows_valid_layouts(furniture):
    sofa, table, _chair = [str(i.item_id) for i in furniture.list_items("alice")]
    saved = lambda: [str(l.layout_id) for l in furniture.list_proposals("alice")]  # noqa: E731
    memory = InMemoryStore()
    alice = AgentContext(user_id="alice", property_id=PROPERTY)

    # 1. the guide hands the job over and does not wait for it
    guide = ScriptedModel(script=[
        call("design_in_background", room_id=LIVING_ROOM, brief="Use the sofa and coffee table."),
        AIMessage(content="I'm working on it. Keep looking around."),
    ])
    started = run(guide, memory, user="alice", thread="t1", text="lay out the living room")
    job = next(e[1] for e in started if e[0] == "ui")
    assert job["type"] == BACKGROUND_DESIGN and job["payload"]["room_id"] == LIVING_ROOM and furniture.list_proposals("alice") == []

    # 2. the designer, off to the side: look at the furniture, then ask the solver
    model = ScriptedModel(script=[
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
    ])
    report = run_designer(build_designer(PROPERTY, store=memory, model=model), alice, **job["payload"])
    assert report == "Three layouts saved."

    # 3. back in the guide, on the layouts_ready event: show what the designer made
    guide = ScriptedModel(script=[
        lambda _messages: call("design_show_variants", layout_ids=saved()),
        AIMessage(content="Here are three ways to set up the living room."),
    ])
    out = run(guide, memory, user="alice", thread="t1", text=event_message("layouts_ready", report))

    assert sofa in model.tool_results[0]  # the designer's tool saw alice's furniture (context reached it)
    layouts = furniture.list_proposals("alice", PROPERTY, LIVING_ROOM)
    assert len(layouts) == 3 and furniture.list_proposals("bob") == []
    assert furniture.house_pieces("alice", PROPERTY) == []  # proposals are not furniture in the house (F8)

    twin = get_twin(PROPERTY)
    room = next(r for r in twin.rooms if r.room_id == LIVING_ROOM)
    items = [layout_solver.Item(str(i.item_id), i.label, i.item_class, i.dims_m.w, i.dims_m.d, i.dims_m.h) for i in furniture.list_items("alice")[:2]]
    for layout in layouts:  # B2 holds for what was saved, and pieces sit on this room's floor
        poses = {str(p.item_id): layout_solver.Pose(p.position.x, p.position.z, p.yaw_deg) for p in layout.placements}
        assert layout_solver.violations(room, twin.openings, items, poses) == []
        assert all(p.position.y == views.floor_height(twin, LIVING_ROOM) for p in layout.placements)  # on the real floor

    shown = [e[1] for e in out if e[0] == "ui"]
    # cards for all options, option 1 placed in the room, and the user taken there: no tab switch
    assert [e["type"] for e in shown] == ["design.showVariants", "layout.select", "viewer.goto", "viewer.look"]  # the last: once around the room
    assert sorted(shown[0]["payload"]["layout_ids"]) == sorted(str(l.layout_id) for l in layouts)
    assert shown[1]["payload"]["layout_id"] == shown[0]["payload"]["layout_ids"][0] and shown[2]["payload"]["room_id"] == LIVING_ROOM


def test_suggested_catalog_pieces_reach_the_chat_as_cards(furniture):
    """catalog_suggest sends the pieces with pictures and sizes; it adds nothing by itself."""
    model = ScriptedModel(
        script=[
            call("catalog_suggest", catalog_ids=["B07B78LXYF", "B0714QGLSD", "made-up"], room_id=LIVING_ROOM),
            call("catalog_suggest", catalog_ids=["B07B78LXYF", "B0714QGLSD"], room_id=LIVING_ROOM, near_item_id="desk-1"),
            AIMessage(content="Two chairs that would suit a desk."),
        ]
    )
    out = run(model, InMemoryStore(), user="alice", thread="t1", text="add a chair behind the desk")
    assert model.tool_results[0].startswith("Error")  # an unknown id shows nothing
    (card,) = [e[1] for e in out if e[0] == "ui"]
    assert card["type"] == "catalog.suggest" and card["payload"]["near_item_id"] == "desk-1"
    assert [p["catalog_id"] for p in card["payload"]["pieces"]] == ["B07B78LXYF", "B0714QGLSD"]
    assert all(p["thumbnail_url"].endswith(".jpg") and p["dims_m"]["w"] > 0 for p in card["payload"]["pieces"])
    assert len(furniture.list_items("alice")) == 3  # only the seeded pieces: showing is not adding


def test_furnishing_with_a_set_adds_its_pieces_and_starts_the_designer(furniture, monkeypatch):
    """One set per room: its pieces (with their counts) go into the user's furniture, and the designer gets them as its brief."""
    import trimesh

    from services.api.objects.providers import catalog

    def box(cid):  # a box of the piece's own size stands in for its model: no download in tests
        _id, _cls, _label, w, d, h = catalog.BY_ID[cid]
        return trimesh.creation.box(extents=(w, h, d)).export(file_type="glb")

    monkeypatch.setattr(catalog, "fetch", box)
    before = len(furniture.list_items("alice"))
    model = ScriptedModel(script=[call("catalog_sets"), call("furnish_with_set", set_id="jonathan_bedroom", room_id=LIVING_ROOM), AIMessage(content="I chose the walnut bedroom set.")])
    out = run(model, InMemoryStore(), user="alice", thread="t1", text="furnish this room")

    assert "Jonathan walnut bedroom" in model.tool_results[0] and "nightstand ×2" in model.tool_results[0]
    added = furniture.list_items("alice")[before:]
    wanted = sum(catalog.SET_COUNTS.get(("jonathan_bedroom", c), 1) for c in catalog.SET_BY_ID["jonathan_bedroom"]["pieces"])
    assert len(added) == wanted and sum(i.item_class == "nightstand" for i in added) == 2
    job = next(e[1] for e in out if e[0] == "ui" and e[1]["type"] == BACKGROUND_DESIGN)
    assert job["payload"]["room_id"] == LIVING_ROOM and all(str(i.item_id) in job["payload"]["brief"] for i in added)


def test_rooms_are_planned_together_and_shown_one_at_a_time(furniture):
    """Asking for two rooms starts the designer on both at once; the second room's layouts are
    held until the user has decided on the first (human in the loop between rooms)."""
    twin = get_twin(PROPERTY)
    other = next(r.room_id for r in twin.rooms if r.room_id != LIVING_ROOM)
    conversation = design_gate.key(None, "alice")
    both = ScriptedModel(script=[
        call("design_in_background", room_id=LIVING_ROOM, brief="whatever fits"),
        call("design_in_background", room_id=other, brief="whatever fits"),
        call("design_in_background", room_id=other, brief="whatever fits"),  # asked twice: not started twice
        AIMessage(content="ok"),
    ])
    out = run(both, InMemoryStore(), user="alice", thread="t1")
    assert [e[1]["payload"]["room_id"] for e in out if e[0] == "ui" and e[1]["type"] == BACKGROUND_DESIGN] == [LIVING_ROOM, other]
    assert "already being arranged" in both.tool_results[2]

    assert design_gate.ready(conversation, LIVING_ROOM, "living room ready")  # the first to finish is shown
    assert not design_gate.ready(conversation, other, "next room ready")  # the second waits for the user
    assert design_gate.user_spoke(conversation) == "next room ready"  # they decided: now it comes up
    assert design_gate.user_spoke(conversation) is None


def test_planning_a_room_someone_else_has_plans_for_is_raised_with_the_person_first(furniture):
    """Two people's agents on one house share no context, only who has plans for which room."""
    from services.api.agent.runner import stream_turn
    from services.api.agent.graph import build_agent
    from services.api.realtime.agent_bridge import others_plans

    maya = AgentContext(user_id="m-maya", property_id=PROPERTY, session_id="s", household_id="h", member_name="Maya", house_user_id="demo")
    leo = AgentContext(user_id="m-leo", property_id=PROPERTY, session_id="s", household_id="h", member_name="Leo", house_user_id="demo")

    def ask(who, **extra):
        model = ScriptedModel(script=[call("design_in_background", room_id=LIVING_ROOM, brief="whatever fits", **extra), AIMessage(content="ok")])
        out = list(stream_turn(build_agent(PROPERTY, store=InMemoryStore(), model=model), "plan the living room", who, who.thread_id))
        return model.tool_results[0], [e for e in out if e[0] == "ui" and e[1]["type"] == BACKGROUND_DESIGN]

    assert ask(maya)[1] and others_plans(maya) == ""  # her own plan is not news to her
    said, started = ask(leo)
    assert started == [] and "Hold on: Maya is having layouts drawn up" in said
    assert "Maya is having layouts drawn up for" in others_plans(leo)
    assert ask(leo, go_ahead_over_other_plans=True)[1]  # Leo said to go ahead anyway
