"""Celery wiring for the Docker deployment (Redis broker). Same task functions as local mode.

Run:  celery -A app.workers.celery_app worker --beat --loglevel=INFO
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings
from app.workers import tasks

_s = get_settings()
celery = Celery("dbpulse", broker=_s.celery_broker_url, backend=None)
celery.conf.update(
    task_ignore_result=True,
    timezone="UTC",
    beat_schedule={
        "collect-cycle": {"task": "dbpulse.collect_cycle", "schedule": float(_s.scheduler_tick_s)},
        "retention-cleanup": {"task": "dbpulse.retention_cleanup", "schedule": 3600.0},
    },
)


@celery.task(name="dbpulse.collect_cycle")
def collect_cycle() -> int:
    return tasks.run_collection_cycle()


@celery.task(name="dbpulse.retention_cleanup")
def retention_cleanup() -> int:
    return tasks.run_retention_cleanup()
