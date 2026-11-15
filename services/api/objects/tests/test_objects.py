"""Photo -> furniture: sizing rules (B6), the whole pipeline through the routes, and moving a piece."""

from __future__ import annotations

import io
import time

import pytest
import trimesh
from fastapi.testclient import TestClient
from PIL import Image

from services.api.agent import provider
from services.api.furniture import store
from services.api.objects import pipeline
from services.api.objects.normalize import NormalizeError, normalize_glb
from services.api.objects.providers import stub
from services.api.objects.providers.base import ImageReading, Piece

PROPERTY, LIVING_ROOM = "onXQLXhfyQv", "tt6gm2a0463x9wmnx7rn6gugc"


def raw_glb(extents, offset=(5.0, 3.0, -2.0)) -> bytes:
    """A box at an arbitrary scale and position, like a model fresh from image-to-3D."""
    box = trimesh.creation.box(extents=extents)
    box.apply_translation(offset)
    return trimesh.Scene(box).export(file_type="glb")


def bounds(glb: bytes):
    return trimesh.load(io.BytesIO(glb), file_type="glb", force="scene").bounds


def test_normalize_sizes_centers_and_turns_the_model_and_rejects_a_wrong_shape():
    # stated 2.10 wide x 0.90 deep x 0.80 high; the raw model is tiny, off-center, and turned a quarter
    glb, dims = normalize_glb(raw_glb((0.045, 0.040, 0.105)), "sofa", (2.10, 0.90, 0.80))
    lo, hi = bounds(glb)
    assert dims == (2.1, 0.9, 0.8)
    assert all(abs(a - b) <= 0.02 for a, b in zip(hi - lo, (2.10, 0.80, 0.90)))  # B6, glTF axes (x, y, z) = (w, h, d)
    assert abs(lo[1]) < 1e-3 and abs(lo[0] + hi[0]) < 1e-3 and abs(lo[2] + hi[2]) < 1e-3  # I-G15: origin bottom center

    _, guessed = normalize_glb(raw_glb((3.0, 1.2, 1.3)), "sofa", (None, None, None))  # no stated size: typical width, same proportions
    assert abs(guessed[0] - 2.10) < 0.01 and abs(guessed[1] / guessed[0] - 1.3 / 3.0) < 0.01

    with pytest.raises(NormalizeError):  # a tall thin model can't be a 2.1 m wide, 0.8 m high sofa
        normalize_glb(raw_glb((0.3, 2.0, 0.3)), "sofa", (2.10, 0.90, 0.80))


class TwoPieceDescriber:
    """Stands in for the vision LLM: a table with printed dimensions, and a piece the segmenter won't find."""

    def read(self, image, mime_type, filename):
        return ImageReading(
            kind="product_page",
            pieces=[
                Piece(label="Walnut desk", item_class="desk", segment_prompt="desk", width_m=1.2, depth_m=0.6, height_m=0.75, price=249, box=[0, 0, 0.5, 1]),
                Piece(label="Ghost chair", item_class="chair", segment_prompt="missing", box=[0.5, 0, 1, 1]),
            ],
        )


class PickySegmenter(stub.WholeImageSegmenter):
    def segment(self, image, mime_type, prompt, near=None, at=None):
        return None if prompt == "missing" else super().segment(image, mime_type, prompt)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_PROVIDER", "scripted")
    monkeypatch.setenv("AGENT_STORAGE", "memory")
    monkeypatch.setenv("FILES_DIR", str(tmp_path))
    provider._storage.cache_clear()
    monkeypatch.setattr(store, "_default", store.FurnitureStore(":memory:"))
    from services.api.main import app

    with TestClient(app) as c:
        app.state.object_providers = pipeline.Providers(TwoPieceDescriber(), PickySegmenter(), stub.BoxTo3D())
        yield c
    provider._storage.cache_clear()


def test_upload_to_inventory_through_the_routes(client):
    alice = {"X-User-Id": "alice"}
    png = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 80, 40)).save(png, "PNG")
    up = client.post("/api/uploads", headers=alice, files={"file": ("desk.png", png.getvalue(), "image/png")}).json()
    job_id = client.post("/api/objects/ingest", headers=alice, json={"upload_id": up["upload_id"]}).json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            break
        time.sleep(0.05)
    assert job["status"] == "done"

    items = client.get("/api/inventory", headers=alice).json()
    desk = next(i for i in items if i["label"] == "Walnut desk")
    assert desk["dims_m"] == {"w": 1.2, "d": 0.6, "h": 0.75} and desk["price"] == 249 and desk["source_image_url"] == up["url"]
    assert not any(i["label"] == "Ghost chair" for i in items)  # the piece that failed was not saved (B6)
    lo, hi = bounds(client.get(desk["glb_url"]).content)  # the saved GLB really has those dimensions
    assert all(abs(a - b) <= 0.02 for a, b in zip(hi - lo, (1.2, 0.75, 0.6)))
    assert "Walnut desk" in client.get("/api/memory/inventory.md", headers=alice).text  # the agent can see it
    assert client.get("/api/files/../../.env").status_code == 404  # no escaping the files folder


def _run(client, headers, body):
    job_id = client.post("/api/objects/ingest", headers=headers, json=body).json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    return job


