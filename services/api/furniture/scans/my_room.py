"""The user's own bedroom, scanned by hand from one photo (my-room.jpg, 1695 x 1275 px).

Every thing in the photo is listed here: where it is in the photo (to click on it), what it is,
how it stood (on the floor, on the bed, on the desk, on the shelf, on the wall, at the window),
and a 3D model of it, built from boxes, cylinders, cloth and curtains, with its pictures and
patterns cut from the photo itself where they can be seen (the map, the poster, the floral duvet,
the rug, the little framed drawing, the beige curtains).

The room around them: dark stained oak floor, pale grey walls, white trim and door.
"""

from __future__ import annotations

import math
import random
from pathlib import Path


from services.api.furniture.scans import textures as tx
from services.api.furniture.scans.mesh import Model, material
from services.api.furniture.scans.types import Thing

PHOTO = Path(__file__).with_name("my-room.jpg")
SIZE = (1695, 1275)


def _photo():
    return tx.photo(str(PHOTO))


def _crop(box: tuple[int, int, int, int]):
    return _photo().crop(box)


WHITE = (240, 240, 236)
BLACK = (24, 24, 26)


# --- furniture on the floor ----------------------------------------------------------------------


def bed() -> Model:
    """A low twin bed on a black metal frame, the mattress in a cream ticking-striped sheet that
    hangs down its sides."""
    m = Model()
    w, d = 0.99, 1.95
    metal = material(BLACK, 0.45, 0.6)
    for sx in (-1, 1):
        for sz in (-1, 1):
            m.box(0.035, 0.2, 0.035, at=(sx * (w / 2 - 0.06), 0.1, sz * (d / 2 - 0.06)), mat=metal)
        m.box(0.03, 0.03, d - 0.08, at=(sx * (w / 2 - 0.05), 0.185, 0), mat=metal)
    for sz in (-1, 0, 1):
        m.box(w - 0.1, 0.03, 0.03, at=(0, 0.185, sz * (d / 2 - 0.05)), mat=metal)
    sheet = material((255, 255, 255), 0.95, image=tx.stripes())
    m.box(w - 0.02, 0.24, d - 0.02, at=(0, 0.32, 0), mat=sheet)  # the mattress
    # The sheet hanging over the sides and the foot.
    drop = material((255, 255, 255), 0.95, image=tx.stripes((512, 256)), double=True)
    for sx in (-1, 1):
        m.panel(d, 0.3, at=(sx * (w / 2 + 0.004), 0.29, 0), rot=(0, sx * 90, 0), mat=drop)
    m.panel(w, 0.3, at=(0, 0.29, d / 2 + 0.004), mat=drop)
    return m


def desk() -> Model:
    """A white desk, a closed side on the left and a three-drawer pedestal on the right, a modesty
    panel at the back."""
    m = Model()
    w, d, h = 1.42, 0.6, 0.75
    white = material(WHITE, 0.55)
    seam = material((196, 198, 200), 0.6)
    m.box(w, 0.03, d, at=(0, h - 0.015, 0), mat=white)
    m.box(0.025, h - 0.03, d - 0.02, at=(-w / 2 + 0.0125, (h - 0.03) / 2, 0), mat=white)
    pw = 0.42
    px = w / 2 - pw / 2
    m.box(pw, h - 0.03, d - 0.02, at=(px, (h - 0.03) / 2, 0), mat=white)
    for y in (0.24, 0.5, 0.64):  # the drawers' edges, with a pull under each one
        m.box(pw - 0.02, 0.006, 0.004, at=(px, y, d / 2 - 0.006), mat=seam)
        m.box(0.12, 0.012, 0.012, at=(px, y - 0.02, d / 2 - 0.004), mat=seam)
    m.box(w - pw - 0.03, 0.32, 0.018, at=(-(pw) / 2 + 0.002, h - 0.2, -d / 2 + 0.03), mat=white)
    return m


