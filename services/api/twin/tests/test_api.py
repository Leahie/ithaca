import shutil

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from services.api.integrations.config import fixtures_dir
from services.api.twin import service
from services.api.twin.app import app

PID = "onXQLXhfyQv"
BASE = f"/api/properties/{PID}"


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def sunny_room():
    """The room with the most exterior windows, and a point inside it."""
    twin = service.get_twin(PID)
    counts = {}
    for o in twin.openings:
        if o.kind != "door" and o.exterior is not False:
            counts[o.room_id] = counts.get(o.room_id, 0) + 1
    room = next(r for r in twin.rooms if r.room_id == max(counts, key=counts.get))
    p = Polygon(room.polygon).representative_point()
    return room.room_id, p.x, p.y


def test_twin_resolves_asset_urls(client):
    r = client.get(f"{BASE}/twin")
    assert r.status_code == 200
    twin = r.json()
    assert twin["property_id"] == PID
    assert twin["assets"]["interior_glb"] == f"http://testserver/api/assets/blobs/{PID}/interior.glb"


def test_unknown_property_is_404(client):
    assert client.get("/api/properties/nope/twin").status_code == 404
    assert client.get("/api/properties/nope/sun/at", params={"when": "2026-06-21T12:00"}).status_code == 404


def test_asset_is_served_and_paths_are_confined(client):
    path = fixtures_dir() / "blobs" / PID / "interior.glb"
    if not path.exists() or path.stat().st_size < 1024:
        pytest.skip("interior.glb not pulled (git lfs)")
    r = client.get(f"/api/assets/blobs/{PID}/interior.glb")
    assert r.status_code == 200
    assert r.headers["content-type"] == "model/gltf-binary"
    assert r.content[:4] == b"glTF"
    assert client.get(f"/api/assets/twin/{PID}.json").status_code == 404
    assert client.get("/api/assets/blobs/../../services/api/twin/app.py").status_code == 404


def test_sun_report(client, sunny_room):
    room_id, _, _ = sunny_room
    r = client.get(f"{BASE}/sun", params={"room_id": room_id, "start": "2026-06-20", "end": "2026-06-22"})
    assert r.status_code == 200
    body = r.json()
    assert [d["date"] for d in body["daily"]] == ["2026-06-20", "2026-06-21", "2026-06-22"]
    assert client.get(f"{BASE}/sun", params={"room_id": "nope", "start": "2026-06-20",
                                             "end": "2026-06-21"}).status_code == 404
    assert client.get(f"{BASE}/sun", params={"room_id": room_id, "start": "2026-01-01",
                                             "end": "2028-01-01"}).status_code == 422


def test_sun_at_day_and_spot(client, sunny_room):
    room_id, x, z = sunny_room
    r = client.get(f"{BASE}/sun/at", params={"when": "2026-06-21T15:00", "height_m": 1.1})
    assert r.status_code == 200
    assert r.json()["datetime"].startswith("2026-06-21T15:00:00-05:00")
    assert client.get(f"{BASE}/sun/at", params={"when": "half past three"}).status_code == 422
    assert client.get(f"{BASE}/sun/at", params={"when": "2026-06-21T15:00", "height_m": 9}).status_code == 422

    r = client.get(f"{BASE}/sun/day", params={"room_id": room_id, "date": "2026-06-21"})
    assert r.status_code == 200 and r.json()["room_id"] == room_id

    r = client.get(f"{BASE}/sun/spot", params={"room_id": room_id, "x": x, "z": z, "date": "2026-06-21"})
    assert r.status_code == 200 and r.json()["height_m"] == pytest.approx(1.1)
    far = client.get(f"{BASE}/sun/spot", params={"room_id": room_id, "x": x + 500, "z": z, "date": "2026-06-21"})
    assert far.status_code == 422


def test_daylight_at_one_room_without_grids(client, sunny_room):
    room_id, _, _ = sunny_room
    r = client.get(f"{BASE}/daylight/at", params={"when": "2026-06-21T12:00", "room_id": room_id, "grids": False})
    assert r.status_code == 200
    rooms = r.json()["rooms"]
    assert [rd["room_id"] for rd in rooms] == [room_id]
    assert rooms[0]["grid"] is None and rooms[0]["mean_lux"] > 50
    assert client.get(f"{BASE}/daylight/at", params={"when": "2026-06-21T12:00", "sky": "cloudy"}).status_code == 422
    assert client.get(f"{BASE}/daylight/at", params={"when": "2026-06-21T12:00", "room_id": "nope"}).status_code == 404


