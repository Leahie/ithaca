"""Pictures for the models: cut straight out of the room photo where the thing is seen flat
enough (the map, the poster, the duvet, the rug), drawn where it is not (the sheet's stripes, the
plaid shirt, the bird curtains)."""

from __future__ import annotations

import math
import random
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

Quad = tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]


@lru_cache(maxsize=4)
def photo(path: str) -> Image.Image:
    return Image.open(Path(path)).convert("RGB")


def warp(image: Image.Image, quad: Quad, size: tuple[int, int]) -> Image.Image:
    """The part of `image` inside `quad` (top left, top right, bottom right, bottom left, in
    pixels), straightened out to a `size` rectangle: a picture seen at an angle, seen flat."""
    w, h = size
    src = np.array(quad, float)
    dst = np.array([(0, 0), (w, 0), (w, h), (0, h)], float)
    rows = []
    for (x, y), (u, v) in zip(dst, src):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    coeffs = np.linalg.solve(np.array(rows), src.reshape(8))
    return image.transform(size, Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)


def tile(image: Image.Image, across: int, down: int, mirror: bool = True) -> Image.Image:
    """The picture repeated; mirrored copies meet without a seam."""
    w, h = image.size
    out = Image.new(image.mode, (w * across, h * down))
    for i in range(across):
        for j in range(down):
            piece = image
            if mirror and i % 2:
                piece = piece.transpose(Image.FLIP_LEFT_RIGHT)
            if mirror and j % 2:
                piece = piece.transpose(Image.FLIP_TOP_BOTTOM)
            out.paste(piece, (i * w, j * h))
    return out


def stripes(size=(512, 512), base=(236, 228, 214), lines=((176, 96, 78), (205, 170, 150)), every=26, seed=3) -> Image.Image:
    """A ticking-striped sheet: thin warm stripes on cream, along its length."""
    img = Image.new("RGB", size, base)
    d = ImageDraw.Draw(img)
    rng = random.Random(seed)
    for k, x in enumerate(range(6, size[0], every)):
        d.line([(x, 0), (x, size[1])], fill=lines[k % len(lines)], width=2 if k % 2 == 0 else 1)
        d.line([(x + 5, 0), (x + 5, size[1])], fill=lines[1], width=1)
    for _ in range(40):  # wrinkles
        y = rng.randrange(size[1])
        d.line([(rng.randrange(size[0]), y), (rng.randrange(size[0]), y + rng.randint(-30, 30))], fill=(222, 212, 198), width=2)
    return img.filter(ImageFilter.GaussianBlur(0.6))


def plaid(size=(256, 256), base=(150, 22, 30), dark=(25, 18, 22), light=(210, 190, 190)) -> Image.Image:
    """A red and black flannel check."""
    img = Image.new("RGB", size, base)
    d = ImageDraw.Draw(img)
    for x in range(0, size[0], 64):
        d.rectangle([x, 0, x + 26, size[1]], fill=dark)
        d.line([(x + 44, 0), (x + 44, size[1])], fill=light, width=2)
    over = Image.new("RGB", size, base)
    o = ImageDraw.Draw(over)
    for y in range(0, size[1], 64):
        o.rectangle([0, y, size[0], y + 26], fill=dark)
        o.line([(0, y + 44), (size[0], y + 44)], fill=light, width=2)
    return Image.blend(img, over, 0.5)