def office_chair() -> Model:
    """A black mesh office chair: five-star base on casters, a mesh seat and a tall mesh back
    with an X-shaped brace behind it, loop armrests."""
    m = Model()
    black = material((30, 30, 32), 0.5, 0.2)
    grey = material((70, 70, 74), 0.4, 0.5)
    weave = material((255, 255, 255), 0.85, image=tx.mesh_back())
    for k in range(5):
        a = math.radians(k * 72 + 18)
        tip = (math.sin(a) * 0.3, 0.06, math.cos(a) * 0.3)
        m.rod((0, 0.09, 0), tip, 0.018, mat=black)
        m.ball(0.025, 0.025, 0.025, at=(tip[0], 0.025, tip[2]), mat=black, subdivisions=2)
    m.cyl(0.028, 0.34, at=(0, 0.25, 0), mat=grey)
    m.box(0.5, 0.07, 0.48, at=(0, 0.46, 0.02), mat=black)
    m.box(0.47, 0.012, 0.45, at=(0, 0.5, 0.02), mat=weave)
    # The back: a frame holding the mesh, leaning back a little.
    back_rot = (-8, 0, 0)
    m.box(0.48, 0.56, 0.03, at=(0, 0.83, -0.24), rot=back_rot, mat=black)
    m.box(0.44, 0.52, 0.006, at=(0, 0.83, -0.222), rot=back_rot, mat=weave)
    m.rod((-0.2, 0.58, -0.27), (0.2, 1.05, -0.33), 0.012, mat=black)
    m.rod((0.2, 0.58, -0.27), (-0.2, 1.05, -0.33), 0.012, mat=black)
    m.rod((0, 0.5, -0.12), (0, 0.6, -0.26), 0.02, mat=black)
    for sx in (-1, 1):  # the armrests
        m.rod((sx * 0.27, 0.48, -0.1), (sx * 0.29, 0.68, -0.08), 0.015, mat=black)
        m.rod((sx * 0.27, 0.48, 0.12), (sx * 0.29, 0.68, 0.1), 0.015, mat=black)
        m.box(0.06, 0.025, 0.26, at=(sx * 0.29, 0.69, 0.01), mat=black)
    return m


def pine_shelf() -> Model:
    """A long, low honey-pine bookcase under the window: a top that holds things, two open rows
    split into three bays, full of notebooks, cups, cans and boxes."""
    m = Model()
    w, d, h = 1.3, 0.32, 0.5
    wood = material((255, 255, 255), 0.6, image=tx.pine())
    dark = material((150, 96, 48), 0.7)
    t = 0.022
    m.box(w, t, d, at=(0, h - t / 2, 0), mat=wood)
    m.box(w - 2 * t, t, d - 0.02, at=(0, 0.26, 0), mat=wood)
    m.box(w - 2 * t, t, d - 0.02, at=(0, 0.06, 0), mat=wood)
    m.box(w - 2 * t, 0.05, 0.02, at=(0, 0.025, d / 2 - 0.02), mat=dark)
    for sx in (-1, 1):
        m.box(t, h - t, d, at=(sx * (w / 2 - t / 2), (h - t) / 2, 0), mat=wood)
    for x in (-0.22, 0.22):
        m.box(t, h - 0.1, d - 0.02, at=(x, (h - t) / 2 + 0.03, 0), mat=wood)
    m.box(w - 2 * t, h - t, 0.008, at=(0, (h - t) / 2, -d / 2 + 0.004), mat=dark)
    # What is in the bays.
    spines = material((255, 255, 255), 0.7, image=tx.book_spines())
    m.box(0.36, 0.1, 0.24, at=(-0.43, 0.12, 0.0), mat=spines)
    m.box(0.3, 0.06, 0.22, at=(-0.45, 0.305, 0.0), mat=spines)
    m.cyl(0.04, 0.1, at=(-0.13, 0.12, 0.05), color=(236, 236, 232), rough=0.3)  # a cup
    m.cyl(0.03, 0.17, at=(-0.03, 0.155, 0.02), color=(190, 192, 196), metal=0.7, rough=0.3)  # a spray can
    m.cyl(0.031, 0.02, at=(-0.03, 0.25, 0.02), color=(210, 40, 40))
    m.box(0.12, 0.08, 0.1, at=(0.08, 0.11, 0.04), color=(220, 60, 60))
    m.box(0.3, 0.07, 0.2, at=(0.43, 0.105, 0.0), color=(28, 28, 30))
    m.box(0.12, 0.12, 0.12, at=(0.34, 0.33, 0.02), color=(214, 196, 168))
    m.cyl(0.035, 0.13, at=(0.1, 0.335, 0.05), color=(200, 140, 200))
    return m


