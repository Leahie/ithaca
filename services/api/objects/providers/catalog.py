"""The furniture catalog: ready-made pieces that belong together.

The pieces are real products from the Amazon Berkeley Objects dataset (ABO,
https://amazon-berkeley-objects.s3.amazonaws.com/index.html), which publishes a 3D model and
photos for each under CC BY 4.0. They come from one mid-century modern line whose named
collections were designed to match (a bed with its nightstand and dresser, a dining table with its
sideboard), so a room furnished from one SET looks like a room, not a second-hand shop. Models are
at real size in metres, stand on y = 0 and face +Z. Credit is shown wherever the catalog is (SOURCE).

OBJECT_PROVIDER=catalog also uses these as stand-ins for a photographed piece: the default model
of its class, sized to the photo. A model is downloaded the first time it is needed and cached
under data/catalog/abo/, with its textures shrunk to 1024 px (the originals are up to 4096 px and
16 MB a piece, too heavy for a browser showing a whole house).
"""

from __future__ import annotations

import io
import json
import logging
import struct
from pathlib import Path

import httpx

from services.api.integrations.config import REPO_ROOT, env
from services.api.objects.providers.base import Model3D, Piece, Segment
from services.api.objects.providers.stub import BoxTo3D

log = logging.getLogger(__name__)

