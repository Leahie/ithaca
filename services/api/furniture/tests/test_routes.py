"""Storage, in-house and proposal routes against in-memory stores (docs/specs/furniture.md)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from services.api.agent import provider
from services.api.furniture import store
from services.api.realtime import households as hh
from services.api.twin.service import get_twin

PROPERTY = "onXQLXhfyQv"
LIVING_ROOM = "tt6gm2a0463x9wmnx7rn6gugc"
HOUSE = f"/api/properties/{PROPERTY}/furniture"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_PROVIDER", "scripted")
    monkeypatch.setenv("AGENT_STORAGE", "memory")
    provider._storage.cache_clear()
    monkeypatch.setattr(store, "_default", store.FurnitureStore(":memory:"))
    monkeypatch.setattr(hh, "REPO_ROOT", tmp_path)  # families in a throwaway database
    hh.households.cache_clear()
    from services.api.main import app

    with TestClient(app) as c:
        yield c
    provider._storage.cache_clear()
    hh.households.cache_clear()


def _middle(room_id: str = LIVING_ROOM) -> tuple[float, float]:
    room = next(r for r in get_twin(PROPERTY).rooms if r.room_id == room_id)
    p = Polygon(room.polygon).representative_point()
    return p.x, p.y


def _snap(client, who: dict, item_id: str, room_id: str = LIVING_ROOM):
    x, z = _middle(room_id)
    return client.put(f"{HOUSE}/{item_id}", headers=who, json={"x": x, "z": z, "room_id": room_id, "snap": True})


def test_inventory_is_per_user_and_feeds_the_agents_inventory_file(client):
    alice = {"X-User-Id": "alice"}
    items = client.get("/api/inventory", headers=alice).json()
    assert len(items) == 3 and all(i["user_id"] == "alice" for i in items)  # seeded samples
    assert items[0]["label"] in client.get("/api/memory/inventory.md", headers=alice).text  # B3: derived view

    assert client.delete(f"/api/inventory/{items[0]['item_id']}", headers=alice).status_code == 204
    assert items[0]["label"] not in client.get("/api/memory/inventory.md", headers=alice).text
    assert len(client.get("/api/inventory", headers={"X-User-Id": "bob"}).json()) == 3  # bob's copy is untouched
    assert client.put("/api/memory/inventory.md", headers=alice, content="x").status_code == 409  # read-only


def test_memory_files_round_trip_and_stay_per_user(client):
    alice = {"X-User-Id": "alice", "content-type": "text/plain"}
    assert client.put("/api/memory/properties/h1.md", headers=alice, content="likes the kitchen").status_code == 204
    assert "properties/h1.md" in client.get("/api/memory", headers=alice).json()
    assert client.get("/api/memory/properties/h1.md", headers=alice).text == "likes the kitchen"
    assert client.get("/api/memory/properties/h1.md", headers={"X-User-Id": "bob"}).status_code == 404  # B4
    assert client.delete("/api/memory/properties/h1.md", headers=alice).status_code == 204


def test_removing_a_piece_takes_it_out_of_the_house_for_real(client):
    """F7: one row per piece, and removing it leaves nothing behind, whatever proposals exist."""
    alice = {"X-User-Id": "alice"}
    sofa, table, chair = [i["item_id"] for i in client.get("/api/inventory", headers=alice).json()]
    assert _snap(client, alice, sofa).status_code == 200
    assert _snap(client, alice, table).status_code == 200
    # three placement options for the chair: proposals, not furniture (F8)
    options = client.post("/api/layouts/options", headers=alice, json={"property_id": PROPERTY, "room_id": LIVING_ROOM, "item_id": chair}).json()
    assert len(options) >= 1 and all(any(p["item_id"] == table for p in o["placements"]) for o in options)
    assert chair not in [p["item_id"] for p in client.get(HOUSE, headers=alice).json()]

    assert client.delete(f"{HOUSE}/{table}", headers=alice).status_code == 204
    assert [p["item_id"] for p in client.get(HOUSE, headers=alice).json()] == [sofa]
    assert table in [i["item_id"] for i in client.get("/api/inventory", headers=alice).json()]  # still in storage
    assert client.delete(f"{HOUSE}/{table}", headers=alice).status_code == 404  # not in the house any more

    assert client.delete(f"/api/inventory/{sofa}", headers=alice).status_code == 204
    assert client.get(HOUSE, headers=alice).json() == []  # F3: deleted pieces leave the house
    assert all(p["item_id"] != sofa for o in client.get(f"/api/layouts?property_id={PROPERTY}", headers=alice).json() for p in o["placements"])


def test_the_room_comes_from_the_spot_and_moves_are_the_users_call(client):
    """F4, F5, F9."""
    alice = {"X-User-Id": "alice"}
    sofa, table, _ = [i["item_id"] for i in client.get("/api/inventory", headers=alice).json()]
    placed = _snap(client, alice, sofa).json()
    assert placed["room_id"] == LIVING_ROOM and placed["position"]["y"] > -100
    _snap(client, alice, table)

    at = placed["position"]
    onto_sofa = client.put(f"{HOUSE}/{table}", headers=alice, json={"x": at["x"], "z": at["z"], "yaw_deg": 0})
    assert onto_sofa.status_code == 200 and any("overlap" in w for w in onto_sofa.json()["warnings"])  # saved, with a warning
    assert client.put(f"{HOUSE}/{table}", headers=alice, json={"x": 500, "z": 500}).status_code == 409  # outside every room
    assert client.put(f"{HOUSE}/{table}", headers=alice, json={"x": at["x"], "z": at["z"], "yaw_deg": 359.97}).json()["yaw_deg"] == 0.0

    twin = get_twin(PROPERTY)
    living = next(r for r in twin.rooms if r.room_id == LIVING_ROOM)
    other = next(r for r in twin.rooms if r.floor_id == living.floor_id and r.room_id != LIVING_ROOM and (r.area_m2 or 0) > 4)
    from services.api.furniture.house import room_at

    x, z = _middle(other.room_id)
    moved = client.put(f"{HOUSE}/{table}", headers=alice, json={"x": x, "z": z}).json()
    assert moved["room_id"] == room_at(twin, x, z, living.floor_id).room_id != LIVING_ROOM  # through the doorway: a new room
    rows = client.get(HOUSE, headers=alice).json()
    assert len(rows) == 2 and [r["room_id"] for r in rows if r["item_id"] == table] == [moved["room_id"]]  # F2: moved, not copied


def test_a_spot_under_two_floors_needs_its_floor(client):
    """F4: a room over another is never guessed."""
    twin = get_twin(PROPERTY)
    below, above = next(
        (a, b) for a in twin.rooms for b in twin.rooms
        if a.floor_id != b.floor_id and Polygon(a.polygon).intersection(Polygon(b.polygon)).area > 1
    )
    p = Polygon(below.polygon).intersection(Polygon(above.polygon)).representative_point()
    alice = {"X-User-Id": "alice"}
    _, _, chair = [i["item_id"] for i in client.get("/api/inventory", headers=alice).json()]

    guessed = client.put(f"{HOUSE}/{chair}", headers=alice, json={"x": p.x, "z": p.y})
    assert guessed.status_code == 409 and guessed.json()["detail"]["error"] == "which_floor"
    for room in (below, above):
        from services.api.furniture.house import room_at

        placed = client.put(f"{HOUSE}/{chair}", headers=alice, json={"x": p.x, "z": p.y, "floor_id": room.floor_id}).json()
        assert placed["room_id"] == room_at(twin, p.x, p.y, room.floor_id).room_id
        assert next(r for r in twin.rooms if r.room_id == placed["room_id"]).floor_id == room.floor_id
    # once placed, the piece's own floor settles a later move
    assert client.put(f"{HOUSE}/{chair}", headers=alice, json={"x": p.x, "z": p.y}).status_code == 200


def test_keeping_a_proposal_makes_its_room_exactly_that(client):
    """F8, with the hand-made sample layouts."""
    demo = {"X-User-Id": "demo"}
    from services.api.agent.scripted import demo_layout_ids

    first, second = demo_layout_ids(PROPERTY)[:2]
    a = client.get(f"/api/layouts/{first}", headers=demo).json()
    assert client.post(f"/api/layouts/{first}/select", headers=demo).json()["status"] == "chosen"
    in_room = lambda room: sorted(p["item_id"] for p in client.get(f"{HOUSE}?room_id={room}", headers=demo).json())  # noqa: E731
    assert in_room(a["room_id"]) == sorted(p["item_id"] for p in a["placements"])

    b = client.post(f"/api/layouts/{second}/select", headers=demo).json()
    assert b["status"] == "chosen" and in_room(b["room_id"]) == sorted(p["item_id"] for p in b["placements"])
    by_id = {l["layout_id"]: l["status"] for l in client.get(f"/api/layouts?property_id={PROPERTY}", headers=demo).json()}
    if a["room_id"] == b["room_id"]:
        assert by_id[first] == "proposed" and by_id[second] == "chosen"  # one chosen layout per room
    ids = [p["item_id"] for p in client.get(HOUSE, headers=demo).json()]
    assert len(ids) == len(set(ids))  # F2
    assert client.get("/api/layouts/nope", headers=demo).status_code == 404


def test_a_family_shares_one_pool_and_each_piece_keeps_its_owner(client):
    """F1, F6: the big screen's user hosts; a member's pieces go into the same house."""
    host = {"X-User-Id": "screen"}
    family = client.post("/api/households", headers=host, json={"name": "Us"}).json()
    joined = client.post("/api/households/join", json={"invite_token": family["invite_token"], "name": "Maya"}).json()
    maya = {"X-User-Id": joined["member"]["member_id"]}
    assert client.get("/api/inventory", headers=maya).json()[0]["user_id"] == "screen"  # members start with none of their own

    from packages.schema.inventory import InventoryItem

    lamp = client.get("/api/inventory", headers=host).json()[0] | {"item_id": "00000000-0000-4000-8000-00000000a1a1", "label": "Maya's lamp"}
    store.furniture_store().put_item(InventoryItem.model_validate({**lamp, "user_id": maya["X-User-Id"], "owner_name": None}))

    pool = {i["label"]: i for i in client.get("/api/inventory", headers=host).json()}
    assert pool["Maya's lamp"]["owner_name"] == "Maya" and len(pool) == 4
    assert "Maya's lamp" not in [i["label"] for i in client.get("/api/inventory", headers={"X-User-Id": "bob"}).json()]

    assert _snap(client, host, lamp["item_id"]).status_code == 200  # the house holds a member's piece
    assert _snap(client, {"X-User-Id": "bob"}, lamp["item_id"]).status_code == 404  # but not another family's house

    screens_piece = pool["Sofa"]["item_id"] if "Sofa" in pool else next(i["item_id"] for i in pool.values() if i["user_id"] == "screen")
    assert client.delete(f"/api/inventory/{screens_piece}", headers=maya).status_code == 403  # not hers to delete
    assert client.delete(f"/api/inventory/{lamp['item_id']}", headers=host).status_code == 204  # the host may tidy up
    assert client.get(HOUSE, headers=host).json() == []


def test_a_family_without_a_host_gets_one_when_the_screen_connects(client):
    """Households made before hosts existed, with a desktop that reuses its saved session: the
    member's pieces still have to go into the screen's house."""
    from packages.schema.inventory import InventoryItem

    family = hh.households().create("Old")  # no host
    session = client.app.state.hub.create_session("screen", PROPERTY, household_id=family.household_id)  # not via POST /sessions
    hh.households().set_active_session(family.household_id, session.session_id)
    joined = client.post("/api/households/join", json={"invite_token": family.invite_token, "name": "Maya"}).json()
    maya = joined["member"]["member_id"]
    lamp = client.get("/api/inventory", headers={"X-User-Id": "screen"}).json()[0] | {"item_id": "00000000-0000-4000-8000-00000000b2b2", "user_id": maya, "owner_name": None}
    store.furniture_store().put_item(InventoryItem.model_validate(lamp))
    host = {"X-User-Id": "screen", "X-Person-Id": maya}
    assert _snap(client, host, lamp["item_id"]).status_code == 404

    with client.websocket_connect(f"/api/ws/sessions/{session.session_id}?client=desktop&token={session.client_token}"):
        pass
    assert _snap(client, host, lamp["item_id"]).status_code == 200
