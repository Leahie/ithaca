"""Furniture from the command line.

    python -m services.api.objects.cli restyle --user demo
        Give the three sample pieces real-looking catalog models (same ids and sizes).

    python -m services.api.objects.cli add --user demo sofa bookshelf plant
        Add catalog pieces by class, at their typical size.

    python -m services.api.objects.cli import-glb chair.glb --user demo --label "My chair" --class armchair --dims 0.8x0.85x0.9
        Add a .glb you made elsewhere (e.g. on a TRELLIS demo site). --dims is width x depth x height in meters.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from services.api.furniture.store import furniture_store
from services.api.objects import files, pipeline
from services.api.objects.classes import FURNITURE_CLASSES, TYPICAL_SIZE_M
from services.api.objects.normalize import normalize_glb
from services.api.objects.providers import catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
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
