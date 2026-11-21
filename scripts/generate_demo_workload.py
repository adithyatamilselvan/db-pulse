"""Generate a REAL query workload against the demo database and let DB Pulse collect it.

Queries are executed by this script (appName 'demo-app'), recorded by MongoDB's profiler, then
collected by DB Pulse through its normal profiler path. The profiler must be enabled on the demo
database; pass --enable-profiler to do that through the API (requires admin and confirmation).

    python scripts/generate_demo_workload.py --enable-profiler --queries 40
"""

from __future__ import annotations

import argparse
import random
import sys

import httpx
from pymongo import MongoClient


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--mongo", default="mongodb://127.0.0.1:27118")
    p.add_argument("--db", default="dbpulse_demo")
    p.add_argument("--email", default="demo@example.com")
    p.add_argument("--password", default="Demo-password-123")
    p.add_argument("--queries", type=int, default=40)
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--enable-profiler", action="store_true")
    args = p.parse_args()

    api = httpx.Client(base_url=args.api.rstrip("/") + "/api/v1", timeout=120)
    r = api.post("/auth/login", json={"email": args.email, "password": args.password})
    if r.status_code != 200:
        sys.exit("Login failed; run scripts/demo_optimization.py once (or register) first")
    api.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    insts = [i for i in api.get("/databases").json()["instances"] if i["is_demo"]]
    if not insts:
        sys.exit("No demo instance. Run POST /demo/setup (or scripts/demo_optimization.py) first.")
    iid = insts[0]["id"]

    if args.enable_profiler:
        r = api.post(
            f"/monitoring/{iid}/profiler",
            json={"database": args.db, "level": 1, "slowms": 5, "confirm": True},
        )
        print("profiler:", r.status_code, r.json().get("current", r.json()))

    rng = random.Random(args.seed)
    app = MongoClient(args.mongo, appname="demo-app")
    orders = app[args.db].orders
    for _ in range(args.queries):
        choice = rng.random()
        if choice < 0.6:
            list(
                orders.find(
                    {"customer_id": rng.randint(1, 5000), "status": rng.choice(["COMPLETED", "PENDING"])}
                )
            )
        elif choice < 0.85:
            list(orders.find({"total_amount": {"$gt": rng.uniform(4000, 4990)}}).limit(50))
        else:
            list(
                orders.aggregate(
                    [
                        {"$match": {"status": "SHIPPED"}},
                        {"$group": {"_id": "$customer_id", "n": {"$sum": 1}}},
                        {"$limit": 10},
                    ]
                )
            )
    print(f"ran {args.queries} real queries")
    res = api.post(f"/monitoring/{iid}/collect-slow").json()["results"]
    print("collected:", res)


if __name__ == "__main__":
    main()
