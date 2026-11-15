"""A piece's own colours, from the pixels of its picture: up to three hex colours, largest first,
and one word for the whole (its tone).

The hard part is knowing which pixels are the piece. Three kinds of picture turn up:

    studio   the piece alone on a plain background (most product photos, every cut-out):
             the background is whatever is the colour of the border and joined to it
    flat     the piece fills the frame (a rug's surface, a print): every pixel counts
    room     the piece stands in a furnished room (many rugs, some cabinets and beds): only the
             middle is trusted, and the colour the maker names decides between what is there

Colours are compared in CIELAB, where equal distances look about equally different.
"""

from __future__ import annotations

import numpy as np

# The tone words, with a swatch each (what the word means when a title says it, and what a
# colour chip shows). The room composer (library/compose.py) matches a piece's tone against
# exactly these words, so none may be added or respelt here alone.
TONES: dict[str, str] = {
    "white": "#f4f3ef", "cream": "#ece2cc", "beige": "#d3c2a5", "tan": "#b8935f", "brown": "#6b4630",
    "black": "#1c1b1b", "charcoal": "#45464a", "grey": "#9a9b9d", "gold": "#c2a14d", "blush": "#e3bfb6",
    "pink": "#d9788f", "red": "#b3262b", "burgundy": "#5f1f2a", "terracotta": "#b5603c", "orange": "#e0832f",
    "mustard": "#c99a1d", "yellow": "#ecd04c", "olive": "#6f6a35", "green": "#4f8a4a",
    "emerald": "#1f6b53", "teal": "#2a7f86", "blue": "#4a79b3", "navy": "#232f4b", "purple": "#6c4a86",
    "multicolour": "#888888",
}

# A colour word in a title or in the maker's colour name -> the tone it means.
TONE_WORDS: dict[str, str] = {
    "white": "white", "snow": "white", "chalk": "white", "ivory": "cream", "cream": "cream", "ecru": "cream", "off-white": "cream",
    "pearl": "cream", "bone": "cream", "alabaster": "cream", "eggshell": "cream",
    "beige": "beige", "oatmeal": "beige", "sand": "beige", "linen": "beige", "natural": "beige", "flax": "beige", "stone": "beige",
    "fawn": "beige", "wheat": "beige", "taupe": "beige", "greige": "beige", "khaki": "beige", "driftwood": "beige",
    "tan": "tan", "camel": "tan", "caramel": "tan", "cognac": "tan", "saddle": "tan", "honey": "tan", "toffee": "tan", "oak": "tan",
    "brown": "brown", "chocolate": "brown", "espresso": "brown", "walnut": "brown", "chestnut": "brown", "mocha": "brown",
    "coffee": "brown", "mahogany": "brown", "cherry": "brown", "java": "brown", "bark": "brown", "teak": "brown",
    "black": "black", "onyx": "black", "ebony": "black", "charcoal": "charcoal", "graphite": "charcoal", "slate": "charcoal",
    "iron": "charcoal", "pewter": "charcoal", "smoke": "charcoal", "anthracite": "charcoal",
    "grey": "grey", "gray": "grey", "silver": "grey", "pebble": "grey", "ash": "grey", "fog": "grey", "dove": "grey", "steel": "grey",
    "gold": "gold", "brass": "gold", "golden": "gold", "blush": "blush", "rose": "blush", "pink": "pink", "coral": "pink",
    "fuchsia": "pink", "magenta": "pink", "red": "red", "crimson": "red", "scarlet": "red", "burgundy": "burgundy", "wine": "burgundy",
    "merlot": "burgundy", "maroon": "burgundy", "oxblood": "burgundy", "terracotta": "terracotta", "rust": "terracotta",
    "copper": "terracotta", "sienna": "terracotta", "paprika": "terracotta", "orange": "orange", "tangerine": "orange",
    "mustard": "mustard", "ochre": "mustard", "yellow": "yellow", "lemon": "yellow", "sunflower": "yellow",
    "olive": "olive", "moss": "olive", "sage": "green", "mint": "green", "green": "green", "emerald": "emerald", "forest": "emerald", "hunter": "emerald",
    "teal": "teal", "turquoise": "teal", "aqua": "teal", "peacock": "teal", "blue": "blue", "denim": "blue", "cobalt": "blue",
    "sky": "blue", "sapphire": "blue", "azure": "blue", "navy": "navy", "indigo": "navy", "midnight": "navy", "ink": "navy",
    "purple": "purple", "plum": "purple", "lavender": "purple", "violet": "purple", "lilac": "purple", "eggplant": "purple",
    "multicolor": "multicolour", "multicolour": "multicolour", "multi": "multicolour", "rainbow": "multicolour",
}


