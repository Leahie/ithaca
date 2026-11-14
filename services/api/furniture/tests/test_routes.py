"""Inventory, layout and memory routes against an in-memory store."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.api.agent import provider
from services.api.furniture import store

PROPERTY = "onXQLXhfyQv"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_PROVIDER", "scripted")
    monkeypatch.setenv("AGENT_STORAGE", "memory")
    provider._storage.cache_clear()
    monkeypatch.setattr(store, "_default", store.FurnitureStore(":memory:"))
    from services.api.main import app

    with TestClient(app) as c:
        yield c
    provider._storage.cache_clear()


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


def test_sample_layouts_resolve_and_choosing_one_sticks(client):
    demo = {"X-User-Id": "demo"}
    from services.api.agent.scripted import demo_layout_ids

    first, second = demo_layout_ids(PROPERTY)[:2]
    assert client.get(f"/api/layouts/{first}", headers=demo).json()["status"] in ("proposed", "chosen")
    assert client.post(f"/api/layouts/{first}/select", headers=demo).json()["status"] == "chosen"
    assert client.post(f"/api/layouts/{second}/select", headers=demo).json()["status"] == "chosen"
    by_id = {l["layout_id"]: l["status"] for l in client.get(f"/api/layouts?property_id={PROPERTY}", headers=demo).json()}
    assert by_id[first] == "proposed" and by_id[second] == "chosen"  # one chosen layout per room
    assert client.get("/api/layouts/nope", headers=demo).status_code == 404


def test_removing_a_piece_keeps_it_owned_and_deleting_it_clears_it_from_every_room(client):
    import uuid

    from packages.schema.layout import LayoutVariant, Placement
    from packages.schema.twin import Vec3

    alice = {"X-User-Id": "alice"}
    sofa, table = [i["item_id"] for i in client.get("/api/inventory", headers=alice).json()[:2]]
    layout = LayoutVariant(
        layout_id=uuid.uuid4(), user_id="alice", property_id=PROPERTY, room_id="tt6gm2a0463x9wmnx7rn6gugc", score=0, rationale="", status="proposed",
        placements=[Placement(item_id=i, position=Vec3(x=-9, y=0, z=-15 - n), yaw_deg=0) for n, i in enumerate((sofa, table))],
    )
    store.furniture_store().put_layout(layout)
    url = f"/api/layouts/{layout.layout_id}"

    removed = client.delete(f"{url}/placements/{table}", headers=alice)
    assert removed.status_code == 200 and [p["item_id"] for p in removed.json()["placements"]] == [sofa]
    assert table in [i["item_id"] for i in client.get("/api/inventory", headers=alice).json()]  # still owned
    assert client.delete(f"{url}/placements/{table}", headers=alice).status_code == 404  # not in the room any more

    assert client.delete(f"/api/inventory/{sofa}", headers=alice).status_code == 204
    assert client.get(url, headers=alice).json()["placements"] == []  # deleted pieces leave their rooms too
