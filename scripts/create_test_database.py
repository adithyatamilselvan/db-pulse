"""Create (or drop) an isolated, deterministic test database for manual experiments.

Only databases whose names start with 'dbpulse_test_' can be created or dropped by this script,
so it can never touch a real database by accident.

    python scripts/create_test_database.py --name dbpulse_test_shop --docs 50000
    python scripts/create_test_database.py --name dbpulse_test_shop --drop
"""

from __future__ import annotations

import argparse
import random
import sys

from pymongo import MongoClient

PREFIX = "dbpulse_test_"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mongo", default="mongodb://127.0.0.1:27118")
    p.add_argument("--name", default=f"{PREFIX}shop")
    p.add_argument("--docs", type=int, default=50_000)
    p.add_argument("--seed", type=int, default=1234)
    p.add_argument("--drop", action="store_true")
    args = p.parse_args()
    if not args.name.startswith(PREFIX):
        sys.exit(f"Refusing: database name must start with '{PREFIX}'")
    client = MongoClient(args.mongo, serverSelectionTimeoutMS=4000, appname="dbpulse-test-db-script")
    if args.drop:
        client.drop_database(args.name)
        print(f"dropped {args.name}")
        return
    rng = random.Random(args.seed)
    client.drop_database(args.name)
    docs = [
        {
            "order_id": i,
            "customer_id": rng.randint(1, 600),
            "status": rng.choice(["COMPLETED", "PENDING", "CANCELLED"]),
            "total_amount": round(rng.uniform(1, 999), 2),
        }
        for i in range(args.docs)
    ]
    client[args.name].orders.insert_many(docs)
    print(f"created {args.name}.orders with {args.docs} documents (seed {args.seed})")


if __name__ == "__main__":
    main()
