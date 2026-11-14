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


class CatalogPiece(Model):
    """A ready-made piece the user can add to their inventory without a photo."""

    catalog_id: str
    label: str
    item_class: str
    dims_m: ItemDims  # the model's own size; the user may override it when adding
    thumbnail_url: str
    source: str  # where the model comes from and its license


class CatalogAddRequest(Model):
    """Body of POST /catalog/{catalog_id}/add. Give all three sizes to resize the piece, or none."""

    label: str | None = None
    width_m: float | None = None
    depth_m: float | None = None
    height_m: float | None = None