def rug() -> Model:
    """An olive and cream damask rug, its scrolls cut from the photo."""
    m = Model()
    patch = tx.warp(_photo(), ((730, 1010), (950, 1010), (950, 1110), (780, 1110)), (200, 100))
    pattern = tx.tile(patch, 4, 5)
    m.box(2.0, 0.012, 1.4, at=(0, 0.006, 0), mat=material((255, 255, 255), 0.95, image=pattern))
    return m


def storage_box() -> Model:
    """A big black storage box, its lid held down with duct tape, a shipping label on top."""
    m = Model()
    w, h, d = 0.62, 0.44, 0.5
    m.box(w, h, d, at=(0, h / 2, 0), color=(34, 32, 31), rough=0.9)
    m.panel(w, d, at=(0, h + 0.001, 0), rot=(-90, 0, 0), mat=material((255, 255, 255), 0.85, image=tx.taped_box()))
    m.panel(0.1, 0.2, at=(0, h - 0.1, d / 2 + 0.001), color=(86, 86, 88))
    return m


def air_purifier() -> Model:
    """A white tower air purifier, holes all over its front, its cord trailing behind."""
    m = Model()
    w, h, d = 0.36, 0.58, 0.2
    m.rounded(w, h, d, 0.035, at=(0, h / 2, 0), color=(242, 243, 244), rough=0.45)
    m.panel(w - 0.04, h - 0.06, at=(0, h / 2, d / 2 + 0.001), mat=material((255, 255, 255), 0.5, image=tx.grille()))
    m.box(0.06, 0.01, 0.04, at=(0.11, h + 0.004, 0.04), color=(60, 120, 200))
    m.rod((0.1, 0.02, -d / 2), (0.16, 0.008, -d / 2 - 0.18), 0.004, color=(240, 240, 240))
    return m


def cable_tangle() -> Model:
    """The pile of chargers and cables on the rug by the desk, with a pair of white earbuds."""
    m = Model()
    rng = random.Random(9)
    black = material((18, 18, 20), 0.6)
    white = material((236, 236, 236), 0.5)
    for k in range(34):
        x0, z0 = rng.uniform(-0.2, 0.2), rng.uniform(-0.18, 0.18)
        pts = [(x0, 0.005, z0)]
        for _ in range(4):
            x, y, z = pts[-1]
            pts.append((max(-0.24, min(0.24, x + rng.uniform(-0.1, 0.1))), rng.uniform(0.004, 0.035), max(-0.21, min(0.21, z + rng.uniform(-0.1, 0.1)))))
        for a, b in zip(pts, pts[1:]):
            m.rod(a, b, 0.0035, mat=black, sections=5)
    for k in range(3):  # power bricks
        m.box(0.06, 0.03, 0.1, at=(rng.uniform(-0.15, 0.15), 0.015, rng.uniform(-0.12, 0.12)), rot=(0, rng.uniform(0, 180), 0), mat=black)
    pts = [(0.05, 0.004, 0.05), (0.1, 0.004, 0.12), (0.06, 0.004, 0.2), (0.13, 0.004, 0.22), (0.2, 0.004, 0.16)]
    for a, b in zip(pts, pts[1:]):
        m.rod(a, b, 0.0025, mat=white, sections=5)
    for x, z in ((0.2, 0.16), (0.22, 0.13)):
        m.ball(0.012, 0.01, 0.012, at=(x, 0.01, z), mat=white, subdivisions=2)
    return m


# --- on the desk -----------------------------------------------------------------------------------


def pc_tower() -> Model:
    """A white gaming PC tower with a curved shell, its black front split by a light strip."""
    m = Model()
    w, h, d = 0.21, 0.48, 0.43
    m.rounded(d, h, w, 0.09, at=(0, h / 2, 0), rot=(0, 90, 0), color=(244, 244, 246), rough=0.35)
    m.box(0.13, h - 0.08, 0.01, at=(0, h / 2, d / 2 + 0.002), color=(22, 22, 26), rough=0.3)
    m.box(0.008, h - 0.16, 0.004, at=(0, h / 2, d / 2 + 0.009), mat=material((200, 220, 255), 0.3, glow=(150, 180, 255)))
    for sx in (-1, 1):
        m.box(0.004, 0.24, 0.3, at=(sx * (w / 2 + 0.001), h / 2, 0), color=(40, 40, 44))
    return m


