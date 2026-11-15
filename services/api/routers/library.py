"""The furniture library: the menu's search, adding a piece, and furnishing a room in a look
(services/api/library). Owner: B."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Header, HTTPException, Request

from packages.schema.events import make_event
from packages.schema.inventory import InventoryItem, ItemDims, LibraryFacet, LibraryLook, LibraryPage, LibraryPiece
from packages.schema.layout import FurnishRoomRequest
from packages.schema.session import JobRef
from services.api.agent import memory_files
from services.api.agent.runner import event_message
from services.api.furniture import furnish, house
from services.api.furniture.store import furniture_store
from services.api.library import SOURCES, compose, sources
from services.api.library.index import Piece, library
from services.api.objects.normalize import NormalizeError
from services.api.twin.service import TwinNotFound, get_twin

router = APIRouter(tags=["library"])


def _card(p: Piece) -> LibraryPiece:
    return LibraryPiece(
        library_id=p.id, label=p.name, item_class=p.cls, category=p.cat, dims_m=ItemDims(w=p.w, d=p.d, h=p.h), thumbnail_url=p.img,
        source_key=p.src, source=SOURCES.get(p.src, (p.src, p.src))[1], brand=p.brand, styles=list(p.styles), color=p.color or p.tone,
        palette=list(p.palette), variants=library().family_size[p.fam], family=p.fam,
    )


@router.get("/library")
def list_library(
    q: str = "", category: str | None = None, source: str | None = None, family: str | None = None, offset: int = 0, limit: int = 60,
) -> LibraryPage:
    """The furniture menu: thousands of real 3D models from open collections. `q` is free words
    (a kind, a colour, a material, a look). The same design in several colours comes back once,
    with `variants`; `family` lists one design's colours and sizes. `categories` and `sources`
    count the current search, so the menu can show how much is under each."""
    page = library().search(q, category or None, source or None, family or None, max(0, offset), limit)
    return LibraryPage(
        total=page["total"], total_all=page["total_all"], items=[_card(p) for p in page["items"]],
        categories=[LibraryFacet(key=k, label=label, count=n) for k, label, n in page["categories"]],
        sources=[LibraryFacet(key=k, label=label, count=n) for k, label, n in page["sources"]],
    )


@lru_cache(maxsize=1)
def _looks() -> list[LibraryLook]:
    """The looks a room can be furnished in, each with four pieces that show it."""
    lib = library()
    out = []
    used: set[str] = set()  # no two looks show the same piece
    for look in compose.LOOKS:
        shown = []
        for classes in (("sofa",), ("armchair",), ("coffee_table", "side_table"), ("floor_lamp", "table_lamp")):
            found = lib.candidates(classes, phrases=look.phrases, tones=look.tones, materials=look.materials, not_cats=("outdoor", "kids"),
                                   anchors=[(p.id, 1.0) for p in shown], exclude_families=used, k=1)
            shown += [p for p, _ in found]
            used |= {p.fam for p, _ in found}
        out.append(LibraryLook(key=look.key, name=look.name, blurb=look.blurb, palette=list(look.palette), thumbnail_urls=[p.img for p in shown]))
    return out


@router.get("/library/looks")
def list_looks() -> list[LibraryLook]:
    return _looks()


@router.post("/library/{library_id}/add")
def add_library_piece(library_id: str, request: Request, x_user_id: str = Header("demo")) -> InventoryItem:
    """Put a library piece into the user's furniture. The first use of a piece downloads its model."""
    piece = library().by_id.get(library_id)
    if piece is None:
        raise HTTPException(404, "no such piece in the library")
    try:
        item = sources.add(piece, x_user_id, furniture_store())
    except NormalizeError as e:
        raise HTTPException(422, str(e)) from e
    except Exception as e:  # the model could not be downloaded
        raise HTTPException(503, f"could not load that model: {e}") from e
    memory_files.sync_inventory(request.app.state.memory_store, x_user_id, furniture_store().list_items(x_user_id))
    return item


@router.post("/properties/{property_id}/furniture/design")
def design_room(
    property_id: str, body: FurnishRoomRequest, request: Request, x_user_id: str = Header("demo"), x_person_id: str | None = Header(None)
) -> JobRef:
    """Have a room furnished from the library in a look: the pieces are chosen to go together,
    added to the person's furniture, and arranged, with rugs, lamps, pictures and plants in their
    places. Returns at once; follow `job.progress` (kind `furnish_room`). When it is done the
    options show on every screen of the house, to be kept or not, like any proposed layout."""
    person = x_person_id or x_user_id  # the new pieces are the person's; the house is the screen's
    try:
        twin = get_twin(property_id)
    except TwinNotFound as e:
        raise HTTPException(404, str(e)) from e
    if house.room_by_id(twin, body.room_id) is None:
        raise HTTPException(404, "no such room")
    memory, bridge = request.app.state.memory_store, request.app.state.bridge

    def work(progress):
        out = furnish.furnish(person, x_user_id, property_id, body.room_id, body.look, body.level, progress=progress)
        memory_files.sync_inventory(memory, person, furniture_store().list_items(person))
        if not out.layouts:
            raise RuntimeError(out.note or "this room could not be furnished")
        return out

    def done(out: furnish.Furnished) -> None:
        """Every screen of the house shows the options; the person's guide hears what was done."""
        from services.api.realtime.households import households

        ids = [str(l.layout_id) for l in out.layouts]
        for room in list(bridge.hub.rooms.values()):
            if room.user_id != x_user_id or room.property_id != property_id or not room.connections:
                continue
            for kind, payload in (("layout.changed", {}), ("design.showVariants", {"layout_ids": ids}), ("layout.select", {"layout_id": ids[0]})):
                bridge.hub.publish(make_event(room.session_id, "server", kind, payload))
            theirs = person if room.household_id and households().member(room.household_id, person) else None
            bridge._start(room, event_message("room_furnished", out.summary()), proactive=False, member_id=theirs)

    job_id = request.app.state.jobs.submit(
        "furnish_room", x_user_id, work, on_done=done, result_ref=lambda out: ",".join(str(l.layout_id) for l in out.layouts) or None,
    )
    return JobRef(job_id=job_id)
