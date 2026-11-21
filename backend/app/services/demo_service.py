"""Demo mode.

Two kinds of data, never blurred:
 * REAL: an ``orders`` collection etc. generated into a *monitored* demo database. Queries against
   it are genuine, so explain plans and benchmarks on it are genuine measurements.
 * SYNTHETIC: pre-generated history (metrics, slow queries, alerts, benchmarks) stored in the
   internal DB with ``source: "SYNTHETIC"``. It exists only so the dashboards are explorable
   immediately; it is labelled everywhere and is never a real measurement.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any

from pymongo import MongoClient
from pymongo.database import Database

from app.analysis.performance_analyzer import compare_runs
from app.analysis.query_fingerprint import fingerprint
from app.config import get_settings
from app.models.enums import SOURCE_SYNTHETIC
from app.repositories import instances as repo
from app.services import alert_service
from app.utils.serialization import utcnow
from app.utils.stats import summarize_latencies

STATUSES = ["COMPLETED", "PENDING", "CANCELLED", "REFUNDED", "SHIPPED"]
STATUS_WEIGHTS = [0.55, 0.15, 0.1, 0.05, 0.15]
SYNTH_COLLECTIONS = (
    "metrics",
    "slow_queries",
    "query_patterns",
    "alerts",
    "benchmarks",
    "comparisons",
    "execution_plans",
)


def demo_db_name() -> str:
    return get_settings().demo_db_name


def generate_demo_dataset(
    client: MongoClient,
    *,
    orders: int = 200_000,
    customers: int = 5000,
    seed: int = 42,
    db_name: str | None = None,
    drop: bool = True,
) -> dict:
    """Deterministically create REAL demo collections in the monitored demo database."""
    db_name = db_name or demo_db_name()
    if db_name in ("admin", "local", "config"):
        raise ValueError("refusing to write to a system database")
    rng = random.Random(seed)
    db = client[db_name]
    if drop:
        for c in ("orders", "customers", "products"):
            db.drop_collection(c)
    base = datetime(2026, 1, 1)
    batch: list[dict] = []
    for i in range(orders):
        batch.append(
            {
                "order_id": 100001 + i,
                "customer_id": rng.randint(1, customers),
                "status": rng.choices(STATUSES, STATUS_WEIGHTS)[0],
                "total_amount": round(rng.uniform(5, 5000), 2),
                "created_at": base + timedelta(seconds=rng.randint(0, 86400 * 300)),
            }
        )
        if len(batch) == 5000:
            db.orders.insert_many(batch, ordered=False)
            batch = []
    if batch:
        db.orders.insert_many(batch, ordered=False)
    db.customers.insert_many(
        [
            {"customer_id": c, "name": f"Customer {c}", "tier": rng.choice(["free", "pro", "enterprise"])}
            for c in range(1, customers + 1)
        ]
    )
    db.products.insert_many(
        [{"sku": f"SKU-{p:05d}", "price": round(rng.uniform(1, 900), 2)} for p in range(500)]
    )
    return {
        "database": db_name,
        "orders": orders,
        "customers": customers,
        "products": 500,
        "seed": seed,
        "indexes_on_orders": [i["name"] for i in db.orders.list_indexes()],
    }


def _synthetic_alert(db: Database, inst: dict, **kw: Any) -> None:
    alert_service.raise_alert(db, instance=inst, source=SOURCE_SYNTHETIC, **kw)


def generate_synthetic_history(
    db: Database, instance: dict, *, hours: int = 24, seed: int = 7
) -> dict[str, int]:
    """Create SYNTHETIC records. Everything is tagged ``source: SYNTHETIC``."""
    rng = random.Random(seed)
    now = utcnow()
    iid, oid = instance["_id"], instance["owner_id"]
    counts = dict.fromkeys(("metrics", "slow_queries", "patterns", "alerts", "benchmarks"), 0)
    shapes = [
        ("orders", "find", {"customer_id": "?", "status": "?"}, True, 900),
        ("orders", "find", {"created_at": {"$gte": "?"}}, True, 400),
        ("customers", "find", {"customer_id": "?"}, False, 120),
    ]
    docs_metrics = []
    for h in range(hours * 4):  # 15-minute snapshots
        ts = now - timedelta(minutes=15 * (hours * 4 - h))
        docs_metrics.append(
            {
                "instance_id": iid,
                "owner_id": oid,
                "ts": ts,
                "source": SOURCE_SYNTHETIC,
                "server": {
                    "connections_current": rng.randint(8, 40),
                    "connections_available": 800,
                    "uptime_s": 86400 + h * 900,
                    "mem_resident_mb": rng.randint(300, 600),
                    "opcounters": None,
                    "rates": {
                        "query": round(rng.uniform(5, 60), 2),
                        "insert": round(rng.uniform(1, 15), 2),
                        "update": round(rng.uniform(1, 10), 2),
                        "delete": round(rng.uniform(0, 3), 2),
                        "command": round(rng.uniform(10, 80), 2),
                        "getmore": 0.0,
                    },
                    "op_latency_ms": {
                        "reads": round(rng.uniform(0.4, 6), 2),
                        "writes": round(rng.uniform(0.3, 3), 2),
                        "commands": round(rng.uniform(0.1, 1), 2),
                    },
                },
                "databases": [],
                "unavailable": ["SYNTHETIC demo record"],
            }
        )
    db.metrics.insert_many(docs_metrics)
    counts["metrics"] = len(docs_metrics)

    slow_docs = []
    for coll, op, flt, scan, base_ms in shapes:
        fp = fingerprint(operation=op, collection=coll, filter_=flt)
        durations = []
        n = rng.randint(25, 60)
        for _ in range(n):
            ts = now - timedelta(minutes=rng.randint(1, hours * 60))
            ms = max(20, rng.gauss(base_ms, base_ms * 0.25))
            examined = rng.randint(180_000, 200_000) if scan else rng.randint(1, 40)
            durations.append(ms)
            slow_docs.append(
                {
                    "instance_id": iid,
                    "owner_id": oid,
                    "database": demo_db_name(),
                    "collection": coll,
                    "operation": op,
                    "ts": ts,
                    "duration_ms": round(ms, 1),
                    "threshold_ms": 100,
                    "severity": "HIGH" if ms > 600 else "MEDIUM" if ms > 300 else "LOW",
                    "fingerprint": fp,
                    "shape": {"op": op, "collection": coll, "filter": flt},
                    "command_redacted": {"find": coll, "filter": flt},
                    "plan_summary": "COLLSCAN" if scan else "IXSCAN { customer_id: 1 }",
                    "collection_scan": scan,
                    "index_scan": not scan,
                    "docs_examined": examined,
                    "keys_examined": 0 if scan else examined,
                    "docs_returned": rng.randint(5, 40),
                    "efficiency_ratio": None,
                    "client_app": "synthetic-demo",
                    "source": SOURCE_SYNTHETIC,
                    "dedupe_key": f"synthetic-{fp}-{ts.isoformat()}-{rng.random()}",
                }
            )
        db.query_patterns.insert_one(
            {
                "instance_id": iid,
                "owner_id": oid,
                "fingerprint": fp,
                "database": demo_db_name(),
                "collection": coll,
                "operation": op,
                "shape": {"op": op, "collection": coll, "filter": flt},
                "first_seen": now - timedelta(hours=hours),
                "last_seen": now,
                "count": n,
                "total_ms": sum(durations),
                "max_ms": max(durations),
                "recent_durations_ms": durations[-60:],
                "sample_enc": None,
                "last_plan_summary": "COLLSCAN" if scan else "IXSCAN",
                "source": SOURCE_SYNTHETIC,
            }
        )
        counts["patterns"] += 1
        if scan:
            _synthetic_alert(
                db,
                instance,
                alert_type="COLLECTION_SCAN",
                severity="HIGH",
                title=f"Collection scan on {demo_db_name()}.{coll}",
                message="SYNTHETIC demo alert",
                database=demo_db_name(),
                collection=coll,
                fingerprint=fp,
                evidence={"synthetic": True},
            )
            counts["alerts"] += 1
    db.slow_queries.insert_many(slow_docs)
    counts["slow_queries"] = len(slow_docs)
    _synthetic_alert(
        db,
        instance,
        alert_type="LATENCY_INCREASE",
        severity="MEDIUM",
        title="Latency increase (SYNTHETIC demo)",
        message="SYNTHETIC demo alert",
        database=demo_db_name(),
        collection="orders",
        evidence={"synthetic": True},
    )
    counts["alerts"] += 1

    def bench(label: str, mean: float, docs: int, plan: list[str]) -> dict:
        lat = [max(0.5, rng.gauss(mean, mean * 0.1)) for _ in range(100)]
        return {
            "instance_id": iid,
            "owner_id": oid,
            "label": label,
            "status": "completed",
            "source": SOURCE_SYNTHETIC,
            "target": f"{demo_db_name()}.orders",
            "created_at": now,
            "config": {
                "database": demo_db_name(),
                "collection": "orders",
                "operation": "find",
                "filter": {"customer_id": 1001, "status": "COMPLETED"},
                "iterations": 100,
            },
            "result": {
                "latency_ms": summarize_latencies(lat),
                "throughput_ops": 1000 / mean,
                "successful": 100,
                "failed": 0,
                "docs_examined": docs,
                "keys_examined": 0 if docs > 1000 else docs,
                "docs_returned": 24,
                "plan": {
                    "stage_names": plan,
                    "index_names": [] if "COLLSCAN" in plan else ["customer_id_1_status_1"],
                    "collection_scan_detected": "COLLSCAN" in plan,
                },
                "latency_samples_ms": lat,
                "histogram": {"edges": [], "counts": []},
            },
            "methodology": "SYNTHETIC demo record - not a real measurement",
        }

    b1, b2 = (
        bench("baseline (SYNTHETIC)", 80.0, 200_000, ["COLLSCAN"]),
        bench("with index (SYNTHETIC)", 0.8, 24, ["FETCH", "IXSCAN"]),
    )
    for b in (b1, b2):
        b["_id"] = db.benchmarks.insert_one(b).inserted_id
    cmp_ = compare_runs(
        {
            "latency_ms": b1["result"]["latency_ms"],
            **{
                k: b1["result"][k]
                for k in (
                    "throughput_ops",
                    "failed",
                    "docs_examined",
                    "keys_examined",
                    "docs_returned",
                    "plan",
                )
            },
        },
        {
            "latency_ms": b2["result"]["latency_ms"],
            **{
                k: b2["result"][k]
                for k in (
                    "throughput_ops",
                    "failed",
                    "docs_examined",
                    "keys_examined",
                    "docs_returned",
                    "plan",
                )
            },
        },
    )
    db.comparisons.insert_one(
        {
            "instance_id": iid,
            "owner_id": oid,
            "before_id": b1["_id"],
            "after_id": b2["_id"],
            "before_label": b1["label"],
            "after_label": b2["label"],
            "target": b1["target"],
            "same_query": True,
            **cmp_,
            "source": SOURCE_SYNTHETIC,
            "created_at": now,
        }
    )
    counts["benchmarks"] = 2
    return counts


def reset_synthetic(db: Database, instance_id: Any) -> dict[str, int]:
    return {
        c: db[c].delete_many({"instance_id": instance_id, "source": SOURCE_SYNTHETIC}).deleted_count
        for c in SYNTH_COLLECTIONS
    }


SYNTH_TIME_FIELDS = {
    "metrics": ("ts",),
    "slow_queries": ("ts",),
    "query_patterns": ("first_seen", "last_seen"),
    "alerts": ("created_at", "last_seen"),
    "benchmarks": ("created_at",),
    "comparisons": ("created_at",),
}
REFRESH_AFTER = timedelta(minutes=15)


def refresh_synthetic_history(db: Database, instance_id: Any) -> dict[str, Any]:
    """Synthetic history is generated relative to 'now', so it ages out of rolling windows such as
    "Last 24 hours". Move it forward in time (nothing is deleted or regenerated) so the newest
    record is current again. Only SYNTHETIC records of this instance are touched."""
    q = {"instance_id": instance_id, "source": SOURCE_SYNTHETIC}
    newest = db.slow_queries.find_one(q, {"ts": 1}, sort=[("ts", -1)])
    if not newest:
        return {"shifted": False, "shifted_by_s": 0}
    delta = utcnow() - newest["ts"]
    if delta < REFRESH_AFTER:
        return {"shifted": False, "shifted_by_s": 0}
    ms = int(delta.total_seconds() * 1000)
    for coll, fields in SYNTH_TIME_FIELDS.items():
        db[coll].update_many(
            q,
            [{"$set": {f: {"$dateAdd": {"startDate": f"${f}", "unit": "millisecond", "amount": ms}} for f in fields}}],
        )
    return {"shifted": True, "shifted_by_s": int(delta.total_seconds())}


def setup_demo(
    db: Database,
    *,
    user: dict,
    demo_uri: str,
    orders: int,
    seed: int,
    client_factory: Any,
    version: str | None,
) -> dict[str, Any]:
    """Idempotent and non-destructive: reuses the demo instance, never drops collections and never
    writes the real dataset or synthetic history twice."""
    existing = db.instances.find_one({"owner_id": user["_id"], "is_demo": True})
    if not existing and user.get("role") == "admin":  # admins already see every instance: don't duplicate
        existing = db.instances.find_one({"is_demo": True}, sort=[("created_at", 1)])
    if existing:
        inst = existing
    else:
        inst = repo.create_instance(
            db,
            owner_id=user["_id"],
            name="Demo MongoDB (local)",
            uri=demo_uri,
            allow_modifications=True,
            server_version=version,
            is_demo=True,
        )
    client = client_factory(inst)
    demo_db = client[demo_db_name()]
    present = {c: demo_db[c].estimated_document_count() for c in ("orders", "customers", "products")}
    if any(present.values()):
        dataset = {"database": demo_db_name(), **present, "status": "already_present"}
    else:
        dataset = {**generate_demo_dataset(client, orders=orders, seed=seed, drop=False), "status": "created"}
    has_synth = any(
        db[c].find_one({"instance_id": inst["_id"], "source": SOURCE_SYNTHETIC}, {"_id": 1}) for c in SYNTH_COLLECTIONS
    )
    if has_synth:
        shift = refresh_synthetic_history(db, inst["_id"])
        synth = {"status": "refreshed" if shift["shifted"] else "already_present", **shift}
    else:
        synth = {**generate_synthetic_history(db, inst, seed=seed), "status": "created"}
    return {
        "instance": repo.public(inst),
        "real_dataset": dataset,
        "synthetic_records": synth,
        "notice": "Dataset in the demo database is real; history records are SYNTHETIC.",
    }