def monitor() -> Model:
    """A 27-inch black monitor on a stand."""
    m = Model()
    black = material((22, 22, 24), 0.4, 0.3)
    m.box(0.26, 0.015, 0.18, at=(0, 0.0075, 0.0), mat=black)
    m.box(0.05, 0.22, 0.03, at=(0, 0.12, -0.05), mat=black)
    m.box(0.62, 0.37, 0.03, at=(0, 0.27, -0.025), mat=black)
    m.panel(0.6, 0.35, at=(0, 0.275, -0.009), mat=material((12, 14, 18), 0.15, 0.1))
    return m


def water_bottle() -> Model:
    m = Model()
    m.cyl(0.033, 0.2, at=(0, 0.1, 0), mat=material((190, 220, 240), 0.1, alpha=0.5), sections=20)
    m.cyl(0.0335, 0.06, at=(0, 0.1, 0), color=(250, 250, 250), sections=20)
    m.cyl(0.015, 0.018, at=(0, 0.209, 0), color=(245, 245, 245), sections=12)
    return m


def mouse() -> Model:
    m = Model()
    m.ball(0.031, 0.018, 0.056, at=(0, 0.0, 0), color=(244, 244, 244), rough=0.3, squareness=1.3)
    return m


def sticky_notes() -> Model:
    m = Model()
    m.box(0.076, 0.04, 0.076, at=(0, 0.02, 0), color=(48, 176, 230))
    m.box(0.074, 0.003, 0.074, at=(0, 0.041, 0), color=(90, 200, 240))
    return m


def pink_box() -> Model:
    m = Model()
    m.box(0.22, 0.05, 0.15, at=(0, 0.025, 0), color=(238, 104, 146))
    m.panel(0.12, 0.05, at=(0, 0.051, 0), rot=(-90, 0, 0), color=(250, 236, 120))
    return m


def shirt_stack() -> Model:
    """New black t-shirts, still folded in their clear bags, one with a red tag."""
    m = Model()
    rng = random.Random(4)
    bag = material((235, 240, 245), 0.15, alpha=0.25)
    for k in range(4):
        y = 0.012 + k * 0.026
        turn = rng.uniform(-12, 12)
        x, z = rng.uniform(-0.02, 0.02), rng.uniform(-0.02, 0.02)
        m.box(0.36, 0.022, 0.28, at=(x, y, z), rot=(0, turn, 0), color=(22, 22, 24), rough=0.95)
        m.box(0.38, 0.024, 0.3, at=(x, y, z), rot=(0, turn, 0), mat=bag)
    m.box(0.09, 0.003, 0.05, at=(0.05, 0.116, 0.02), rot=(0, 10, 0), color=(220, 60, 60))
    m.box(0.12, 0.003, 0.08, at=(-0.08, 0.116, -0.05), rot=(0, -6, 0), color=(240, 240, 240))
    return m


def scanner() -> Model:
    """A small black document scanner, its paper tray up, a few sheets on it."""
    m = Model()
    m.box(0.36, 0.1, 0.24, at=(0, 0.05, 0), color=BLACK, rough=0.4)
    m.box(0.3, 0.008, 0.16, at=(0, 0.14, -0.13), rot=(30, 0, 0), color=(30, 30, 32))
    m.box(0.21, 0.002, 0.15, at=(0, 0.147, -0.12), rot=(30, 0, 0), color=(246, 246, 244))
    m.box(0.21, 0.002, 0.28, at=(0.02, 0.102, 0.02), rot=(0, 8, 0), color=(246, 246, 244))
    return m


def desk_cables() -> Model:
    """Chargers and loose cables at the back of the desk."""
    m = Model()
    rng = random.Random(21)
    for colour in ((240, 240, 240), (20, 20, 20), (240, 240, 240), (40, 160, 80)):
        pts = [(rng.uniform(-0.12, 0.12), 0.004, rng.uniform(-0.08, 0.08)) for _ in range(5)]
        for a, b in zip(pts, pts[1:]):
            m.rod(a, b, 0.003, color=colour, sections=5)
    m.box(0.05, 0.025, 0.05, at=(0.08, 0.0125, 0.0), color=(240, 240, 240))
    return m


# --- on the shelf ---------------------------------------------------------------------------------


def framed_drawing() -> Model:
    """A small orange frame on a stand, a pencil drawing in it (from the photo)."""
    m = Model()
    lean = (-10, 0, 0)
    m.box(0.16, 0.21, 0.016, at=(0, 0.105, 0), rot=lean, color=(222, 120, 50), rough=0.5)
    drawing = _crop((657, 463, 684, 497)).resize((96, 128))
    m.panel(0.12, 0.17, at=(0, 0.106, 0.0095), rot=lean, mat=material((255, 255, 255), 0.8, image=drawing))
    m.box(0.012, 0.16, 0.008, at=(0, 0.08, -0.05), rot=(20, 0, 0), color=(200, 110, 45))
    return m


