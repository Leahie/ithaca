"""Record/replay cache that every external call goes through (I-G20, C7).

- live:   always call the service, never touch fixtures.
- record: use the fixture if it exists, otherwise call the service and save the response.
- replay: fixtures only; a missing fixture is an error. CI runs in this mode.

Fixtures live at fixtures/integrations/<service>/<name>-<hash>.json, where the hash covers the
request (never the credentials). Delete a fixture to record it again.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from services.api.integrations.config import env, fixtures_dir


class IntegrationMode(str, Enum):
    LIVE = "live"
    RECORD = "record"
    REPLAY = "replay"


class FixtureMissing(RuntimeError):
    pass


def request_hash(request: Any) -> str:
    blob = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class RecordReplayCache:
    def __init__(self, root: Path | None = None, mode: IntegrationMode | str | None = None):
        self.root = Path(root) if root else fixtures_dir() / "integrations"
        self.mode = IntegrationMode(mode or env("INTEGRATION_MODE", "replay"))

    def path_for(self, service: str, name: str, request: Any) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        return self.root / service / f"{safe}-{request_hash(request)}.json"

    def call(self, service: str, name: str, request: Any, fetch: Callable[[], Any]) -> Any:
        if self.mode is IntegrationMode.LIVE:
            return fetch()
        path = self.path_for(service, name, request)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        if self.mode is IntegrationMode.REPLAY:
            raise FixtureMissing(
                f"no fixture for {service}/{name} at {path}; record it with INTEGRATION_MODE=record"
            )
        response = fetch()
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"service": service, "name": name, "request": request, "response": response}
        path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        return response
