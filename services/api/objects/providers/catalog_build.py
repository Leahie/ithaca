"""Tools for growing the furniture catalog (providers/catalog.py). Not used by the running API.

    python -m services.api.objects.providers.catalog_build index
        Download the Amazon Berkeley Objects listing metadata (84 MB, once) into
        data/catalog/abo_meta/ and write models.jsonl: one line per product that has a 3D model.
    python -m services.api.objects.providers.catalog_build find brand="Stone & Beam" name="Parson" [type=TABLE,BED] [not=rug]
        Products in that index, colour variants collapsed, with the model's own size.
    python -m services.api.objects.providers.catalog_build check <id> [<id> ...]
        Fetch each model, measure it and compare it with its listing: real size in metres, standing
        on y = 0, centred, not too heavy. Prints a catalog row for the ones that pass.
    python -m services.api.objects.providers.catalog_build rows pieces.tsv
        Print catalog rows (to paste into catalog._ROWS) for a tab-separated list of id, class, label.
    python -m services.api.objects.providers.catalog_build render [--all | <id> ...] [--sheet name]
        Draw each piece's 360 px picture into fixtures/blobs/catalog/ from its model: the same
        three-quarter view on white for every piece. Needs moderngl and an EGL driver (Mesa has one):
        PYTHONPATH=. uv run --no-project --with-requirements services/api/requirements-b.txt \
            --with-requirements services/api/twin/requirements.txt --with moderngl \
            python -m services.api.objects.providers.catalog_build render --all
        --sheet also writes data/catalog/_sheets/<name>.jpg, the pictures side by side, for review.

Metadata and models are downloaded data: they stay under data/catalog/ (not in git) and are only
ever parsed, never run.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import re
import sys
from collections import OrderedDict
from pathlib import Path

import httpx
import numpy as np

from services.api.integrations.config import REPO_ROOT, fixtures_dir
from services.api.objects.providers import catalog

META = REPO_ROOT / "data" / "catalog" / "abo_meta"
INDEX = META / "models.jsonl"
SHEETS = REPO_ROOT / "data" / "catalog" / "_sheets"
PICTURES = fixtures_dir() / "blobs" / "catalog"
INCH = 0.0254
MAX_FACES = 400_000  # a piece heavier than this slows a browser showing a whole house
MAX_MB = 6.0  # the cached file, after its textures are shrunk


# --- the ABO index --------------------------------------------------------------------------------


def _english(values: list[dict] | None) -> str | None:
    """The en_US (else any English, else the first) value of a multilingual field."""
    best = None
    for v in values or []:
        tag = v.get("language_tag", "")
        if tag == "en_US":
            return v.get("value")
        if tag.startswith("en") and best is None:
            best = v.get("value")
    return best if best is not None else (values[0].get("value") if values else None)


def build_index() -> None:
    META.mkdir(parents=True, exist_ok=True)
    names = ["3dmodels/metadata/3dmodels.csv.gz", "images/metadata/images.csv.gz"] + [f"listings/metadata/listings_{h}.json.gz" for h in "0123456789abcdef"]
    with httpx.Client(timeout=300, headers=catalog.HEADERS, follow_redirects=True) as http:
        for name in names:
            target = META / Path(name).name
            if not target.exists():
                print("downloading", name)
                target.write_bytes(http.get(f"{catalog.BUCKET}/{name}").raise_for_status().content)
    with gzip.open(META / "3dmodels.csv.gz", "rt", newline="") as f:
        models = {row["3dmodel_id"]: row for row in csv.DictReader(f)}
    with gzip.open(META / "images.csv.gz", "rt", newline="") as f:
        photos = {row["image_id"]: row["path"] for row in csv.DictReader(f)}
    rows: dict[str, dict] = {}
    for h in "0123456789abcdef":
        with gzip.open(META / f"listings_{h}.json.gz", "rt") as f:
            for line in f:
                d = json.loads(line)
                mid = d.get("3dmodel_id")
                if mid not in models:
                    continue
                dims = d.get("item_dimensions") or {}
                m = models[mid]
                rec = {
                    "id": mid, "domain": d.get("domain_name"), "brand": _english(d.get("brand")), "name": _english(d.get("item_name")),
                    "type": (d.get("product_type") or [{}])[0].get("value"), "color": _english(d.get("color")),
                    "inches": {k: ((dims.get(k) or {}).get("normalized_value") or {}).get("value") for k in ("width", "length", "height")},
                    "photo": photos.get(d.get("main_image_id") or "", ""), "faces": int(m["faces"]),
                    "extent": [float(m["extent_x"]), float(m["extent_y"]), float(m["extent_z"])],
                }
                old = rows.get(mid)
                if old is None or (rec["domain"] == "amazon.com" and old["domain"] != "amazon.com"):  # the US listing, in English
                    rows[mid] = rec
    with INDEX.open("w") as f:
        for r in rows.values():
            f.write(json.dumps(r) + "\n")
    print(f"{len(rows)} products with a 3D model -> {INDEX}")


def index() -> dict[str, dict]:
    if not INDEX.exists():
        build_index()
    return {r["id"]: r for r in map(json.loads, INDEX.open())}


def _title(row: dict) -> str:
    return re.sub(r"^Amazon Brand\s*[–-]\s*", "", row["name"] or "")


def find(args: dict[str, str]) -> None:
    types = set(args["type"].split(",")) if "type" in args else None
    name = re.compile(args["name"], re.I) if "name" in args else None
    no = re.compile(args["not"], re.I) if "not" in args else None
    groups: OrderedDict[tuple[str, str], list[dict]] = OrderedDict()
    for r in sorted(index().values(), key=_title):
        n = _title(r)
        if ("brand" in args and (r["brand"] or "") != args["brand"]) or (types and r["type"] not in types):
            continue
        if (name and not name.search(n)) or (no and no.search(n)):
            continue
        base = n.rsplit(",", 1)[0] if "," in n else n
        size = "x".join(f"{v:.2f}" for v in r["extent"])
        groups.setdefault((base, size), []).append(r)
    cols = int(args.get("cols", 4))
    for (base, size), rs in groups.items():
        variants = ", ".join(f"{_title(r).rsplit(',', 1)[-1].strip()[:18]}={r['id']}" for r in rs[:cols])
        print(f"{base[: int(args.get('w', 90))]} | {size} | {rs[0]['faces'] // 1000}k | {variants}{f' +{len(rs) - cols}' if len(rs) > cols else ''}")
    print(f"{len(groups)} products", file=sys.stderr)


# --- measuring a model ----------------------------------------------------------------------------


def fetch_any(piece_id: str) -> bytes:
    """A model by id, in the catalog or not yet: catalog.fetch works for any ABO model id."""
    return catalog.fetch(piece_id)


def prefetch(ids: list[str]) -> None:
    """Download several models side by side (each is 5 to 50 MB before its textures are shrunk)."""
    from concurrent.futures import ThreadPoolExecutor

    def one(piece_id: str) -> None:
        try:
            fetch_any(piece_id)
        except Exception as e:
            print(f"{piece_id} could not be fetched: {e}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(one, ids))


def measure(piece_id: str) -> dict:
    import trimesh

    glb = fetch_any(piece_id)
    scene = trimesh.load(io.BytesIO(glb), file_type="glb", force="scene")
    lo, hi = scene.bounds
    w, h, d = (float(v) for v in hi - lo)
    faces = sum(len(g.faces) for g in scene.geometry.values() if hasattr(g, "faces"))
    return {
        "w": w, "d": d, "h": h, "floor": float(lo[1]), "off_x": float(lo[0] + hi[0]) / 2, "off_z": float(lo[2] + hi[2]) / 2,
        "faces": faces, "mb": len(glb) / 1e6,
    }


def check(ids: list[str]) -> None:
    """One line per model: its size, the listing's size, and what is wrong with it, if anything."""
    rows = index()
    prefetch(ids)
    for piece_id in ids:
        row = rows.get(piece_id, {})
        try:
            m = measure(piece_id)
        except Exception as e:
            print(f"{piece_id} FAILED to load: {e}")
            continue
        listed = sorted(v * INCH for v in (row.get("inches") or {}).values() if v)
        got = sorted([m["w"], m["d"], m["h"]])
        problems, notes = [], []
        # Not faults: adding a piece centres it and stands it on the floor (objects/normalize.py).
        if abs(m["floor"]) > 0.01:
            notes.append(f"stands at y={m['floor']:.3f}")
        if max(abs(m["off_x"]), abs(m["off_z"])) > 0.05:
            notes.append(f"off centre by {m['off_x']:.2f}, {m['off_z']:.2f}")
        if m["faces"] > MAX_FACES:
            problems.append(f"{m['faces'] // 1000}k faces")
        if m["mb"] > MAX_MB:
            problems.append(f"{m['mb']:.1f} MB")
        # The listing's three sizes against the model's, largest to smallest: within 12 % or 6 cm.
        # A listing often gives the size of the box it ships in: then compare with the width in the
        # product's name, and look at the picture.
        if len(listed) == 3 and any(abs(a - b) > max(0.12 * b, 0.06) for a, b in zip(got, listed)):
            problems.append("size differs from the listing " + "x".join(f"{v:.2f}" for v in listed))
        elif len(listed) < 3:
            notes.append("listing gives no full size")
        print(
            f"{piece_id} {m['w']:.2f} x {m['d']:.2f} x {m['h']:.2f} m (w d h) · {m['faces'] // 1000}k faces · {m['mb']:.1f} MB · "
            f"{'OK' if not problems else '; '.join(problems)}{' (' + '; '.join(notes) + ')' if notes else ''} · {_title(row)[:70]}"
        )


