"""Seed the REAL demo dataset directly into a monitored MongoDB (no DB Pulse API needed).

    python scripts/seed_demo_data.py --orders 200000 --seed 42

This only creates data in the demo database (default: dbpulse_demo). The SYNTHETIC history used by
demo mode is created separately by POST /api/v1/demo/setup and is always labelled SYNTHETIC.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from pymongo import MongoClient


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mongo", default="mongodb://127.0.0.1:27118")
    p.add_argument("--db", default="dbpulse_demo")
    p.add_argument("--orders", type=int, default=200_000)
    p.add_argument("--customers", type=int, default=5000)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()
    # imported lazily: the generator lives with the app so the API and this script share one source
    import os

    os.environ.setdefault("SECRET_KEY", "x" * 40)
    from cryptography.fernet import Fernet

    os.environ.setdefault("ENCRYPTION_KEY", Fernet.generate_key().decode())
    os.environ.setdefault("INTERNAL_MONGO_URI", args.mongo)
    from app.services.demo_service import generate_demo_dataset

    client = MongoClient(args.mongo, serverSelectionTimeoutMS=4000)
    info = generate_demo_dataset(
        client, orders=args.orders, customers=args.customers, seed=args.seed, db_name=args.db
    )
    print(info)


if __name__ == "__main__":
    main()