def to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (0..255, any shape ending in 3) to CIELAB under D65."""
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    xyz = c @ np.array([[0.4124, 0.2126, 0.0193], [0.3576, 0.7152, 0.1192], [0.1805, 0.0722, 0.9505]])
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def to_rgb(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], axis=-1)
    xyz = np.where(f > 0.206893, f ** 3, (f - 16 / 116) / 7.787) * np.array([0.95047, 1.0, 1.08883])
    c = xyz @ np.array([[3.2406, -0.9689, 0.0557], [-1.5372, 1.8758, -0.2040], [-0.4986, 0.0415, 1.0570]])
    c = np.where(c > 0.0031308, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055, 12.92 * c)
    return np.clip(np.rint(c * 255), 0, 255).astype(np.uint8)


def hex_of(rgb) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


def lab_of_hex(text: str) -> np.ndarray:
    return to_lab(np.array([int(text[i:i + 2], 16) for i in (1, 3, 5)]))


def tone_of(lab: np.ndarray) -> str:
    """The word for one colour. Lightness, how strong the colour is, then its hue: greys first,
    then the warm neutrals wood, leather and linen come in, then the colours proper."""
    light, a, b = float(lab[0]), float(lab[1]), float(lab[2])
    chroma = float(np.hypot(a, b))
    hue = float(np.degrees(np.arctan2(b, a))) % 360
    warm = 45 <= hue <= 105  # between orange and yellow: where wood, leather and linen are
    if chroma < 5 or (chroma < 9 and not warm):
        return "white" if light > (82 if chroma < 3.5 else 86) else "grey" if light > 47 else "charcoal" if light > 21 else "black"
    if light < 15:
        return "black"
    if warm and chroma < 38 and not (hue > 82 and chroma > 30) and not (hue > 92 and chroma > 18 and light < 62):
        if light > 86:
            return "cream" if chroma >= 6 else "white"
        if chroma < 11 and light < 68:
            return "grey" if light > 47 else "charcoal" if light > 24 else "black"
        if light > 70:
            return "cream" if light > 82 and chroma < 22 else "beige" if chroma < 24 else "tan"
        if light > 50:
            return "beige" if chroma < 14 else "tan"
        return "brown"
    if hue < 12 or hue >= 335:  # pinks and cool reds
        return "blush" if light > 68 and chroma < 32 else "burgundy" if light < 33 else "pink" if light > 48 else "red" if chroma > 38 else "burgundy"
    if hue < 45 and chroma < 24:  # dusty pinks and the red-browns of cherry and mahogany
        return "blush" if light > 62 or (light > 46 and hue < 32) else "tan" if light > 46 else "brown"
    if hue < 35:  # reds
        if light > 70:
            return "blush"
        return "burgundy" if light < 30 or (light < 40 and chroma < 40) else "red" if chroma > 45 else "terracotta" if light > 38 else "burgundy"
    if hue < 62:  # orange side
        if light > 74:
            return "blush" if hue < 50 else "orange" if chroma > 50 else "tan"
        if light < 32:
            return "brown"
        return "orange" if chroma > 58 and light > 55 else "terracotta" if hue < 55 or chroma > 45 else "tan"
    if hue < 80:
        return "brown" if light < 40 else "orange" if chroma > 62 and hue < 70 else "mustard" if chroma > 48 and hue > 68 else "tan"
    if hue < 105:  # yellows
        if light < 46 or (light < 56 and hue > 92):
            return "olive"
        return "yellow" if light > 76 and chroma > 45 else "mustard" if chroma > 50 else "gold" if chroma > 28 else "olive" if light < 60 else "beige"
    if hue < 128:  # yellow-greens; the pale ones (sage) are green, the dull dark ones olive
        return "green" if light > 62 and chroma < 24 else "olive" if chroma < 42 or light < 50 else "green"
    if hue < 175:  # greens
        if chroma < 20:
            return "green" if light > 55 else "olive" if hue < 140 else "emerald"
        return "emerald" if light < 45 and hue > 140 else "green"
    if hue < 235:  # blue-greens
        if chroma < 12:
            return "green" if light > 60 and hue < 195 else "grey" if light > 47 else "charcoal"
        return "emerald" if hue < 185 and light < 42 else "teal"
    if hue < 300:  # blues
        if light < 30:
            return "navy"
        return "navy" if light < 38 and chroma < 30 else "blue"
    return "purple" if light < 72 or chroma > 22 else "blush"  # 300..335


def _family(tone: str) -> str:
    """Tones that are one colour in different lights. A room of them is not "multicolour"."""
    for family in (("white", "cream", "beige", "grey", "charcoal", "black"), ("tan", "brown", "gold", "mustard", "terracotta", "orange"),
                   ("blush", "pink", "red", "burgundy"), ("olive", "green", "emerald"), ("teal", "blue", "navy"), ("purple",), ("yellow",)):
        if tone in family:
            return family[0]
    return tone


def _edge(values: np.ndarray, width: int = 3) -> np.ndarray:
    """The pixels of a picture's border, `width` deep, as one array."""
    return np.concatenate([values[:width].reshape(-1, *values.shape[2:]), values[-width:].reshape(-1, *values.shape[2:]),
                           values[:, :width].reshape(-1, *values.shape[2:]), values[:, -width:].reshape(-1, *values.shape[2:])])