def rows(spec: Path) -> None:
    """Catalog rows for the pieces in a tab-separated file of `id, class, label`: each with the
    size of its cached model (after any catalog._SCALE), its product photo and product name."""
    listed = index()
    for line in spec.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        piece_id, item_class, label = (part.strip() for part in line.split("\t"))
        m = measure(piece_id)
        row = listed[piece_id]
        name = re.sub(r"\s+", " ", _title(row).replace("&nbsp;", " ").replace("&amp;", "&"))[:110]
        print(f"    ({piece_id!r}, {item_class!r}, {label!r}, {m['w']:.2f}, {m['d']:.2f}, {m['h']:.2f}, {row['photo']!r}, {name!r}),")


# --- pictures -------------------------------------------------------------------------------------

VIEW = {"azimuth_deg": 35.0, "elevation_deg": 20.0, "fov_deg": 26.0, "fill": 0.94}  # the catalog's one view
FLAT_ELEVATION_DEG = 45.0  # a rug is looked at from higher up, or it is a sliver
PX, SUPER = 360, 4

VERT = """
#version 330
uniform mat4 mvp;
in vec3 in_pos; in vec3 in_normal; in vec2 in_uv;
out vec3 v_normal; out vec2 v_uv;
void main() { gl_Position = mvp * vec4(in_pos, 1.0); v_normal = in_normal; v_uv = in_uv; }
"""
FRAG = """
#version 330
uniform sampler2D tex; uniform bool has_tex; uniform vec4 base; uniform vec3 key; uniform vec3 fill; uniform vec3 eye_dir; uniform bool cutout;
in vec3 v_normal; in vec2 v_uv;
out vec4 color;
void main() {
    vec4 c = base;
    if (has_tex) { vec4 t = texture(tex, v_uv); c *= vec4(pow(t.rgb, vec3(2.2)), t.a); }
    if (cutout && c.a < 0.4) discard;
    vec3 n = normalize(v_normal);
    if (dot(n, eye_dir) < 0.0) n = -n;  // both sides of a sheet are lit alike
    float light = 0.84 + 0.50 * max(dot(n, key), 0.0) + 0.22 * max(dot(n, fill), 0.0) + 0.12 * n.y;
    vec3 lit = c.rgb * light;
    // A soft shoulder, so a white mattress or lampshade keeps its shape instead of going flat.
    lit = mix(lit, 0.6 + 0.4 * (1.0 - exp(-(lit - 0.6) / 0.4)), step(0.6, lit));
    color = vec4(pow(lit, vec3(1.0 / 2.2)), 1.0);
}
"""


