"""The shared furniture class list, with a typical size per class (w, d, h in meters).

Sizes are used when a photo gives no dimensions: the generated model is scaled so its footprint
matches the typical one. Stream C keys its sound-absorption table on the same class names.
"""

from __future__ import annotations

TYPICAL_SIZE_M: dict[str, tuple[float, float, float]] = {
    "sofa": (2.10, 0.92, 0.85),
    "loveseat": (1.50, 0.90, 0.85),
    "sectional": (2.80, 1.70, 0.85),
    "armchair": (0.85, 0.85, 0.90),
    "chair": (0.50, 0.55, 0.90),
    "stool": (0.40, 0.40, 0.65),
    "ottoman": (0.70, 0.55, 0.42),
    "bench": (1.20, 0.40, 0.45),
    "coffee_table": (1.10, 0.60, 0.45),
    "side_table": (0.50, 0.50, 0.55),
    "dining_table": (1.80, 0.90, 0.75),
    "desk": (1.40, 0.70, 0.75),
    "console_table": (1.20, 0.35, 0.80),
    "tv_stand": (1.60, 0.45, 0.50),
    "bookshelf": (0.90, 0.35, 1.90),
    "cabinet": (1.00, 0.45, 0.90),
    "dresser": (1.20, 0.50, 0.90),
    "wardrobe": (1.20, 0.60, 2.00),
    "nightstand": (0.50, 0.40, 0.55),
    "bed": (1.60, 2.05, 1.10),
    "crib": (0.75, 1.35, 0.95),
    "floor_lamp": (0.40, 0.40, 1.60),
    "plant": (0.50, 0.50, 1.20),
    "rug": (2.40, 1.70, 0.02),
    "other": (0.80, 0.60, 0.80),
}

FURNITURE_CLASSES = tuple(TYPICAL_SIZE_M)
