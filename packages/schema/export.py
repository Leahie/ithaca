"""Write JSON Schema for every shared type to packages/schema/json/ (I-G5).

    python -m packages.schema.export          # write
    python -m packages.schema.export --check  # fail if the committed files are stale (CI)

Stream A generates TypeScript from these files (e.g. json-schema-to-typescript into
packages/schema/ts/).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic.json_schema import models_json_schema

from packages.schema import events, reports, session, twin

OUT = Path(__file__).parent / "json"


def build() -> dict[str, str]:
    event_models = [events.Envelope, *dict.fromkeys(model for model, _ in events.EVENTS.values())]
    groups = {
        "events": event_models,
        "session": [session.Session, session.ViewerState],
        "twin": [twin.PropertyTwin],
        "reports": [
            reports.SunReport, reports.SunMoment, reports.SunRoomDay, reports.SunSpot,
            reports.DaylightMoment, reports.DaylightSpot, reports.NoiseReport, reports.AreaProfile,
            reports.SoundCatalog, reports.SoundField, reports.NoiseHouse, reports.NoiseSources,
        ],
    }
    files = {}
    for name, models in groups.items():
        _, schema = models_json_schema([(m, "validation") for m in models], title=name)
        if name == "events":
            schema["x-event-types"] = {
                t: {"payload": model.__name__, "sent_by": sorted(senders)} for t, (model, senders) in events.EVENTS.items()
            }
        files[f"{name}.schema.json"] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return files


def main() -> None:
    files = build()
    if "--check" in sys.argv:
        stale = [n for n, text in files.items() if not (OUT / n).exists() or (OUT / n).read_text() != text]
        if stale:
            sys.exit(f"stale schema files: {', '.join(stale)}. Run: python -m packages.schema.export")
        return
    OUT.mkdir(exist_ok=True)
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {len(files)} files to {OUT}")


if __name__ == "__main__":
    main()