# (ABO model id, furniture class, label, width, depth, height in metres, model path, photo path, product name)
_ROWS: list[tuple[str, str, str, float, float, float, str, str, str]] = [
    ("B072FVHS9G", "sofa", "Sloane sofa, pebble grey", 2.06, 0.97, 0.87, "G/B072FVHS9G.glb", "39/39992106.jpg", "Amazon Brand – Rivet Sloane Mid-Century Modern Sofa with Tufted Back, 79.9W, Pebble"),
    ("B0719WQH8N", "loveseat", "Sloane loveseat, pebble grey", 1.65, 0.95, 0.92, "N/B0719WQH8N.glb", "02/02c92f39.jpg", "Amazon Brand – Rivet Sloane Mid-Century Modern Sofa with Tufted Back, 64.2W, Pebble"),
    ("B0714QGLSD", "armchair", "Sloane armchair, pebble grey", 0.85, 0.94, 0.97, "D/B0714QGLSD.glb", "7e/7eee102c.jpg", "Amazon Brand – Rivet Sloane Mid-Century Modern Armchair with Tapered Legs, 32.7W, Pebble"),
    ("B071W5VJF4", "ottoman", "Sloane ottoman, pebble grey", 0.82, 0.61, 0.44, "4/B071W5VJF4.glb", "9a/9ae7d2f0.jpg", "Amazon Brand – Rivet Sloane Mid-Century Angled Leg Modern Ottoman, 31.9W, Pebble"),
    ("B07P6JTVZV", "armchair", "Cove accent chair, caramel leather", 0.83, 0.87, 0.90, "V/B07P6JTVZV.glb", "f8/f86e6baa.jpg", "Amazon Brand – Rivet Cove Modern Tufted Accent Chair with Tapered Legs, Mid-Century, 32.7W, Caramel"),
    ("B072ZN4NDS", "coffee_table", "Two-drawer coffee table, grey and walnut", 1.20, 0.70, 0.40, "S/B072ZN4NDS.glb", "cf/cff3bdd5.jpg", "Amazon Brand – Rivet Mid-Century Lacquer Two-Drawer Coffee Table, Grey and Walnut"),
    ("B07GDSF3MR", "coffee_table", "Round coffee table, walnut", 0.80, 0.80, 0.36, "R/B07GDSF3MR.glb", "32/32cd9937.jpg", "Amazon Brand - Rivet Round Coffee Table with Solid Wood Legs, 80 x 80 x 36cm, MDF with Walnut Veneer/Solid Bee"),
    ("B07BVHKPFS", "tv_stand", "Bowlyn media stand, walnut", 1.63, 0.48, 0.61, "S/B07BVHKPFS.glb", "2f/2fc198b8.jpg", "Amazon Brand – Rivet Bowlyn Mid-Century Modern Wood TV Media Table Stand, 64, Walnut"),
    ("B07GDZ8HN6", "side_table", "One-drawer side table, walnut", 0.43, 0.43, 0.51, "6/B07GDZ8HN6.glb", "af/af8a4df6.jpg", "Amazon Brand - Rivet 1-Drawer End/Side Table, 43 x 43 x 52cm, MDF with Walnut Veneer/Black Metal Frame"),
    ("B07B7ND7YH", "side_table", "Nesting side tables, brass and walnut", 0.53, 0.75, 0.56, "H/B07B7ND7YH.glb", "e9/e94e8413.jpg", "Amazon Brand – Rivet Mid-Century Nesting Metal Side Table, Set of 2, Brass and Walnut"),
    ("B07HK5HDPZ", "floor_lamp", "Floor lamp, antique brass", 0.31, 0.30, 1.50, "Z/B07HK5HDPZ.glb", "44/446404a2.jpg", "Amazon Brand – Rivet Modern Metal Floor Lamp with Bulb, 59H, Antique Brass"),
    ("B07B4WH5K3", "rug", "Striated jute rug, linen, large", 3.20, 2.43, 0.05, "3/B07B4WH5K3.glb", "1a/1a52b9ad.jpg", "Amazon Brand – Rivet Contemporary Striated Jute Area Rug, 10' 6 x 8', Linen"),
    ("B07B4WKMB1", "rug", "Striated jute rug, oatmeal, small", 1.79, 1.15, 0.02, "1/B07B4WKMB1.glb", "d9/d910a666.jpg", "Amazon Brand – Rivet Contemporary Striated Jute Area Rug, 5' 9 x 3' 9, Oatmeal"),
    ("B07QC6X16K", "plant", "Ceramic planter on a stand, white and tan", 0.31, 0.31, 0.43, "K/B07QC6X16K.glb", "1f/1f3384a9.jpg", "Amazon Brand – Rivet Mid-Century Ceramic Planter with Iron Stand, 17H, White and Tan"),
    ("B07HSCJZQM", "bookshelf", "Roxmere ladder bookcase, acacia", 0.65, 0.45, 2.00, "M/B07HSCJZQM.glb", "c3/c3249cb5.jpg", "Amazon Brand – Rivet Roxmere Modern Ladder Bookcase, 23.6W, Acacia Wood and Dark Metal"),
    ("B075X2WN11", "sofa", "Aiden leather sofa, cognac", 2.20, 0.89, 0.78, "1/B075X2WN11.glb", "bb/bb983934.jpg", "Amazon Brand – Rivet Aiden Tufted Mid-Century Leather Bench Seat Sofa, Without Side Pillows, 86.6W, Cognac"),
    ("B075X2WN27", "armchair", "Aiden leather chair, cognac", 0.93, 0.80, 0.77, "7/B075X2WN27.glb", "94/94871e53.jpg", "Amazon Brand – Rivet Aiden Tufted Mid-Century Modern Leather Accent Chair, 35.4W, Cognac"),
    ("B07QJX1JY9", "sectional", "Aiden leather sectional, cognac", 2.18, 1.51, 0.88, "9/B07QJX1JY9.glb", "23/23dafd0a.jpg", "Amazon Brand – Rivet Aiden Mid-Century Leather Sectional with Tapered Wood Legs, 86W, Cognac"),
    ("B07B4SBZFN", "bed", "Jonathan queen bed, walnut", 1.66, 2.23, 1.36, "N/B07B4SBZFN.glb", "fe/fe7b18f1.jpg", "Rivet Jonathan Mid-Century Modern Wood Queen Bed, 64.5 W, Walnut"),
    ("B07B4W31C3", "bed", "Jonathan full bed, walnut", 1.51, 2.09, 1.30, "3/B07B4W31C3.glb", "fe/fe7b18f1.jpg", "Rivet Jonathan Mid-Century Modern Wood Full Bed, 59.5W, Walnut"),
    ("B07B4VXWTY", "bed", "Jonathan king bed, walnut", 1.66, 2.23, 1.32, "Y/B07B4VXWTY.glb", "fe/fe7b18f1.jpg", "Amazon Brand – Rivet Jonathan Mid-Century Modern Wood King Bed Frame Platform with Headboard, 81.5W, Walnut"),
    ("B07B4W2FFY", "nightstand", "Jonathan nightstand, walnut", 0.52, 0.44, 0.61, "Y/B07B4W2FFY.glb", "3f/3f527ee3.jpg", "Amazon Brand – Rivet Jonathan Mid-Century Modern Wood Bedside Nightstand, 20, Walnut"),
    ("B07B4SBZDV", "dresser", "Jonathan dresser, walnut", 1.47, 0.50, 0.89, "V/B07B4SBZDV.glb", "94/9441a53e.jpg", "Amazon Brand – Rivet Jonathan Mid-Century Modern Wood Bedroom Dresser, Brass-Toned Hardware, 58 Inch Wide, Wal"),
    ("B07B4YZWRK", "dresser", "Jonathan tall dresser, walnut", 0.92, 0.50, 1.36, "K/B07B4YZWRK.glb", "06/060cfb81.jpg", "Amazon Brand – Rivet Jonathan Mid-Century Modern Tall Wood Bedroom Dresser Drawers, 54 Inch Height, Brass Hard"),
    ("B07B4YXNR3", "bed", "Hillmoor queen bed, light grey", 1.66, 2.23, 1.15, "3/B07B4YXNR3.glb", "50/5045b0c2.jpg", "Amazon Brand – Rivet Hillmoor Mid-Century Queen Bed with Headboard, 66W, Light Gray"),
    ("B072ZMHBKQ", "nightstand", "Nightstand, grey and walnut", 0.37, 0.29, 0.51, "Q/B072ZMHBKQ.glb", "8e/8eb95ea0.jpg", "Amazon Brand – Rivet Mid-Century Modern Lacquer Side End Table Nightstand, Grey and Walnut"),
    ("B0856FL9HR", "bed", "Pine platform bed, twin, espresso", 0.96, 1.90, 0.62, "R/B0856FL9HR.glb", "28/2887b77c.jpg", "Amazon Brand – Rivet Modern Solid Pine Wood Platform Bed, Twin, 38.39W, Antique Espresso"),
    ("B0856FK4BW", "bed", "Pine platform bed, full, espresso", 1.35, 1.93, 0.62, "W/B0856FK4BW.glb", "8b/8b7224e1.jpg", "Amazon Brand – Rivet Modern Solid Pine Wood Platform Bed, Full, 53.9W, Antique Espresso"),
    ("B07177355J", "nightstand", "West nightstand, dark oak", 0.56, 0.44, 0.58, "J/B07177355J.glb", "b3/b3b371c0.jpg", "Amazon Brand – Rivet West, Mid-Century, Oak Nightstand Bedside, 1-Drawer Table, 22W, Dark Oak Finish"),
    ("B071SHBTM5", "dresser", "West chest of drawers, dark oak", 0.96, 0.52, 1.32, "5/B071SHBTM5.glb", "48/488235f2.jpg", "Amazon Brand – Rivet West, Mid-Century, Oak Distinct Grain Chest Bedroom Dresser, 38W, Dark Oak Finish"),
    ("B07B86VTX4", "dining_table", "Federal dining table, seats six", 1.52, 0.99, 0.76, "4/B07B86VTX4.glb", "37/37192b81.jpg", "Amazon Brand – Rivet Federal Mid-Century Modern Medium Wood Dining Table, 60-82L, Brown"),
    ("B07B85FJD5", "dining_table", "Federal dining table, small", 0.99, 0.84, 0.76, "5/B07B85FJD5.glb", "91/91d88b57.jpg", "Amazon Brand – Rivet Federal Mid-Century Modern Small Wood Dining Table, 39-57L, Brown"),
    ("B07B78LXYF", "chair", "Federal dining chair, grey", 0.53, 0.68, 0.92, "F/B07B78LXYF.glb", "dd/dd24653f.jpg", "Amazon Brand – Rivet Federal Mid-Century Modern Wood Dining Room Kitchen Chairs, Set of 2, Grey"),
    ("B07B79WSKT", "cabinet", "Federal sideboard", 1.57, 0.50, 0.94, "T/B07B79WSKT.glb", "79/795a58b9.jpg", "Amazon Brand – Rivet Federal Mid-Century Modern Wood Dining Buffet Sideboard, Cabinet Credenza, Storage, Brown"),
    ("B07TSD3DYP", "stool", "Decatur counter stool, light grey", 0.46, 0.56, 0.94, "P/B07TSD3DYP.glb", "d8/d87fba31.jpg", "Amazon Brand – Rivet Decatur Modern Kitchen Counter-Height Stool, 37H, Light Grey"),
    ("B07TYTR8J5", "stool", "Malida swivel counter stool, grey", 0.55, 0.56, 0.94, "5/B07TYTR8J5.glb", "29/2958b275.jpg", "Amazon Brand – Rivet Malida Mid-Century Modern Faux Leather Open Back Swivel Kitchen Counter Stool, 37H, Grey"),
    ("B07B7DL32P", "desk", "Anne desk, walnut", 1.57, 0.72, 0.76, "P/B07B7DL32P.glb", "14/1478021a.jpg", "Amazon Brand – Rivet Anne Mid-Century Modern Wood Office Computer Desk, 62W, Walnut"),
    ("B07B7JY9C5", "cabinet", "Anne filing cabinet, walnut", 0.40, 0.50, 0.46, "5/B07B7JY9C5.glb", "a0/a0382e73.jpg", "Amazon Brand – Rivet Anne Mid-Century Modern Wood Filing Cabinet with Wheels, 20W, Walnut"),
    ("B075Z8M22B", "chair", "Swivel desk chair, marble grey", 0.61, 0.63, 0.88, "B/B075Z8M22B.glb", "62/62cb6175.jpg", "Amazon Brand – Rivet Mid-Century Open Back Swivel Office Desk Chair, 24W, Marble"),
    ("B075ZBW1SN", "desk", "Ventura writing desk, small", 1.28, 0.61, 0.76, "N/B075ZBW1SN.glb", "57/57937f83.jpg", "Amazon Brand – Rivet Ventura Mid-Century Small Reversible Writing Home Office Computer Desk with File Drawer, "),
]
# (id, class, label, w, d, h): the shape the rest of the code reads.
PIECES: list[tuple[str, str, str, float, float, float]] = [row[:6] for row in _ROWS]
BY_ID = {row[0]: row for row in PIECES}
_FILES = {row[0]: row[6:] for row in _ROWS}
SOURCE = "Amazon Berkeley Objects (CC BY 4.0)"
BUCKET = "https://amazon-berkeley-objects.s3.amazonaws.com"