def model_plane(colour=(60, 62, 66), span=0.5, length=0.42) -> Model:
    """A small model aeroplane: fuselage, wings, tail, propeller."""
    m = Model()
    body = material(colour, 0.6)
    m.box(0.035, 0.035, length, at=(0, 0.05, 0), mat=body)
    m.box(span, 0.008, 0.08, at=(0, 0.06, length * 0.12), mat=body)
    m.box(span * 0.36, 0.006, 0.05, at=(0, 0.055, -length * 0.45), mat=body)
    m.box(0.006, 0.06, 0.05, at=(0, 0.09, -length * 0.45), mat=body)
    m.box(0.14, 0.012, 0.004, at=(0, 0.05, length / 2 + 0.004), rot=(0, 0, 25), color=(30, 30, 30))
    for sx in (-1, 1):  # the landing gear
        m.rod((sx * 0.04, 0.04, length * 0.2), (sx * 0.07, 0.0, length * 0.22), 0.003, color=(40, 40, 40))
    return m


def figurine() -> Model:
    m = Model()
    m.cyl(0.022, 0.055, at=(0, 0.0275, 0), color=(110, 190, 110))
    m.cyl(0.03, 0.012, at=(0, 0.006, 0), color=(245, 245, 240))
    m.ball(0.02, 0.02, 0.02, at=(0, 0.075, 0), color=(250, 225, 200))
    m.ball(0.022, 0.012, 0.022, at=(0, 0.09, 0), color=(110, 190, 110))
    return m


def red_cloth() -> Model:
    m = Model()
    m.cloth(0.3, 0.2, 0.04, mat=material((196, 30, 40), 0.95), seed=3, lumps=4)
    return m


def blue_folder() -> Model:
    m = Model()
    m.box(0.31, 0.012, 0.24, at=(0, 0.006, 0), color=(70, 150, 225), rough=0.4)
    m.box(0.3, 0.004, 0.22, at=(0.004, 0.014, 0.004), rot=(0, 3, 0), color=(246, 246, 244))
    return m


def plush_frog() -> Model:
    """A small olive-green plush toy lying on the shelf."""
    m = Model()
    green = material((96, 126, 64), 1.0)
    m.ball(0.08, 0.035, 0.05, at=(0, 0.035, 0), mat=green)
    m.ball(0.035, 0.03, 0.035, at=(0.085, 0.045, 0), mat=green)
    for sx in (-1, 1):
        m.ball(0.008, 0.008, 0.008, at=(0.11, 0.065, sx * 0.015), color=(20, 20, 20), subdivisions=1)
        m.ball(0.03, 0.012, 0.015, at=(-0.03, 0.012, sx * 0.05), mat=green, subdivisions=2)
    return m


def black_case() -> Model:
    m = Model()
    m.box(0.32, 0.05, 0.22, at=(0, 0.025, 0), color=(26, 26, 28), rough=0.5)
    m.box(0.08, 0.002, 0.04, at=(0.09, 0.051, 0.06), color=(236, 236, 230))
    return m


# --- on the bed -----------------------------------------------------------------------------------


def duvet() -> Model:
    """The bunched-up duvet in its black-on-white floral cover (the flowers from the photo)."""
    m = Model()
    flowers = tx.tile(_crop((80, 665, 310, 790)), 2, 2)
    m.cloth(0.98, 1.05, 0.3, mat=material((255, 255, 255), 0.95, image=flowers), seed=1, lumps=6, drape=0.03)
    return m


def pillow(colour=(246, 246, 242), w=0.66, d=0.46, h=0.16, image=None) -> Model:
    m = Model()
    mat = material((255, 255, 255) if image else colour, 0.95, image=image)
    m.ball(w / 2, h / 2, d / 2, at=(0, h / 2, 0), mat=mat, squareness=2.6)
    return m


def plaid_shirt() -> Model:
    m = Model()
    m.cloth(0.46, 0.4, 0.08, mat=material((255, 255, 255), 0.95, image=tx.plaid()), seed=5, lumps=5)
    return m


