"""Create DB Pulse's own indexes on its internal database (idempotent).

The API also does this at startup; this script exists for deployments that prefer to run it
as an explicit step (e.g. a Docker init job). It never touches monitored databases.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.config import get_settings  # noqa: E402
from app.database import ensure_indexes, get_internal_db  # noqa: E402

if __name__ == "__main__":
    ensure_indexes()
    db = get_internal_db()
    print(f"indexes ensured on {get_settings().internal_db_name}:")
    for name in sorted(db.list_collection_names()):
        print(f"  {name}: {[i['name'] for i in db[name].list_indexes()]}")
