"""Capture REAL explain output from a local MongoDB as regression fixtures.

Usage: python scripts/capture_explain_fixtures.py [mongodb://127.0.0.1:27118]
The recorded files document the server version they came from (serverInfo.version).
"""

from __future__ import annotations

import sys
from pathlib import Path

from bson import json_util
from pymongo import MongoClient

OUT = Path(__file__).resolve().parents[1] / "backend" / "app" / "tests" / "fixtures"
URI = sys.argv[1] if len(sys.argv) > 1 else "mongodb://127.0.0.1:27118"
DB = "dbpulse_fixture_capture"


def explain(db, cmd):
    return db.command({"explain": cmd, "verbosity": "executionStats"})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    c = MongoClient(URI, serverSelectionTimeoutMS=3000)
    c.drop_database(DB)
    db = c[DB]
    docs = [
        {
            "customer_id": i % 400,
            "status": ["COMPLETED", "PENDING", "CANCELLED"][i % 3],
            "total": i % 977,
            "n": i,
            "tag": f"t{i % 50}",
        }
        for i in range(30000)
    ]
    db.orders.insert_many(docs)
    cases = {}
    flt = {"customer_id": 7, "status": "COMPLETED"}
    cases["find_collscan"] = explain(db, {"find": "orders", "filter": flt})
    cases["find_sort_no_index"] = explain(
        db, {"find": "orders", "filter": {"customer_id": 7}, "sort": {"total": -1}}
    )
    cases["count_collscan"] = explain(db, {"count": "orders", "query": flt})
    cases["aggregate_collscan"] = explain(
        db,
        {
            "aggregate": "orders",
            "cursor": {},
            "pipeline": [{"$match": {"status": "PENDING"}}, {"$group": {"_id": "$tag", "n": {"$sum": 1}}}],
        },
    )
    db.orders.create_index([("customer_id", 1), ("status", 1)])
    cases["find_ixscan_compound"] = explain(db, {"find": "orders", "filter": flt})
    cases["find_covered"] = explain(
        db, {"find": "orders", "filter": flt, "projection": {"_id": 0, "customer_id": 1, "status": 1}}
    )
    cases["find_or"] = explain(db, {"find": "orders", "filter": {"$or": [{"customer_id": 3}, {"tag": "t4"}]}})
    cases["aggregate_indexed"] = explain(
        db,
        {
            "aggregate": "orders",
            "cursor": {},
            "pipeline": [{"$match": {"customer_id": 7}}, {"$sort": {"total": 1}}, {"$limit": 5}],
        },
    )
    cases["find_empty_result"] = explain(db, {"find": "orders", "filter": {"customer_id": -1, "status": "X"}})
    for name, doc in cases.items():
        (OUT / f"explain_{name}.json").write_text(json_util.dumps(doc, indent=1), encoding="utf-8")
    c.drop_database(DB)
    print(f"captured {len(cases)} fixtures from MongoDB {c.server_info()['version']} into {OUT}")


if __name__ == "__main__":
    main()
