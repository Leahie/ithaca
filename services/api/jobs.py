"""Background jobs inside the API process (B8): anything slower than about 2 s.

A job runs in a thread and reports through `job.progress` events to every session of its user.
This is the stand-in for the arq worker; the job functions don't know which one runs them.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from packages.schema.events import make_event
from services.api.realtime.hub import Hub

log = logging.getLogger(__name__)

Progress = Callable[[float, str], None]


class JobAbandoned(BaseException):
    """Raised inside a job, from its progress report, when everything was reset while it ran
    (JobRunner.wipe). Not an Exception: the pipelines catch those piece by piece ("one piece
    failing must not lose the others"), and this has to end the whole job."""


class JobRunner:
    def __init__(self, hub: Hub, loop: asyncio.AbstractEventLoop) -> None:
        self.hub, self.loop = hub, loop
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
        self.status: dict[str, dict] = {}
        self._lock = threading.RLock()  # the three below are written from job threads, routes and the event loop
        self._going: dict[str, Future] = {}  # job_id -> its future, while it is queued or running
        self._abandoned: set[str] = set()  # jobs a reset left behind: nothing more is heard of them

    def _publish(self, user_id: str, job_id: str, kind: str, pct: float, status: str, result_ref: str | None = None, note: str | None = None) -> None:
        payload = {"job_id": job_id, "kind": kind, "pct": round(pct, 1), "status": status, "result_ref": result_ref, "note": note or None}
        with self._lock:
            if job_id in self._abandoned:
                return  # no screen is told, and the status of a job from before the reset is not kept
            self.status[job_id] = payload

        def send() -> None:
            for room in self.hub.rooms.values():
                if room.user_id == user_id:
                    self.hub.publish(make_event(room.session_id, "server", "job.progress", payload))

        self.loop.call_soon_threadsafe(send)

    # --- reset everything (routers/reset.py) ----------------------------------------------------

    def wipe(self) -> None:
        """Forget every job. One that has not started is cancelled. One that is running cannot be
        killed: it is abandoned, which ends it at its next progress report, and no screen or guide
        hears of it again. `going()` says how many are still at work."""
        with self._lock:
            for job_id, future in list(self._going.items()):
                self._abandoned.add(job_id)
                future.cancel()
            self.status.clear()

    def going(self) -> int:
        """How many jobs are queued or running."""
        with self._lock:
            return len(self._going)

    def _finished(self, job_id: str) -> None:
        with self._lock:
            self._going.pop(job_id, None)
            self._abandoned.discard(job_id)

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

        def report(pct: float, note: str = "") -> None:
            if job_id in self._abandoned:
                raise JobAbandoned(job_id)
            self._publish(user_id, job_id, kind, min(pct, 99), "running", note=note)

        def run() -> None:
            try:
                result = work(report)
            except JobAbandoned:
                log.info("job %s (%s) stopped: everything was reset while it ran", job_id, kind)
                return
            except Exception as e:
                log.exception("job %s (%s) failed", job_id, kind)
                self._publish(user_id, job_id, kind, 100, "failed", str(e))
                return
            if job_id in self._abandoned:
                return  # it got to its end after a reset: nobody is told, and its guide is not woken
            self._publish(user_id, job_id, kind, 100, "done", result_ref(result))
            if on_done:
                self.loop.call_soon_threadsafe(on_done, result)

        with self._lock:  # so a reset between these two lines cannot miss the job
            future = self._going[job_id] = self._pool.submit(run)
        future.add_done_callback(lambda _: self._finished(job_id))
        return job_id