# Rooms' worth of furniture that was designed to go together. A room is furnished from ONE set.
# `pieces` are in the order they matter: the first few make the room, the rest are extras.
SETS: list[dict] = [
    {"id": "sloane_living", "name": "Sloane living room", "rooms": "living room, family room",
     "look": "Pebble-grey tufted seating on tapered walnut legs, walnut tables, a jute rug and a brass lamp.",
     "pieces": ["B072FVHS9G", "B0714QGLSD", "B072ZN4NDS", "B07BVHKPFS", "B07B4WH5K3", "B07GDZ8HN6", "B07HK5HDPZ", "B071W5VJF4", "B07HSCJZQM", "B07QC6X16K"]},
    {"id": "aiden_lounge", "name": "Aiden leather lounge", "rooms": "living room, family room, den",
     "look": "Cognac leather sofa and chair with a round walnut coffee table, brass nesting tables and a jute rug.",
     "pieces": ["B075X2WN11", "B075X2WN27", "B07GDSF3MR", "B07BVHKPFS", "B07B4WH5K3", "B07B7ND7YH", "B07HK5HDPZ", "B07QC6X16K"]},
    {"id": "jonathan_bedroom", "name": "Jonathan walnut bedroom", "rooms": "bedroom (queen bed)",
     "look": "Walnut queen bed with matching nightstands and dresser, brass hardware, a small jute rug.",
     "pieces": ["B07B4SBZFN", "B07B4W2FFY", "B07B4SBZDV", "B07B4WKMB1", "B07B4YZWRK", "B07HK5HDPZ"]},
    {"id": "hillmoor_bedroom", "name": "Hillmoor light bedroom", "rooms": "bedroom (queen bed)",
     "look": "Light-grey upholstered queen bed, grey-and-walnut nightstand, walnut tall dresser, a jute rug.",
     "pieces": ["B07B4YXNR3", "B072ZMHBKQ", "B07B4YZWRK", "B07B4WKMB1", "B07QC6X16K"]},
    {"id": "west_kids_room", "name": "West kid's room", "rooms": "small bedroom, child's room (twin bed)",
     "look": "Low espresso pine twin bed with a dark-oak nightstand and chest, and a small writing desk.",
     "pieces": ["B0856FL9HR", "B07177355J", "B071SHBTM5", "B075ZBW1SN", "B075Z8M22B", "B07B4WKMB1"]},
    {"id": "federal_dining", "name": "Federal dining room", "rooms": "dining room, kitchen island",
     "look": "Walnut dining table and sideboard with grey upholstered chairs; grey counter stools for an island.",
     "pieces": ["B07B86VTX4", "B07B78LXYF", "B07B79WSKT", "B07TSD3DYP", "B07B4WH5K3"]},
    {"id": "anne_office", "name": "Anne walnut office", "rooms": "office, study",
     "look": "Walnut desk with its filing cabinet, a grey swivel chair, a ladder bookcase and a brass lamp.",
     "pieces": ["B07B7DL32P", "B075Z8M22B", "B07B7JY9C5", "B07HSCJZQM", "B07HK5HDPZ", "B07B4WKMB1"]},
]
SET_BY_ID = {s["id"]: s for s in SETS}
# How many of a piece a set wants (default one).
SET_COUNTS: dict[tuple[str, str], int] = {
    ("jonathan_bedroom", "B07B4W2FFY"): 2,
    ("federal_dining", "B07B78LXYF"): 6,
    ("federal_dining", "B07TSD3DYP"): 3,
}