def border_plain(rgb: np.ndarray, near: int = 14) -> tuple[float, np.ndarray]:
    """(how much of the picture's border is one plain colour, that colour). Close to 1 for a
    studio photo on white, low for a room or a pattern that runs off the edge."""
    edge = _edge(rgb).astype(int)
    colour = np.median(edge, axis=0)
    return float((np.abs(edge - colour).max(axis=1) <= near).mean()), colour


def _even(rgb: np.ndarray, step: int = 7) -> np.ndarray:
    """Where a picture changes by no more than `step` from one pixel to the next: a backdrop
    lit from one side changes a lot from corner to corner and never from pixel to pixel."""
    values = rgb.astype(int)
    change = np.zeros(values.shape[:2], dtype=int)
    across = np.abs(values[:, 1:] - values[:, :-1]).max(axis=2)
    down = np.abs(values[1:] - values[:-1]).max(axis=2)
    change[:, 1:] = np.maximum(change[:, 1:], across)
    change[:, :-1] = np.maximum(change[:, :-1], across)
    change[1:] = np.maximum(change[1:], down)
    change[:-1] = np.maximum(change[:-1], down)
    return change <= step


def border_even(rgb: np.ndarray) -> float:
    """How much of the picture's border is even backdrop, plain or shaded: close to 1 for a
    render on a grey gradient (every Sketchfab thumbnail), as for a photo on white."""
    return float(_edge(_even(rgb)).mean())


