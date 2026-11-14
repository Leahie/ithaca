"""Skybox re-cut and sweep rotations (offline)."""

import numpy as np
from fastapi.testclient import TestClient

from services.api.twin import panos
from services.api.twin.app import app
from services.api.twin.service import get_twin

PROPERTY_ID = "onXQLXhfyQv"


def _gl_face(d: np.ndarray) -> list[str]:
    """Which face the OpenGL cube lookup picks for each direction (spec table 8.19)."""
    ax = np.abs(d)
    dom = np.argmax(ax, axis=1)
    return [("px", "nx", "py", "ny", "pz", "nz")[2 * a + (v[a] < 0)] for a, v in zip(dom, d)]


def test_face_directions_land_on_their_own_face():
    for face in panos.FACES:
        d = panos.gl_face_directions(face, 8).reshape(-1, 3)
        assert set(_gl_face(d)) == {face}


def test_recut_is_a_pixel_permutation_of_matterport_faces():
    # a face whose pixels encode their own (face, row, col) survives the re-cut and back
    n = 16
    faces = []
    for f in range(6):
        r, c = np.meshgrid(np.arange(n), np.arange(n), indexing="ij")
        faces.append(np.stack([np.full_like(r, f), r, c], axis=-1).astype(np.uint8))
    seen = set()
    for face in panos.FACES:
        pixels = panos.sample_matterport(faces, panos.gl_face_directions(face, n))
        seen |= {tuple(p) for p in pixels.reshape(-1, 3)}
    assert len(seen) == 6 * n * n


def test_every_sweep_has_a_unit_rotation():
    twin = get_twin(PROPERTY_ID)
    q = np.array([[s.rotation.x, s.rotation.y, s.rotation.z, s.rotation.w] for s in twin.sweeps])
    assert np.allclose(np.linalg.norm(q, axis=1), 1, atol=1e-4)


def test_skybox_unknown_sweep_is_404():
    r = TestClient(app).get(f"/api/properties/{PROPERTY_ID}/sweeps/nope/skybox/px")
    assert r.status_code == 404


def test_skybox_bad_face_is_422():
    sweep = get_twin(PROPERTY_ID).sweeps[0].sweep_id
    r = TestClient(app).get(f"/api/properties/{PROPERTY_ID}/sweeps/{sweep}/skybox/up")
    assert r.status_code == 422
