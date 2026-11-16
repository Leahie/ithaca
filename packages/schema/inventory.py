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


class LibraryPiece(Model):
    """One piece of the furniture library: the large open 3D model collections behind the
    furniture menu (services/api/library). Anyone can add one; its model is fetched on first use."""

    library_id: str  # "abo:B07B4MG9HX", "ph:ArmChair_01"
    label: str
    item_class: str  # from the shared furniture class list
    category: str  # the section of the menu it is listed under (LibraryFacet.key)
    dims_m: ItemDims  # real size
    thumbnail_url: str
    source_key: str  # which collection: "abo" | "polyhaven" | "objaverse"
    source: str  # the credit line to show: "Amazon Berkeley Objects · CC BY 4.0"
    brand: str | None = None
    styles: list[str] = []  # the looks it suits, the strongest first ("Glam", "Mid-century modern")
    color: str | None = None  # the colour's name, as the maker gives it
    palette: list[str] = []  # its main colours as hex ("#8a8d8f"), the largest first
    variants: int = 1  # how many colours and sizes of this same design the library has
    family: str | None = None  # those share this key: GET /library?family=<key> lists them


class LibraryFacet(Model):
    """A way to narrow the library (a section of the menu, or a collection), with how many pieces
    of the current search fall under it."""

    key: str
    label: str
    count: int


class LibraryPage(Model):
    """One page of GET /library."""

    total: int  # pieces that match the search, the section and the collection asked for
    total_all: int  # pieces in the whole library
    items: list[LibraryPiece]
    categories: list[LibraryFacet]  # every section in menu order, counted for this search and collection
    sources: list[LibraryFacet]  # every collection, counted for this search and section


class LibraryLook(Model):
    """A look a room can be furnished in: a designer's recipe of styles, colours and materials."""

    key: str  # "quiet_luxury"
    name: str  # "Quiet luxury"
    blurb: str  # one sentence on what it looks like
    palette: list[str] = []  # hex colours that carry it
    thumbnail_urls: list[str] = []  # a few pieces that show it


class CatalogAddRequest(Model):
    """Body of POST /catalog/{catalog_id}/add. Give all three sizes to resize the piece, or none."""

    label: str | None = None
    width_m: float | None = None
    depth_m: float | None = None
    height_m: float | None = None


class ScanThing(Model):
    """One thing in a scanned photo of someone's own room (services/api/furniture/scans)."""

    key: str
    label: str
    item_class: str  # from the shared furniture class list
    group: str  # furniture, floor, bed, desk, shelf, wall, window
    dims_m: ItemDims
    box: tuple[float, float, float, float]  # where it is in the photo: left, top, right, bottom, as fractions of its size
    rests_on: str | None = None  # the key of the piece it stands on
    on_wall: bool = False
    note: str = ""
    model_url: str  # its 3D model (GLB), to look at before it is added


class RoomScan(Model):
    """A photo of someone's own room, with everything in it listed and modelled in 3D."""

    scan_id: str
    name: str
    blurb: str
    photo_url: str
    width: int  # the photo's size in pixels
    height: int
    finishes: list[str] = []  # the room itself: floor, walls, trim
    things: list[ScanThing]


class FurnishFromScanRequest(Model):
    """Body of POST /properties/{property_id}/furniture/from-scan: set up a room of this house with
    a scanned room. `keys`: only these things (none: everything). What stood there goes to storage."""

    scan_id: str
    room_id: str
    keys: list[str] | None = None


class ScanThingPlaced(Model):
    """Reply to putting one scanned thing in the house, or in storage."""

    item: InventoryItem
    room_id: str | None = None
