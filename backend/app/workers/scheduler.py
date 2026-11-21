"""Lightweight in-process scheduler (local development without Redis/Celery)."""

from __future__ import annotations

import logging
import threading

from app.config import get_settings
from app.workers import tasks

log = logging.getLogger("dbpulse.scheduler")


class Scheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="dbpulse-scheduler")
        self._thread.start()
        log.info("in-process scheduler started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        tick = get_settings().scheduler_tick_s
        cleanups = 0
        while not self._stop.wait(tick):
            try:
                tasks.run_collection_cycle()
                cleanups += 1
                if cleanups % max(1, 3600 // tick) == 0:  # roughly hourly
                    tasks.run_retention_cleanup()
            except Exception:
                log.exception("scheduler cycle failed")