def test_daylight_spot(client, sunny_room):
    room_id, x, z = sunny_room
    r = client.get(f"{BASE}/daylight/spot", params={"room_id": room_id, "x": x, "z": z, "date": "2026-06-21"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["times"]) == len(body["lux"]) == len(body["direct_sun"])
    assert max(body["lux"]) > 50


def test_noise_not_built_says_so(client, sunny_room):
    room_id, _, _ = sunny_room
    r = client.get(f"{BASE}/noise", params={"room_id": room_id})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "not_configured"


def test_area(client, monkeypatch):
    monkeypatch.setenv("INTEGRATION_MODE", "replay")
    r = client.get(f"{BASE}/area")
    assert r.status_code == 200
    body = r.json()
    assert body["listing"]["price"] == 1_995_000 and body["comps"] and body["amenities"]
    r = client.get(f"{BASE}/area/search", params={"q": "coffee", "limit": 2})
    assert r.status_code == 200 and 0 < len(r.json()) <= 2
    assert client.get(f"{BASE}/area/comps").json() == body["comps"]


def test_sound_presets(client):
    r = client.get(f"{BASE}/sound/presets")
    assert r.status_code == 200
    body = r.json()
    assert {"tv", "baby", "recording"} <= {p["preset_id"] for p in body["presets"]}
    assert all(len(p["bands_db"]) == 8 for p in body["presets"])
    assert body["loudness_dba"]["talking"] == 60


def test_sound_field_every_room_and_listener(client, sunny_room):
    room_id, x, z = sunny_room
    params = {"room_id": room_id, "x": x, "z": z, "preset": "baby"}
    r = client.get(f"{BASE}/sound/field", params=params)
    assert r.status_code == 200
    body = r.json()
    assert len(body["rooms"]) == len(service.get_twin(PID).rooms)
    assert body["rooms"][0]["room_id"] == room_id  # loudest first
    assert body["source"]["level_dba"] == 80 and body["listener"] is None

    near = client.get(f"{BASE}/sound/field", params={**params, "listener_room_id": room_id,
                                                      "listener_x": x, "listener_z": z}).json()["listener"]
    assert near["distance_m"] is not None and near["laeq_dba"] > body["rooms"][0]["laeq_dba"]

    louder = client.get(f"{BASE}/sound/field", params={**params, "level_dba": 90}).json()
    assert louder["rooms"][0]["laeq_dba"] == pytest.approx(body["rooms"][0]["laeq_dba"] + 10, abs=0.15)

    assert client.get(f"{BASE}/sound/field", params={**params, "preset": "lawnmower"}).status_code == 404
    assert client.get(f"{BASE}/sound/field", params={**params, "x": x + 500}).status_code == 422
    assert client.get(f"{BASE}/sound/field", params={**params, "listener_x": 1.0}).status_code == 422


def test_put_openings_saves_annotation_and_bumps_version(client, tmp_path, monkeypatch):
    shutil.copy(fixtures_dir() / "twin" / f"{PID}.json", tmp_path / "twin.json")
    (tmp_path / "twin").mkdir()
    shutil.move(tmp_path / "twin.json", tmp_path / "twin" / f"{PID}.json")
    monkeypatch.setenv("FIXTURES_DIR", str(tmp_path))

    before = client.get(f"{BASE}/twin").json()
    openings = before["openings"]
    edited = dict(openings[0], glazing="triple", stc=38)
    r = client.put(f"{BASE}/openings", json=[edited, *openings[1:]])
    assert r.status_code == 200
    assert len(r.json()) == len(openings)

    after = client.get(f"{BASE}/twin").json()
    assert after["version"] == before["version"] + 1
    assert after["quality"]["openings"] == "annotated"
    saved = next(o for o in after["openings"] if o["opening_id"] == edited["opening_id"])
    assert saved["glazing"] == "triple" and saved["stc"] == 38
    assert (tmp_path / "twin" / "annotations" / f"{PID}.openings.json").exists()

    bad = dict(openings[0], room_id="nope")
    assert client.put(f"{BASE}/openings", json=[bad]).status_code == 422
    dup = [openings[0], openings[0]]
    assert client.put(f"{BASE}/openings", json=dup).status_code == 422