def bedding_bag() -> Model:
    """A spare comforter still in its clear zip bag."""
    m = Model()
    m.cloth(0.56, 0.52, 0.22, mat=material((236, 234, 228), 0.35), seed=8, lumps=3)
    m.box(0.5, 0.004, 0.006, at=(0, 0.2, 0.18), color=(200, 200, 200))
    return m


# --- on the walls and at the window ----------------------------------------------------------------


def us_map() -> Model:
    """The pull-down style map of the United States, taped to the wall (from the photo)."""
    m = Model()
    picture = tx.warp(_photo(), ((919, 293), (1099, 287), (1118, 459), (917, 452)), (512, 352))
    m.box(1.26, 0.86, 0.004, at=(0, 0.43, -0.002), color=(236, 232, 220))
    m.panel(1.24, 0.84, at=(0, 0.43, 0.0005), mat=material((255, 255, 255), 0.7, image=picture))
    return m


def scroll_poster() -> Model:
    """The hanging scroll poster: the print between two black rods, hung from a string (the
    print from the photo)."""
    m = Model()
    picture = tx.warp(_photo(), ((1111, 136), (1253, 70), (1226, 352), (1099, 368)), (320, 480))
    w, h = 0.45, 0.66
    m.panel(w, h, at=(0, h / 2 + 0.02, 0.006), mat=material((255, 255, 255), 0.6, image=picture))
    m.box(w, h, 0.003, at=(0, h / 2 + 0.02, 0.003), color=(240, 240, 240))
    for y in (0.012, h + 0.03):
        m.rod((-w / 2 - 0.03, y, 0.008), (w / 2 + 0.03, y, 0.008), 0.011, color=(18, 18, 18), sections=12)
    top = h + 0.03
    m.rod((-w / 2 + 0.02, top, 0.006), (0, top + 0.2, 0.004), 0.0015, color=(20, 20, 20))
    m.rod((w / 2 - 0.02, top, 0.006), (0, top + 0.2, 0.004), 0.0015, color=(20, 20, 20))
    return m


def taped_papers() -> Model:
    """Printouts and notes taped to the wall under the map (from the photo)."""
    m = Model()
    picture = tx.warp(_photo(), ((962, 457), (1097, 455), (1097, 560), (964, 566)), (384, 320))
    m.panel(0.55, 0.42, at=(0, 0.21, 0.004), mat=material((255, 255, 255), 0.8, image=picture))
    m.box(0.2, 0.28, 0.002, at=(-0.2, 0.2, 0.001), rot=(0, 0, 4), color=(246, 246, 242))
    m.box(0.22, 0.3, 0.002, at=(0.19, 0.22, 0.0015), rot=(0, 0, -3), color=(244, 244, 240))
    return m


def wall_dots() -> Model:
    """Rows of sticker dots and bits of washi tape stuck around the map."""
    m = Model()
    dots = material((255, 255, 255), 0.6, image=tx.wall_dots(), cutout=True)
    m.panel(0.9, 0.6, at=(0, 0.3, 0.003), mat=dots)
    m.panel(0.9, 0.6, at=(0, 0.3, 0.0), mat=dots)
    return m


def wall_hook() -> Model:
    m = Model()
    m.box(0.03, 0.05, 0.008, at=(0, 0.03, 0.004), color=(236, 236, 232))
    m.rod((0, 0.02, 0.008), (0, 0.02, 0.035), 0.003, color=(200, 200, 200))
    m.rod((0, 0.02, 0.035), (0, 0.04, 0.04), 0.003, color=(200, 200, 200))
    return m


def sheer_curtains() -> Model:
    """The sheer white window curtains on a white rod: birds flying over a navy city skyline."""
    m = Model()
    w, h = 1.9, 2.1
    m.curtain(w, h, at=(0, h / 2, 0.04), mat=material((255, 255, 255), 0.9, image=tx.bird_curtain()), folds=8, depth=0.07)
    m.rod((-w / 2 - 0.08, h + 0.02, 0.04), (w / 2 + 0.08, h + 0.02, 0.04), 0.012, color=(240, 240, 240), sections=12)
    for sx in (-1, 1):
        m.box(0.02, 0.05, 0.05, at=(sx * (w / 2 + 0.05), h + 0.02, 0.02), color=(240, 240, 240))
    return m


