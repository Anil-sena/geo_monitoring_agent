"""Background execution of pipeline runs.

A small thread pool is plenty here: the pipeline is dominated by Earth Engine
network time, and GDAL/rasterio release the GIL for the heavy parts. Status
updates are pushed into per-run queues so the API can stream them over SSE.
"""
from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator

from geoagent.config import settings
from geoagent.db.models import RunStatus
from geoagent.pipeline.monitor import execute_run

log = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, workers: int) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pipeline")
        self._listeners: dict[str, list[queue.Queue]] = {}
        self._lock = threading.Lock()
        self._running: set[str] = set()

    def submit(self, run_id: str) -> None:
        with self._lock:
            if run_id in self._running:
                return
            self._running.add(run_id)
        self._pool.submit(self._work, run_id)

    def is_running(self, run_id: str) -> bool:
        return run_id in self._running

    def _work(self, run_id: str) -> None:
        try:
            execute_run(run_id, on_status=lambda s, note: self._publish(run_id, s, note))
        finally:
            with self._lock:
                self._running.discard(run_id)
                self._listeners.pop(run_id, None)

    def _publish(self, run_id: str, status: RunStatus, note: str) -> None:
        with self._lock:
            listeners = list(self._listeners.get(run_id, []))
        for q in listeners:
            q.put((status.value, note))

    def events(self, run_id: str) -> Iterator[tuple[str, str]]:
        """Yield (status, note) tuples until the run finishes."""
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._listeners.setdefault(run_id, []).append(q)
        try:
            while True:
                try:
                    status, note = q.get(timeout=15)
                except queue.Empty:
                    if not self.is_running(run_id):
                        return
                    yield ("heartbeat", "")
                    continue
                yield (status, note)
                if status in (RunStatus.completed.value, RunStatus.failed.value):
                    return
        finally:
            with self._lock:
                if run_id in self._listeners:
                    try:
                        self._listeners[run_id].remove(q)
                    except ValueError:
                        pass

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


runner = JobRunner(workers=settings.WORKERS)