def test_a_tapped_photo_becomes_only_the_piece_that_was_tapped(client):
    bob = {"X-User-Id": "bob"}
    png = io.BytesIO()
    Image.new("RGB", (64, 48), (120, 80, 40)).save(png, "PNG")
    up = client.post("/api/uploads", headers=bob, files={"file": ("room.png", png.getvalue(), "image/png")}).json()
    before = {i["item_id"] for i in client.get("/api/inventory", headers=bob).json()}

    desk = _run(client, bob, {"upload_id": up["upload_id"], "at": {"x": 0.25, "y": 0.5}})
    assert desk["status"] == "done"
    added = [i for i in client.get("/api/inventory", headers=bob).json() if i["item_id"] not in before]
    assert [i["label"] for i in added] == ["Walnut desk"] and added[0]["item_id"] == desk["result_ref"]  # the chair was not tapped

    ghost = _run(client, bob, {"upload_id": up["upload_id"], "at": {"x": 0.75, "y": 0.5}})
    assert ghost["status"] == "failed" and "Ghost chair" in ghost["result_ref"]  # the reason, for the app to show


def test_pick_and_covers_follow_the_tapped_spot():
    from services.api.objects.providers.base import covers, pick

    found = [(0.9, [0.0, 0.0, 0.4, 1.0]), (0.6, [0.5, 0.0, 1.0, 1.0]), (0.8, [0.45, 0.2, 0.9, 0.8])]
    assert pick(found, None, at=(0.7, 0.5)) == 2  # the best detection around the spot, not the best overall
    assert pick(found, None, at=(0.7, 0.95)) == 1
    assert pick(found, None, at=(0.42, 0.1)) is None
    mask = Image.new("L", (100, 50), 0)
    mask.paste(255, (60, 0, 100, 50))
    assert covers(mask, (0.8, 0.5)) and not covers(mask, (0.2, 0.5)) and covers(mask, (1.0, 1.0))


def test_placing_moving_and_options_in_the_house(client):
    from services.api.sim import layout_solver as ls
    from services.api.twin.service import get_twin

    alice = {"X-User-Id": "alice"}
    furniture = store.furniture_store()
    twin = get_twin(PROPERTY)
    room = next(r for r in twin.rooms if r.room_id == LIVING_ROOM)
    owned = furniture.list_items("alice")[:2]
    items = [ls.Item(str(i.item_id), i.label, i.item_class, i.dims_m.w, i.dims_m.d, i.dims_m.h) for i in owned]
    poses = ls.solve(room, twin.openings, items, [], n=1).variants[0].poses
    base = f"/api/properties/{PROPERTY}/furniture"
    for k, p in poses.items():
        assert client.put(f"{base}/{k}", headers=alice, json={"x": p.x, "z": p.z, "yaw_deg": p.yaw, "floor_id": room.floor_id}).status_code == 200
    sofa, table = (str(i.item_id) for i in owned)

    # the user's hand wins: any spot and any angle inside the room is saved, with warnings, not refusals
    onto_sofa = client.put(f"{base}/{table}", headers=alice, json={"x": poses[sofa].x, "z": poses[sofa].z, "yaw_deg": 0})
    assert onto_sofa.status_code == 200 and any("overlap" in w for w in onto_sofa.json()["warnings"])
    assert client.put(f"{base}/{table}", headers=alice, json={"x": 500, "z": 500, "yaw_deg": 0}).status_code == 409  # except leaving every room

    turned = client.put(f"{base}/{table}", headers=alice, json={"x": poses[table].x, "z": poses[table].z, "yaw_deg": 37.5})
    assert turned.status_code == 200
    saved = next(p for p in client.get(base, headers=alice).json() if p["item_id"] == table)
    assert saved["yaw_deg"] == 37.5 and saved["room_id"] == LIVING_ROOM  # any angle, not only along the walls

    # a pin on the floor plan: the third piece snaps to a valid spot near the table, in the room under the pin
    chair = str(furniture.list_items("alice")[2].item_id)
    dropped = client.put(f"{base}/{chair}", headers=alice, json={"x": poses[table].x, "z": poses[table].z, "floor_id": room.floor_id, "snap": True})
    assert dropped.status_code == 200 and dropped.json()["room_id"] == LIVING_ROOM
    assert len(client.get(f"{base}?room_id={LIVING_ROOM}", headers=alice).json()) == 3
    # "where could it go?": up to three proposed layouts, the room's other pieces untouched, the piece somewhere new in each
    options = client.post("/api/layouts/options", headers=alice, json={"property_id": PROPERTY, "room_id": LIVING_ROOM, "item_id": chair}).json()
    spots = {(p["position"]["x"], p["position"]["z"]) for o in options for p in o["placements"] if p["item_id"] == chair}
    assert 1 <= len(options) <= 3 and len(spots) == len(options) and all(len(o["placements"]) == 3 and o["status"] == "proposed" for o in options)


def test_catalog_piece_is_added_at_its_own_size(client, monkeypatch, tmp_path):
    from services.api.objects.providers import catalog

    cid, _cls, label, w, d, h = catalog.BY_ID["B07B4SBZFN"]
    monkeypatch.setattr(catalog, "fetch", lambda asset_id: raw_glb((w * 3, h * 3, d * 3)))  # no download in tests
    alice = {"X-User-Id": "alice"}

    listed = client.get("/api/catalog?item_class=bed").json()
    assert cid in [p["catalog_id"] for p in listed] and all(p["item_class"] == "bed" for p in listed)

    item = client.post(f"/api/catalog/{cid}/add", headers=alice).json()
    assert item["label"] == label and item["dims_m"] == {"w": w, "d": d, "h": h}  # the model's own size
    assert label in client.get("/api/memory/inventory.md", headers=alice).text  # the agent can now place it
    resized = client.post(f"/api/catalog/{cid}/add", headers=alice, json={"width_m": 1.8, "depth_m": 2.1, "height_m": 1.2}).json()
    assert resized["dims_m"] == {"w": 1.8, "d": 2.1, "h": 1.2}
    assert client.post("/api/catalog/nope/add", headers=alice).status_code == 404
