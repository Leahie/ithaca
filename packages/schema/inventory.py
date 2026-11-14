"""Inventory types. Owner: B. Consumer: A."""
from __future__ import annotations

from uuid import UUID

from .common import Model


class ItemAttributes(Model):
    style: str | None = None
    color: str | None = None
    material: str | None = None


class ItemDims(Model):
    """Item space: w along X, d along Z, h along Y. Meters."""

    w: float
    d: float
    h: float


class InventoryItem(Model):
    item_id: UUID
    user_id: str
    label: str
    # From the shared furniture class list.
    item_class: str
    attributes: ItemAttributes
    dims_m: ItemDims
    glb_url: str
    source_image_url: str
    mask_url: str | None = None
    price: float | None = None
    product_url: str | None = None