def hearts(size=(256, 256), base=(246, 222, 120), heart=(214, 40, 48), extra=((80, 160, 70), (250, 140, 40))) -> Image.Image:
    """A pale yellow pillowcase with little red hearts."""
    img = Image.new("RGB", size, base)
    d = ImageDraw.Draw(img)
    rng = random.Random(7)
    for k in range(26):
        x, y, s = rng.randrange(size[0]), rng.randrange(size[1]), rng.randint(9, 16)
        colour = heart if k % 5 else extra[k % 2]
        d.ellipse([x - s, y - s // 2, x, y + s // 2], fill=colour)
        d.ellipse([x, y - s // 2, x + s, y + s // 2], fill=colour)
        d.polygon([(x - s, y), (x + s, y), (x, y + s + 2)], fill=colour)
    return img


def bird_curtain(size=(512, 640), seed=11) -> Image.Image:
    """The sheer window curtain: white, black birds flying across it, a navy city skyline along
    the bottom (towers with windows, a pointed spire, a peaked roof)."""
    w, h = size
    img = Image.new("RGB", size, (238, 240, 242))
    d = ImageDraw.Draw(img)
    rng = random.Random(seed)
    for _ in range(26):  # birds: a shallow "m"
        x, y, s = rng.randrange(w), rng.randrange(int(h * 0.62)), rng.randint(6, 12)
        d.line([(x - s, y), (x - s // 3, y - s // 3), (x, y)], fill=(30, 34, 52), width=2)
        d.line([(x, y), (x + s // 3, y - s // 3), (x + s, y)], fill=(30, 34, 52), width=2)
    navy, white = (28, 44, 92), (238, 240, 242)
    base = int(h * 0.98)
    x = 0
    while x < w:
        bw = rng.randint(28, 58)
        bh = rng.randint(int(h * 0.1), int(h * 0.3))
        top = base - bh
        d.rectangle([x, top, x + bw, base], fill=navy)
        kind = rng.random()
        if kind < 0.3:  # a spire
            d.polygon([(x, top), (x + bw, top), (x + bw // 2, top - bh // 3)], fill=navy)
        elif kind < 0.55:  # a peaked roof
            d.polygon([(x - 3, top), (x + bw + 3, top), (x + bw // 2, top - 18)], fill=navy)
        for wy in range(top + 8, base - 8, 14):  # windows
            for wx in range(x + 6, x + bw - 8, 11):
                if rng.random() < 0.7:
                    d.rectangle([wx, wy, wx + 4, wy + 6], fill=white)
        x += bw + rng.randint(4, 18)
    d.rectangle([0, base, w, h], fill=navy)
    return img


def wall_dots(size=(512, 340), seed=5) -> Image.Image:
    """The stickers and washi-tape dots stuck around the map, on a see-through ground."""
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    rng = random.Random(seed)
    colours = [(214, 64, 52), (240, 170, 40), (60, 140, 80), (60, 110, 190), (250, 220, 90), (120, 70, 50), (230, 120, 160)]
    for _ in range(14):  # short rows of dots, as they are stuck on in the photo
        x, y = rng.randrange(20, size[0] - 80), rng.randrange(20, size[1] - 20)
        angle = rng.uniform(-0.8, 0.8)
        for k in range(rng.randint(3, 7)):
            cx, cy = x + math.cos(angle) * k * 11, y + math.sin(angle) * k * 11
            r = rng.randint(3, 5)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*rng.choice(colours), 255))
    for _ in range(4):  # a few bits of tape
        x, y = rng.randrange(size[0] - 40), rng.randrange(size[1] - 12)
        d.rectangle([x, y, x + rng.randint(18, 36), y + 7], fill=(236, 220, 180, 235))
    return img


def grille(size=(256, 360), label: str | None = "WINIX") -> Image.Image:
    """An air purifier's front: a field of small holes, a name in the corner."""
    img = Image.new("RGB", size, (240, 241, 242))
    d = ImageDraw.Draw(img)
    for y in range(40, size[1] - 16, 9):
        for x in range(16, size[0] - 14, 9):
            d.ellipse([x, y, x + 3, y + 3], fill=(150, 154, 160))
    if label:
        d.text((size[0] - 70, 14), label, fill=(70, 72, 78))
    return img


def mesh_back(size=(256, 256)) -> Image.Image:
    """The office chair's mesh: a fine dark weave."""
    img = Image.new("RGB", size, (38, 38, 40))
    d = ImageDraw.Draw(img)
    for k in range(0, size[0], 5):
        d.line([(k, 0), (k, size[1])], fill=(70, 70, 74))
        d.line([(0, k), (size[0], k)], fill=(58, 58, 62))
    return img


def pine(size=(512, 128), base=(196, 130, 66), seed=2) -> Image.Image:
    """Honey pine, the grain running along the board."""
    rng = np.random.default_rng(seed)
    w, h = size
    y = np.linspace(0, 1, h)[:, None]
    x = np.linspace(0, 1, w)[None, :]
    grain = np.sin((y * 38 + np.sin(x * 6 + rng.uniform(0, 6)) * 1.4) * math.pi) * 0.5 + 0.5
    noise = rng.normal(0, 0.04, (h, w))
    shade = 0.86 + 0.12 * grain + noise
    arr = np.clip(np.array(base)[None, None, :] * shade[..., None], 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def taped_box(size=(256, 256)) -> Image.Image:
    """The top of the black storage box: duct tape down the middle, a white label in a corner."""
    img = Image.new("RGB", size, (34, 32, 31))
    d = ImageDraw.Draw(img)
    d.rectangle([size[0] * 0.42, 0, size[0] * 0.58, size[1]], fill=(86, 86, 88))
    d.rectangle([size[0] * 0.08, size[1] * 0.06, size[0] * 0.3, size[1] * 0.2], fill=(236, 236, 232))
    for k in range(4):
        d.line([(size[0] * 0.1, size[1] * (0.09 + k * 0.025)), (size[0] * 0.27, size[1] * (0.09 + k * 0.025))], fill=(120, 120, 120))
    return img


def book_spines(size=(256, 128), seed=4) -> Image.Image:
    """A row of notebooks and books on their side, seen from the front."""
    img = Image.new("RGB", size, (40, 40, 40))
    d = ImageDraw.Draw(img)
    rng = random.Random(seed)
    colours = [(30, 60, 120), (200, 60, 50), (40, 140, 90), (230, 200, 70), (70, 70, 75), (230, 230, 225), (120, 70, 140)]
    y = size[1]
    while y > 0:
        t = rng.randint(6, 16)
        d.rectangle([0, y - t, size[0] - rng.randint(0, 40), y], fill=rng.choice(colours))
        y -= t + 1
    return img