def beige_curtains() -> Model:
    """Heavy beige curtain panels, floor to ceiling, in deep folds (the cloth from the photo)."""
    m = Model()
    w, h = 1.5, 2.3
    cloth = _crop((1450, 140, 1640, 640)).resize((256, 512))
    m.curtain(w, h, at=(0, h / 2, 0.07), mat=material((255, 255, 255), 0.95, image=cloth), folds=7, depth=0.12, sway=0.04)
    m.rod((-w / 2 - 0.06, h + 0.02, 0.07), (w / 2 + 0.06, h + 0.02, 0.07), 0.014, color=(60, 50, 42), sections=12, metal=0.4)
    for sx in (-1, 1):
        m.box(0.02, 0.05, 0.08, at=(sx * (w / 2 + 0.03), h + 0.02, 0.035), color=(60, 50, 42))
    return m


# --- the scan ------------------------------------------------------------------------------------

NAME = "My bedroom"
BLURB = "A low twin bed under the window, a white desk with a gaming PC and a monitor, a pine shelf, the map and the poster on the wall."
FINISHES = ["Dark stained oak floor", "Pale grey walls", "White trim and door"]

THINGS: list[Thing] = [
    # On the floor, most important first: in a small room the last ones wait in storage.
    Thing("bed", "Twin bed", "bed", "furniture", (0, 560, 820, 1215), bed, color="cream", material="cotton",
          note="Low black metal frame, cream ticking-striped sheet hanging to the floor."),
    Thing("desk", "White desk", "desk", "furniture", (815, 625, 1395, 965), desk, color="white", material="laminate",
          note="Closed side on the left, three drawers on the right."),
    Thing("chair", "Mesh office chair", "chair", "furniture", (870, 630, 1170, 950), office_chair, color="black", material="mesh",
          beside="desk", note="Black mesh seat and back, X brace, loop armrests, five casters."),
    Thing("shelf", "Low pine bookcase", "bookshelf", "furniture", (600, 495, 925, 615), pine_shelf, color="honey", material="pine",
          note="Two open rows in three bays, full of notebooks, a cup, a spray can and boxes."),
    Thing("rug", "Damask rug", "rug", "floor", (610, 800, 1540, 1275), rug, color="olive", material="wool",
          note="Olive and cream scrolls, cut from the photo."),
    Thing("purifier", "Air purifier", "other", "floor", (1338, 835, 1597, 1103), air_purifier, color="white", material="plastic",
          beside="desk", note="White tower, perforated front."),
    Thing("box", "Black storage box", "ottoman", "floor", (955, 878, 1362, 1275), storage_box, color="black", material="cardboard",
          beside="desk", note="Lid taped down with duct tape, a shipping label on top."),
    Thing("cables", "Cable pile and earbuds", "decor", "floor", (730, 815, 960, 1010), cable_tangle, color="black",
          beside="desk", note="Chargers, cables and white wired earbuds."),
    # On the desk.
    Thing("monitor", "Monitor", "other", "desk", (1095, 472, 1330, 668), monitor, rests_on="desk", offset=(-0.14, 0.12), color="black"),
    Thing("pc", "Gaming PC tower", "other", "desk", (1278, 513, 1436, 748), pc_tower, rests_on="desk", offset=(-0.05, 0.58), color="white",
          note="Curved white shell, black front with a light strip."),
    Thing("scanner", "Document scanner", "decor", "desk", (940, 560, 1052, 630), scanner, rests_on="desk", offset=(-0.14, -0.3), color="black"),
    Thing("shirts", "New t-shirts in bags", "decor", "desk", (818, 578, 978, 657), shirt_stack, rests_on="desk", offset=(0.05, -0.5), color="black"),
    Thing("bottle", "Water bottle", "decor", "desk", (1195, 608, 1236, 700), water_bottle, rests_on="desk", offset=(0.0, 0.36)),
    Thing("mouse", "Mouse", "decor", "desk", (1143, 630, 1182, 657), mouse, rests_on="desk", offset=(0.12, 0.2), color="white"),
    Thing("sticky", "Sticky notes", "decor", "desk", (1224, 668, 1268, 697), sticky_notes, rests_on="desk", offset=(0.18, 0.4), color="blue"),
    Thing("pinkbox", "Pink box", "decor", "desk", (1115, 688, 1200, 737), pink_box, rests_on="desk", offset=(0.2, 0.0), color="pink"),
    Thing("deskcables", "Chargers and cables", "decor", "desk", (1195, 690, 1290, 742), desk_cables, rests_on="desk", offset=(0.12, 0.42)),
    # On the shelf.
    Thing("frame", "Framed drawing", "decor", "shelf", (648, 453, 692, 503), framed_drawing, rests_on="shelf", offset=(-0.06, -0.5), color="orange"),
    Thing("case", "Black case", "decor", "shelf", (602, 497, 657, 516), black_case, rests_on="shelf", offset=(0.02, -0.38), color="black"),
    Thing("plane", "Model aeroplane", "decor", "shelf", (660, 485, 745, 522), model_plane, rests_on="shelf", offset=(0.0, -0.12), turn=70, color="grey"),
    Thing("plane2", "White model aeroplane", "decor", "shelf", (765, 505, 830, 535), lambda: model_plane((236, 236, 232), 0.34, 0.3),
          rests_on="shelf", offset=(0.02, 0.12), turn=-60, color="white"),
    Thing("figurine", "Little figurine", "decor", "shelf", (740, 502, 770, 525), figurine, rests_on="shelf", offset=(-0.08, 0.02), color="green"),
    Thing("cloth", "Red cloth", "decor", "shelf", (810, 530, 865, 548), red_cloth, rests_on="shelf", offset=(0.04, 0.3), color="red"),
    Thing("folder", "Blue folder", "decor", "shelf", (848, 527, 898, 540), blue_folder, rests_on="shelf", offset=(0.0, 0.46), color="blue"),
    Thing("plush", "Green plush toy", "decor", "shelf", (875, 532, 910, 547), plush_frog, rests_on="shelf", offset=(0.06, 0.55), color="green"),
    # On the bed.
    Thing("duvet", "Floral duvet", "decor", "bed", (0, 610, 460, 830), duvet, rests_on="bed", offset=(0.15, 0.0), color="white", material="cotton",
          note="Black-on-white flowers, cut from the photo."),
    Thing("pillow", "Big white pillow", "decor", "bed", (445, 512, 640, 600), pillow, rests_on="bed", offset=(-0.7, -0.1), color="white"),
    Thing("pillow2", "Grey pillow", "decor", "bed", (628, 585, 818, 665), lambda: pillow((178, 180, 186), 0.6, 0.4, 0.13),
          rests_on="bed", offset=(-0.72, 0.14), turn=8, color="grey"),
    Thing("heart", "Heart pillow", "decor", "bed", (355, 558, 455, 635), lambda: pillow(w=0.42, d=0.32, h=0.11, image=tx.hearts()),
          rests_on="bed", offset=(-0.4, -0.2), turn=-15, color="yellow"),
    Thing("flannel", "Plaid flannel shirt", "decor", "bed", (372, 578, 495, 668), plaid_shirt, rests_on="bed", offset=(-0.35, 0.2), color="red"),
    Thing("bag", "Comforter in its bag", "decor", "bed", (408, 618, 662, 800), bedding_bag, rests_on="bed", offset=(0.2, 0.2), color="white"),
    # On the walls, by the piece they hang over.
    Thing("map", "Map of the United States", "wall_art", "wall", (913, 280, 1128, 460), us_map, on_wall=True, anchor="desk", along=-0.1, hang=1.26),
    Thing("poster", "Hanging scroll poster", "wall_art", "wall", (1095, 52, 1262, 375), scroll_poster, on_wall=True, anchor="desk", along=0.8, hang=1.25),
    Thing("papers", "Papers taped to the wall", "wall_art", "wall", (958, 452, 1100, 580), taped_papers, on_wall=True, anchor="desk", along=-0.1, hang=0.82),
    Thing("dots", "Sticker dots", "wall_art", "wall", (740, 280, 960, 470), wall_dots, on_wall=True, anchor="desk", along=-1.05, hang=1.2),
    Thing("hook", "Wall hook", "wall_art", "wall", (508, 85, 538, 108), wall_hook, on_wall=True, anchor="bed", along=0.6, hang=2.05),
    # At the windows.
    Thing("sheers", "Bird and skyline curtains", "wall_art", "window", (0, 95, 545, 650), sheer_curtains, on_wall=True, window="cover",
          color="white", material="voile", note="Sheer white, birds over a navy city skyline."),
    Thing("drapes", "Beige curtains", "wall_art", "window", (1160, 0, 1695, 840), beige_curtains, on_wall=True, window="beside",
          color="beige", material="polyester", note="Heavy floor-to-ceiling panels in deep folds."),
]
