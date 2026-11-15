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
    """A piece in storage (docs/specs/furniture.md)."""

    item_id: UUID
    # The owner (F1).
    user_id: str
    # The owner's name in the family, filled in when the piece is listed.
    owner_name: str | None = None
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
    # The piece's own short number ("4821"): random, unique among all stored pieces, and never
    # reused. People and the guides name a piece by it; item_id stays the key in the database.
    tag: str | None = None
    # How many times its model's own size it is drawn (resized by hand in the editor). dims_m is
    # already the resized size; the GLB is drawn at this scale.
    scale: float = 1.0


class ResizeItem(Model):
    """Body of PUT /properties/{property_id}/furniture/{item_id}/scale: draw a piece at this many times its model's size."""

    scale: float


class CatalogPiece(Model):
    """A ready-made piece the user can add to their inventory without a photo."""

    catalog_id: str
    label: str
    item_class: str
    dims_m: ItemDims  # the model's own size; the user may override it when adding
    thumbnail_url: str
    source: str  # where the model comes from and its license
    # The look it belongs to ("Mid-century modern", "Farmhouse"), and the names of the sets it is in.
    style: str | None = None
    sets: list[str] = []


class CatalogSet(Model):
    """A room's worth of catalog pieces that were designed to go together."""

    set_id: str
    name: str
    style: str | None = None
    rooms: str  # the kinds of room it is for, in words
    look: str  # one sentence on what it looks like
    piece_ids: list[str]  # catalog_ids, the ones that make the room first
    thumbnail_urls: list[str]  # pictures of its first pieces, for a card


class CatalogAddRequest(Model):
    """Body of POST /catalog/{catalog_id}/add. Give all three sizes to resize the piece, or none."""

    label: str | None = None
    width_m: float | None = None
    depth_m: float | None = None
    height_m: float | None = None