def _camera(lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(view-projection matrix, the direction toward the eye) for the catalog's view of a box."""
    flat = (hi[1] - lo[1]) < 0.12 * max(hi[0] - lo[0], hi[2] - lo[2])
    az, el = math.radians(VIEW["azimuth_deg"]), math.radians(FLAT_ELEVATION_DEG if flat else VIEW["elevation_deg"])
    toward = np.array([math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el)])
    centre = (lo + hi) / 2
    forward = -toward
    right = np.cross(forward, [0, 1, 0])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward)
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]) - centre
    # Far enough that every corner is inside the picture with a margin.
    tan = math.tan(math.radians(VIEW["fov_deg"]) / 2) * VIEW["fill"]
    dist = max(max(abs(c @ right), abs(c @ up)) / tan + c @ toward for c in corners)
    eye = centre + toward * dist
    view = np.eye(4)
    view[0, :3], view[1, :3], view[2, :3] = right, up, -forward
    view[:3, 3] = -view[:3, :3] @ eye
    radius = float(np.linalg.norm(hi - lo))
    near, far = max(dist - radius, 0.01), dist + radius
    f = 1 / math.tan(math.radians(VIEW["fov_deg"]) / 2)
    proj = np.array([[f, 0, 0, 0], [0, f, 0, 0], [0, 0, (far + near) / (near - far), 2 * far * near / (near - far)], [0, 0, -1, 0]])
    return proj @ view, toward


