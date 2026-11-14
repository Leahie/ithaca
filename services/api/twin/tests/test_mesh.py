import json
import struct

import pytest

from services.api.integrations.config import fixtures_dir
from services.api.twin.mesh import MAX_GLB_BYTES, interior_glb_path
from services.api.twin.service import get_twin

PROPERTY_ID = "onXQLXhfyQv"
# Extensions the Sun Lab's three.js GLTFLoader handles (meshopt needs MeshoptDecoder).
SUPPORTED_EXTENSIONS = {"KHR_mesh_quantization", "EXT_meshopt_compression", "KHR_texture_transform"}


@pytest.fixture(scope="module")
def glb() -> bytes:
    path = interior_glb_path(PROPERTY_ID)
    data = path.read_bytes()
    if data.startswith(b"version https://git-lfs"):
        pytest.skip("interior.glb is an LFS pointer; run `git lfs pull`")
    return data


def test_glb_header_and_budget(glb):
    magic, version, length = struct.unpack("<4sII", glb[:12])
    assert (magic, version, length) == (b"glTF", 2, len(glb))
    assert len(glb) <= MAX_GLB_BYTES


def test_glb_uses_only_supported_extensions(glb):
    chunk_length, chunk_type = struct.unpack("<I4s", glb[12:20])
    assert chunk_type == b"JSON"
    gltf = json.loads(glb[20 : 20 + chunk_length])
    assert set(gltf.get("extensionsRequired", [])) <= SUPPORTED_EXTENSIONS
    assert gltf["asset"]["version"] == "2.0"
    assert gltf.get("textures")


def test_twin_points_at_the_glb():
    twin = get_twin(PROPERTY_ID)
    assert (fixtures_dir() / twin.assets.interior_glb).exists()