def sets_of(piece_id: str) -> list[str]:
    return [s["name"] for s in SETS if piece_id in s["pieces"]]


# furniture class -> the default model for that class (the first one listed). For photographed pieces.
MODELS: dict[str, str] = {}
for _id, _cls, *_ in PIECES:
    MODELS.setdefault(_cls, _id)

HEADERS = {"User-Agent": "furniture-catalog-fetch/0.1"}
TEXTURE_PX = 1024


def thumbnail_url(piece_id: str) -> str:
    return f"{BUCKET}/images/small/{_FILES[piece_id][1]}"


def product_name(piece_id: str) -> str:
    return _FILES[piece_id][2]


def catalog_dir() -> Path:
    path = Path(env("CATALOG_DIR") or REPO_ROOT / "data" / "catalog") / "abo"
    path.mkdir(parents=True, exist_ok=True)
    return path


def shrink_textures(glb: bytes, size: int = TEXTURE_PX) -> bytes:
    """The same GLB with every embedded image at most `size` px a side (JPEG, or PNG if it has
    transparency). Geometry is untouched. A file that is not a plain binary glTF comes back as it is."""
    from PIL import Image

    if glb[:4] != b"glTF":
        return glb
    json_len = struct.unpack_from("<I", glb, 12)[0]
    doc = json.loads(glb[20:20 + json_len])
    start = 20 + json_len + 8
    blob = glb[start:start + struct.unpack_from("<I", glb, 20 + json_len)[0]]
    views = doc.get("bufferViews", [])
    replaced: dict[int, bytes] = {}
    for image in doc.get("images", []):
        index = image.get("bufferView")
        if index is None:
            continue
        view = views[index]
        raw = blob[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
        picture = Image.open(io.BytesIO(raw))
        if max(picture.size) <= size and image.get("mimeType") == "image/jpeg":
            continue
        picture.thumbnail((size, size))
        out = io.BytesIO()
        if picture.mode in ("RGBA", "LA", "P") and "A" in picture.convert("RGBA").getbands() and picture.convert("RGBA").getextrema()[3][0] < 255:
            picture.convert("RGBA").save(out, "PNG", optimize=True)
            image["mimeType"] = "image/png"
        else:
            picture.convert("RGB").save(out, "JPEG", quality=86)
            image["mimeType"] = "image/jpeg"
        replaced[index] = out.getvalue()
    if not replaced:
        return glb
    packed = bytearray()
    for index, view in enumerate(views):
        data = replaced.get(index) or blob[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]
        packed += b"\x00" * (-len(packed) % 4)
        view["byteOffset"], view["byteLength"] = len(packed), len(data)
        packed += data
    packed += b"\x00" * (-len(packed) % 4)
    doc["buffers"] = [{"byteLength": len(packed)}]
    text = json.dumps(doc, separators=(",", ":")).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(packed), 0x004E4942) + bytes(packed)
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