def picture(glb: bytes, ctx=None):
    """A 360 px picture of a model: three-quarter view from its front left, on white."""
    import moderngl
    import trimesh
    from PIL import Image

    own = ctx is None
    ctx = ctx or moderngl.create_standalone_context(backend="egl")
    try:
        scene = trimesh.load(io.BytesIO(glb), file_type="glb", force="scene")
        meshes = [m for m in scene.dump(concatenate=False) if hasattr(m, "faces") and len(m.faces)]
        lo, hi = scene.bounds
        mvp, toward = _camera(np.array(lo), np.array(hi))
        size = PX * SUPER
        target = ctx.framebuffer(color_attachments=[ctx.renderbuffer((size, size), samples=4)], depth_attachment=ctx.depth_renderbuffer((size, size), samples=4))
        flat = ctx.framebuffer(color_attachments=[ctx.renderbuffer((size, size))])
        target.use()
        ctx.clear(1.0, 1.0, 1.0, 1.0)
        ctx.enable(moderngl.DEPTH_TEST)
        ctx.disable(moderngl.CULL_FACE)
        prog = ctx.program(vertex_shader=VERT, fragment_shader=FRAG)
        prog["mvp"].write(mvp.T.astype("f4").tobytes())
        key = np.array([-0.35, 0.75, 0.55])
        fill = np.array([0.8, 0.25, 0.3])
        prog["key"].value = tuple(key / np.linalg.norm(key))
        prog["fill"].value = tuple(fill / np.linalg.norm(fill))
        prog["eye_dir"].value = tuple(toward)
        for mesh in meshes:
            material = getattr(mesh.visual, "material", None)
            uv = getattr(mesh.visual, "uv", None)
            image = getattr(material, "baseColorTexture", None) if material is not None else None
            factor = getattr(material, "baseColorFactor", None) if material is not None else None
            base = np.array(factor if factor is not None else [255, 255, 255, 255], dtype=float)
            base = base / 255.0 if base.max() > 1.0 else base
            base[:3] = base[:3] ** 2.2
            prog["base"].value = tuple(base[:4]) if len(base) == 4 else (*base[:3], 1.0)
            prog["cutout"].value = getattr(material, "alphaMode", None) in ("MASK", "BLEND")
            texture = None
            if image is not None and uv is not None:
                rgba = image.convert("RGBA").transpose(Image.FLIP_TOP_BOTTOM)
                texture = ctx.texture(rgba.size, 4, rgba.tobytes())
                texture.build_mipmaps()
                texture.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
                texture.use(0)
            prog["has_tex"].value = texture is not None
            data = np.hstack([mesh.vertices, mesh.vertex_normals, uv if uv is not None and texture is not None else np.zeros((len(mesh.vertices), 2))]).astype("f4")
            vbo = ctx.buffer(data.tobytes())
            ibo = ctx.buffer(np.asarray(mesh.faces, dtype="i4").tobytes())
            vao = ctx.vertex_array(prog, [(vbo, "3f 3f 2f", "in_pos", "in_normal", "in_uv")], ibo)
            vao.render()
            for obj in (vao, vbo, ibo, texture):
                if obj is not None:
                    obj.release()
        ctx.copy_framebuffer(flat, target)
        shot = Image.frombytes("RGB", (size, size), flat.read(components=3)).transpose(Image.FLIP_TOP_BOTTOM)
        for obj in (prog, target, flat):
            obj.release()
        return shot.resize((PX, PX), Image.LANCZOS)
    finally:
        if own:
            ctx.release()


