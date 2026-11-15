"""Background jobs inside the API process (B8): anything slower than about 2 s.

A job runs in a thread and reports through `job.progress` events to every session of its user.
This is the stand-in for the arq worker; the job functions don't know which one runs them.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from packages.schema.events import make_event
from services.api.realtime.hub import Hub

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]


class JobRunner:
    def __init__(self, hub: Hub, loop: asyncio.AbstractEventLoop) -> None:
        self.hub, self.loop = hub, loop
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
        self.status: dict[str, dict] = {}

    def _publish(self, user_id: str, job_id: str, kind: str, pct: float, status: str, result_ref: str | None = None, note: str | None = None) -> None:
        self.status[job_id] = {"job_id": job_id, "kind": kind, "pct": round(pct, 1), "status": status, "result_ref": result_ref, "note": note or None}

        def send() -> None:
            for room in self.hub.rooms.values():
                if room.user_id == user_id:
                    self.hub.publish(make_event(room.session_id, "server", "job.progress", self.status[job_id]))

        self.loop.call_soon_threadsafe(send)

    def submit(
        self,
        kind: str,
        user_id: str,
        work: Callable[[Progress], Any],
        on_done: Callable[[Any], None] | None = None,
        result_ref: Callable[[Any], str | None] = lambda result: None,
    ) -> str:
        """Start `work(progress)`. `on_done(result)` runs on the event loop after success."""
        job_id = uuid.uuid4().hex
        self._publish(user_id, job_id, kind, 0, "queued")

        def run() -> None:
            try:
                result = work(lambda pct, note="": self._publish(user_id, job_id, kind, min(pct, 99), "running", note=note))
            except Exception as e:
                log.exception("job %s (%s) failed", job_id, kind)
                self._publish(user_id, job_id, kind, 100, "failed", str(e))
                return
            self._publish(user_id, job_id, kind, 100, "done", result_ref(result))
            if on_done:
                self.loop.call_soon_threadsafe(on_done, result)

        self._pool.submit(run)
        return job_id
