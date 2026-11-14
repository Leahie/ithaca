"""Furniture from the command line.

    python -m services.api.objects.cli restyle --user demo
        Give the three sample pieces real-looking catalog models (same ids and sizes).

    python -m services.api.objects.cli add --user demo sofa bookshelf plant
        Add catalog pieces by class, at their typical size.

    python -m services.api.objects.cli import-glb chair.glb --user demo --label "My chair" --class armchair --dims 0.8x0.85x0.9
        Add a .glb you made elsewhere (e.g. on a TRELLIS demo site). --dims is width x depth x height in meters.

    python -m services.api.objects.cli segment room.mp4 --frames 8
        Cut the furniture out of a video or a photo of a room. Writes the frames, one cut-out and
        mask per piece, pieces.json and overview.jpg to data/files/segments/<file name>/.
        Needs an LLM key, and FAL_KEY or services/api/objects/requirements-segment.txt.

    python -m services.api.objects.cli room room.mp4 --user demo
        The whole way: a video (or photo) of a room -> sized 3D models in the user's inventory.
        The same job the app runs for an uploaded video. Needs an LLM key and FAL_KEY.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import time
from pathlib import Path

from PIL import Image, ImageDraw

from services.api.furniture.store import furniture_store
from services.api.objects import files, pipeline
from services.api.objects.classes import FURNITURE_CLASSES, TYPICAL_SIZE_M
from services.api.objects.normalize import normalize_glb
from services.api.objects.providers import catalog

COLORS = [(230, 60, 60), (50, 130, 240), (40, 180, 90), (240, 160, 30), (160, 80, 220), (20, 180, 190), (230, 90, 170), (130, 130, 40)]


def room_frames(source: Path, count: int) -> list[bytes]:
    """JPEG stills of the room: the sharpest frames of a video, or the one photo."""
    from services.api.objects import video

    if source.suffix.lower() in video.VIDEO_SUFFIXES:
        return video.sharp_frames(source, count)
    jpeg = io.BytesIO()
    Image.open(source).convert("RGB").save(jpeg, "JPEG", quality=95)
    return [jpeg.getvalue()]


def add_room(source: Path, count: int, user: str) -> None:
    started = time.monotonic()
    frames = room_frames(source, count)
    print(f"{len(frames)} frame(s) from {source.name}")
    store = furniture_store()
    result = pipeline.ingest_frames(
        frames, pipeline.save_frames(frames, user), user, store, pipeline.default_providers(), lambda pct, note: print(f"  {pct:3.0f}%  {time.monotonic() - started:5.1f} s  {note}")
    )
    for item in result.items:
        print(f"added {item.label} ({item.item_class}): {item.dims_m.w} × {item.dims_m.d} × {item.dims_m.h} m, {item.glb_url}, item_id {item.item_id}")
    for reason in result.skipped:
        print(f"skipped {reason}")
    print(f"{time.monotonic() - started:.0f} s in all")


def segment_room(source: Path, count: int, out: Path | None) -> None:
    from services.api.objects.providers.vision import LlmDescriber

    frames = room_frames(source, count)
    out = out or files.root() / "segments" / source.stem
    out.mkdir(parents=True, exist_ok=True)
    for pattern in ("frame_[0-9][0-9].jpg", "[0-9][0-9]_*.png", "pieces.json", "overview.jpg"):  # an earlier run's output
        for old in out.glob(pattern):
            old.unlink()
    for n, frame in enumerate(frames):
        (out / f"frame_{n:02d}.jpg").write_bytes(frame)
    print(f"{len(frames)} frame(s) from {source.name}")

    if pipeline.segmenter_name() == "fal":
        segmenter = pipeline.default_segmenter()
    else:
        from services.api.objects.providers.local import LocalSegmenter

        segmenter = LocalSegmenter()
    cuts, skipped = pipeline.cut_from_frames(frames, LlmDescriber(), segmenter, lambda pct, note: print(f"  {pct:3.0f}%  {note}"))

    tinted = {n: Image.open(io.BytesIO(frame)).convert("RGB") for n, frame in enumerate(frames)}
    listing = []
    for n, cut in enumerate(cuts):
        name = f"{n:02d}_{re.sub(r'[^a-z0-9]+', '_', cut.piece.label.lower()).strip('_')}"
        (out / f"{name}.png").write_bytes(cut.segment.cutout_png)
        (out / f"{name}.mask.png").write_bytes(cut.segment.mask_png)
        listing.append({**cut.piece.model_dump(), "cutout": f"{name}.png", "mask": f"{name}.mask.png", "frame_file": f"frame_{cut.piece.frame:02d}.jpg"})
        color, mask = COLORS[n % len(COLORS)], Image.open(io.BytesIO(cut.segment.mask_png))
        frame = tinted[cut.piece.frame]
        frame.paste(Image.blend(frame, Image.new("RGB", frame.size, color), 0.5), mask=mask)
        left, top, _, _ = mask.getbbox()
        draw = ImageDraw.Draw(frame)
        draw.rectangle(draw.textbbox((left + 4, top + 4), cut.piece.label), fill=color)
        draw.text((left + 4, top + 4), cut.piece.label, fill="white")
        print(f"cut out {cut.piece.label} ({cut.piece.item_class}) from frame {cut.piece.frame}: {name}.png")
    for reason in skipped:
        print(f"skipped {reason}")
    (out / "pieces.json").write_text(json.dumps({"source": source.name, "pieces": listing, "skipped": skipped}, indent=2))
    used = sorted({cut.piece.frame for cut in cuts})
    if used:
        sheet = Image.new("RGB", (sum(tinted[n].width for n in used), max(tinted[n].height for n in used)), "white")
        x = 0
        for n in used:
            sheet.paste(tinted[n], (x, 0))
            x += tinted[n].width
        sheet.save(out / "overview.jpg", quality=90)
    print(f"wrote {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("segment", "room"):
        p = sub.add_parser(name)
        p.add_argument("file", type=Path)
        p.add_argument("--frames", type=int, default=8, help="how many frames to take from a video")
        if name == "segment":
            p.add_argument("--out", type=Path, help="output folder (default data/files/segments/<file name>)")
        else:
            p.add_argument("--user", default="demo")
    for name in ("restyle", "add", "import-glb"):
        p = sub.add_parser(name)
        p.add_argument("--user", default="demo")
        if name == "add":
            p.add_argument("classes", nargs="+", choices=sorted(catalog.MODELS))
        if name == "import-glb":
            p.add_argument("file", type=Path)
            p.add_argument("--label", required=True)
            p.add_argument("--class", dest="item_class", default="other", choices=FURNITURE_CLASSES)
            p.add_argument("--dims", help="WxDxH in meters, e.g. 2.1x0.9x0.85")
    args = parser.parse_args()
    if args.cmd == "segment":
        return segment_room(args.file, args.frames, args.out)
    if args.cmd == "room":
        add_room(args.file, args.frames, args.user)
        print("If the API is running, open /me or ask the guide; inventory.md updates on the next request.")
        return
    store = furniture_store()

    if args.cmd == "restyle":
        for item in store.list_items(args.user):
            asset = catalog.MODELS.get(item.item_class)
            if not asset or not item.glb_url.startswith("/fixtures/"):
                continue
            glb, _ = normalize_glb(catalog.fetch(asset), item.item_class, (item.dims_m.w, item.dims_m.d, item.dims_m.h))
            store.put_item(item.model_copy(update={"glb_url": files.save(f"items/{item.item_id}.glb", glb)}))
            print(f"{item.label}: now uses the catalog model {asset}")
    elif args.cmd == "add":
        for item_class in args.classes:
            item = pipeline.import_glb(catalog.fetch(catalog.MODELS[item_class]), item_class.replace("_", " ").capitalize(), item_class, TYPICAL_SIZE_M[item_class], args.user, store)
            print(f"added {item.label}: {item.dims_m.w} × {item.dims_m.d} × {item.dims_m.h} m, item_id {item.item_id}")
    else:
        dims = tuple(float(x) for x in args.dims.lower().split("x")) if args.dims else (None, None, None)
        item = pipeline.import_glb(args.file.read_bytes(), args.label, args.item_class, dims, args.user, store)
        print(f"added {item.label}: {item.dims_m.w} × {item.dims_m.d} × {item.dims_m.h} m, item_id {item.item_id}")
    print("If the API is running, open /me or ask the guide; inventory.md updates on the next request.")


if __name__ == "__main__":
    main()