def fetch(piece_id: str) -> bytes:
    """One catalog model as a GLB with browser-sized textures. Cached on disk."""
    small = catalog_dir() / f"{piece_id}.1k.glb"
    if small.exists():
        return small.read_bytes()
    original = catalog_dir() / f"{piece_id}.glb"
    if not original.exists():
        with httpx.Client(timeout=120, headers=HEADERS, follow_redirects=True) as http:
            original.write_bytes(http.get(f"{BUCKET}/3dmodels/original/{_FILES[piece_id][0]}").raise_for_status().content)
    glb = shrink_textures(original.read_bytes())
    small.write_bytes(glb)
    return glb


class CatalogTo3D:
    def generate(self, image: bytes, segment: Segment, piece: Piece) -> Model3D:
        piece_id = MODELS.get("sofa" if piece.item_class == "sectional" else piece.item_class)
        if piece_id:
            try:
                return Model3D(fetch(piece_id))
            except Exception as e:  # offline, or the file moved: a box is better than nothing
                log.warning("catalog model %s unavailable (%s); using a box", piece_id, e)
        return BoxTo3D().generate(image, segment, piece)


if __name__ == "__main__":  # python -m services.api.objects.providers.catalog : download everything now
    for piece_id, _cls, label, *_ in PIECES:
        try:
            print(f"{label:44} {len(fetch(piece_id)) / 1e6:5.1f} MB")
        except Exception as e:
            print(f"{label:44} FAILED: {e}")