def render(ids: list[str], sheet: str | None = None) -> None:
    import moderngl
    from PIL import Image, ImageDraw

    prefetch(ids)
    ctx = moderngl.create_standalone_context(backend="egl")
    PICTURES.mkdir(parents=True, exist_ok=True)
    shots = []
    for piece_id in ids:
        try:
            shot = picture(fetch_any(piece_id), ctx)
        except Exception as e:
            print(f"{piece_id} FAILED: {e}")
            continue
        if sheet is None:  # a sheet is for looking at candidates: it changes no picture
            shot.save(PICTURES / f"{piece_id}.jpg", "JPEG", quality=88)
        shots.append((piece_id, shot))
    if sheet and shots:
        cols = min(6, len(shots))
        rows = math.ceil(len(shots) / cols)
        cell = 240
        page = Image.new("RGB", (cols * cell, rows * (cell + 14)), "white")
        draw = ImageDraw.Draw(page)
        for n, (piece_id, shot) in enumerate(shots):
            x, y = (n % cols) * cell, (n // cols) * (cell + 14)
            page.paste(shot.resize((cell, cell), Image.LANCZOS), (x, y))
            draw.text((x + 4, y + cell), piece_id, fill="black")
        SHEETS.mkdir(parents=True, exist_ok=True)
        page.save(SHEETS / f"{sheet}.jpg", "JPEG", quality=85)
        print(SHEETS / f"{sheet}.jpg")


def main(argv: list[str]) -> None:
    command, rest = (argv[0], argv[1:]) if argv else ("", [])
    if command == "index":
        build_index()
    elif command == "find":
        find(dict(a.split("=", 1) for a in rest))
    elif command == "check":
        check(rest)
    elif command == "rows":
        rows(Path(rest[0]))
    elif command == "render":
        sheet = rest[rest.index("--sheet") + 1] if "--sheet" in rest else None
        ids = [a for a in rest if not a.startswith("--") and a != sheet]
        render([row[0] for row in catalog.PIECES] if "--all" in rest else ids, sheet)
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
