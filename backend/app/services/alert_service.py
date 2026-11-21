"""Alert creation with deduplication + cooldown, and lifecycle (acknowledge / resolve)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from bson import ObjectId
from pymongo.database import Database

from app.core.exceptions import NotFoundError, ValidationFailed
from app.models.enums import ALERT_STATUSES, ALERT_TYPES, SEVERITIES, SOURCE_OBSERVED
from app.utils.serialization import utcnow


def make_dedupe_key(
    alert_type: str, database: str | None, collection: str | None, fingerprint: str | None
) -> str:
    return "|".join([alert_type, database or "-", collection or "-", fingerprint or "-"])


def raise_alert(
    db: Database,
    *,
    instance: dict,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    evidence: dict[str, Any] | None = None,
    database: str | None = None,
    collection: str | None = None,
    fingerprint: str | None = None,
    source: str = SOURCE_OBSERVED,
) -> tuple[dict, bool]:
    """Create an alert unless an identical one is still active inside its cooldown.

    Returns ``(alert, created)``. A suppressed duplicate increments ``occurrences`` on the
    existing alert instead, so the user sees the frequency without alert spam.
    """
    if alert_type not in ALERT_TYPES:
        raise ValidationFailed(f"Unknown alert type {alert_type}")
    settings = instance.get("settings", {})
    severity = settings.get("severity_overrides", {}).get(alert_type, severity)
    if severity not in SEVERITIES:
        raise ValidationFailed(f"Unknown severity {severity}")
    cooldown = int(settings.get("alert_cooldown_s", 900))
    key = make_dedupe_key(alert_type, database, collection, fingerprint)
    now = utcnow()
    existing = db.alerts.find_one(
        {
            "instance_id": instance["_id"],
            "dedupe_key": key,
            "source": source,
            "$or": [
                {"status": {"$in": ["open", "acknowledged"]}},
                {"last_seen": {"$gte": now - timedelta(seconds=cooldown)}},
            ],
        },
        sort=[("last_seen", -1)],
    )
    if existing:
        db.alerts.update_one(
            {"_id": existing["_id"]},
            {"$inc": {"occurrences": 1}, "$set": {"last_seen": now}},
        )
        existing["occurrences"] = existing.get("occurrences", 1) + 1
        return existing, False
    doc = {
        "instance_id": instance["_id"],
        "owner_id": instance["owner_id"],
        "type": alert_type,
        "severity": severity,
        "status": "open",
        "title": title,
        "message": message,
        "evidence": evidence or {},
        "database": database,
        "collection": collection,
        "fingerprint": fingerprint,
        "dedupe_key": key,
        "occurrences": 1,
        "source": source,
        "created_at": now,
        "last_seen": now,
        "acknowledged_at": None,
        "acknowledged_by": None,
        "resolved_at": None,
        "resolved_by": None,
    }
    doc["_id"] = db.alerts.insert_one(doc).inserted_id
    return doc, True


def _transition(db: Database, alert_id: str, user_id: ObjectId, new_status: str) -> dict:
    if new_status not in ALERT_STATUSES:
        raise ValidationFailed("Invalid status")
    try:
        oid = ObjectId(alert_id)
    except Exception as exc:
        raise NotFoundError("Alert not found") from exc
    alert = db.alerts.find_one({"_id": oid})
    if not alert:
        raise NotFoundError("Alert not found")
    now = utcnow()
    update: dict[str, Any] = {"status": new_status}
    if new_status == "acknowledged":
        update.update(acknowledged_at=now, acknowledged_by=user_id)
    elif new_status == "resolved":
        update.update(resolved_at=now, resolved_by=user_id)
    db.alerts.update_one({"_id": oid}, {"$set": update})
    alert.update(update)
    return alert


def acknowledge(db: Database, alert_id: str, user_id: ObjectId) -> dict:
    return _transition(db, alert_id, user_id, "acknowledged")


def resolve(db: Database, alert_id: str, user_id: ObjectId) -> dict:
    return _transition(db, alert_id, user_id, "resolved")