def piece_mask(picture: np.ndarray, kind: str, generous: bool = False) -> np.ndarray:
    """Which pixels are the piece. `picture` is (H, W, 3 or 4) uint8; `kind` as in the module text.
    `generous` keeps the piece's outline (and the shadow under it) rather than lose a thin edge:
    right for cutting the piece out, wrong for counting its colours."""
    from scipy import ndimage

    height, width = picture.shape[:2]
    if picture.shape[2] == 4 and (picture[..., 3] < 128).mean() > 0.02:
        return picture[..., 3] >= 128
    rgb = picture[..., :3].astype(int)
    if kind == "flat":
        return np.ones((height, width), bool)
    if kind == "room":
        # The middle of the picture, a little below centre: where a photographer puts the thing sold.
        ys, xs = np.mgrid[0:height, 0:width]
        return ((xs - width / 2) / (0.30 * width)) ** 2 + ((ys - 0.56 * height) / (0.30 * height)) ** 2 <= 1
    _, colour = border_plain(picture[..., :3])
    near = np.abs(rgb - colour).max(axis=2)
    labels, _ = ndimage.label(near <= 14)
    edge = np.unique(_edge(labels, 1))
    background = np.isin(labels, edge[edge > 0])
    # A shaded backdrop is not one colour, but it is even, and it runs along the border. An even
    # stretch that only touches the border here and there is a piece cut off by the frame.
    labels, _ = ndimage.label(_even(rgb))
    ring = _edge(labels, 1)
    for label, count in zip(*np.unique(ring[ring > 0], return_counts=True)):
        if count >= 0.15 * len(ring):
            background |= labels == label
    # Holes the border does not reach (between the legs of a table, through a lamp's frame) are
    # background too when they are exactly its colour: a white piece is never as flat as the backdrop.
    background |= near <= 4
    mask = ~background
    if generous:
        return ndimage.binary_dilation(mask, iterations=1)
    # The soft shadow under a piece is a thin grey film next to the background: one step of erosion drops it.
    eroded = ndimage.binary_erosion(mask, iterations=1)
    return eroded if eroded.mean() > 0.02 else mask


def on_white(picture: np.ndarray) -> np.ndarray:
    """A studio picture with its backdrop made white, (H, W, 3). A render on a dark or shaded
    ground is otherwise "dark" and "moody" to the model whatever the piece is, and every piece
    of one collection looks alike for sharing a backdrop."""
    out = picture[..., :3].copy()
    out[~piece_mask(picture, "studio", generous=True)] = 255
    return out


def _clusters(lab: np.ndarray, weight: np.ndarray, k: int = 6, rounds: int = 12) -> tuple[np.ndarray, np.ndarray]:
    """Weighted k-means in CIELAB, started from the pixels spread furthest apart so a small
    strong colour gets a centre of its own. Returns (centres, share of the weight each holds)."""
    centres = [np.average(lab, axis=0, weights=weight)]
    for _ in range(k - 1):
        far = np.min([((lab - c) ** 2).sum(axis=1) for c in centres], axis=0)
        centres.append(lab[int(np.argmax(far * (weight > 0)))])
    centres = np.array(centres)
    for _ in range(rounds):
        nearest = ((lab[:, None, :] - centres[None]) ** 2).sum(axis=2).argmin(axis=1)
        for i in range(k):
            members = nearest == i
            if weight[members].sum() > 0:
                centres[i] = np.average(lab[members], axis=0, weights=weight[members])
    share = np.array([weight[nearest == i].sum() for i in range(k)]) / weight.sum()
    return centres, share


# Tones a maker's colour word may also mean: "blue" for what the pixels call navy.
_NEAR: dict[str, tuple[str, ...]] = {
    "white": ("cream",), "cream": ("white", "beige"), "beige": ("cream", "tan", "grey"), "tan": ("beige", "brown", "gold", "terracotta"),
    "brown": ("tan", "burgundy", "terracotta"), "black": ("charcoal",), "charcoal": ("black", "grey"), "grey": ("charcoal",),
    "gold": ("tan", "mustard", "yellow"), "blush": ("pink", "beige"), "pink": ("blush", "red"), "red": ("burgundy", "pink", "terracotta"),
    "burgundy": ("red", "brown"), "terracotta": ("orange", "brown", "red", "tan"), "orange": ("terracotta", "mustard"),
    "mustard": ("gold", "yellow", "orange"), "yellow": ("mustard", "gold"), "olive": ("green",), "green": ("olive", "emerald", "teal"),
    "emerald": ("green", "teal"), "teal": ("emerald", "blue", "green"), "blue": ("navy", "teal"), "navy": ("blue",), "purple": ("pink", "navy"),
}
_GREYS = ("white", "grey", "charcoal", "black")
_EXACT = frozenset(TONES) - {"white", "black", "grey", "brown", "red", "green", "blue", "pink", "yellow", "orange", "purple", "multicolour"}


def palette(picture: np.ndarray, kind: str = "studio", named: str | None = None, pale_last: bool = False) -> tuple[list[str], str]:
    """(up to three hex colours, largest first; the tone word) for the piece in `picture`.

    `named` is the tone the maker's words give ("navy"), if any. The pixels say what colours
    there are; the word says which of them the piece is known by (a "black" lamp is mostly white
    shade), and what a dull dark colour is (a "denim" sofa that photographs near charcoal).
    `pale_last` is for pieces whose largest area is not what they are known by either: a lamp's
    shade, a bed's linen. Their white comes after any other colour of some size."""
    mask = piece_mask(picture, kind)
    pixels = picture[..., :3][mask]
    if len(pixels) < 40:  # a white piece on white: nothing left to count
        pixels = picture[..., :3].reshape(-1, 3)
    if len(pixels) > 6000:
        pixels = pixels[np.linspace(0, len(pixels) - 1, 6000).astype(int)]
    lab = to_lab(pixels)
    centres, share = _clusters(lab, np.ones(len(lab)))
    # Join centres that look the same, then order by how much of the piece each covers.
    groups: list[list[int]] = []
    for i in np.argsort(-share):
        if share[i] <= 0:
            continue
        for group in groups:
            if np.linalg.norm(centres[group[0]] - centres[i]) < 13:
                group.append(int(i))
                break
        else:
            groups.append([int(i)])
    merged = [(float(share[g].sum()), np.average(centres[g], axis=0, weights=share[g])) for g in groups]
    merged.sort(key=lambda item: -item[0])
    tones = [tone_of(colour) for _, colour in merged]
    wanted = (named, *_NEAR.get(named, ())) if named in TONES and named != "multicolour" else ()
    for i, (_, colour) in enumerate(merged):
        if wanted and named not in _GREYS and tones[i] in ("grey", "charcoal", "black"):
            # A dull dark colour reads as grey or black. If the maker names a colour and the hue
            # agrees once the colour is turned up, it is that colour.
            vivid = tone_of(np.array([max(colour[0], 22.0), 2.5 * colour[1], 2.5 * colour[2]]))
            if vivid in wanted:
                tones[i] = vivid
        elif named in ("white", "cream") and tones[i] in ("grey", "beige") and colour[0] > 70 and np.hypot(colour[1], colour[2]) < 10:
            # A white piece is light grey wherever the studio light does not fall straight on it.
            tones[i] = named

    def to_front(i: int) -> None:
        merged.insert(0, merged.pop(i))
        tones.insert(0, tones.pop(i))

    if pale_last and tones[0] in ("white", "cream"):
        other = next((i for i, (part, _) in enumerate(merged) if tones[i] not in ("white", "cream") and part >= 0.12), None)
        if other:
            to_front(other)
    if wanted:
        for i, (part, colour) in enumerate(merged):
            if (tones[i] in wanted or np.linalg.norm(colour - lab_of_hex(TONES[named])) < 20) and part >= 0.08:
                if i:
                    to_front(i)
                break
    keep = [i for i, (part, _) in enumerate(merged) if i == 0 or part >= 0.10][:3]
    colours = [hex_of(to_rgb(merged[i][1])) for i in keep]
    # Many colours of different families and none in charge: a patterned rug, a painting.
    strong = [(merged[i][0], tones[i]) for i in keep]
    families = {_family(tone) for _, tone in strong}
    chromatic = [tone for _, tone in strong if _family(tone) not in ("white",)]
    if len(families) >= 3 and len(chromatic) >= 2 and strong[0][0] < 0.5 and kind != "room":
        return colours, "multicolour"
    if named == "multicolour" and len(families) >= 2:
        return colours, "multicolour"
    # Between two words for much the same colour the maker's is the better one when it is the
    # exact one: "emerald" for what the pixels call green, "tan" for a cognac leather they call brown.
    if named in _EXACT and tones[0] in _NEAR[named]:
        return colours, named
    return colours, tones[0]
