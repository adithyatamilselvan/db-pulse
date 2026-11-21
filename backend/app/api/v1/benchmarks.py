from __future__ import annotations

from bson import ObjectId
from fastapi import APIRouter, Query

from app.core.dependencies import DB, CurrentUser, accessible_instance_ids, load_instance
from app.core.exceptions import NotFoundError
from app.schemas.common import BenchmarkIn, CompareIn
from app.services import benchmark_service as bs
from app.services import connection_service as conn
from app.utils.serialization import to_jsonable

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


def _present(doc: dict) -> dict:
    out = to_jsonable(doc)
    prog = bs.get_progress(str(doc["_id"]))
    if prog:
        out["progress"] = prog
        out["target_iterations"] = doc["config"].get("iterations")
    return out


def _load(db: DB, bench_id: str, user: dict) -> dict:
    try:
        doc = db.benchmarks.find_one({"_id": ObjectId(bench_id)})
    except Exception as exc:
        raise NotFoundError("Benchmark not found") from exc
    if not doc:
        raise NotFoundError("Benchmark not found")
    load_instance(db, str(doc["instance_id"]), user)
    return doc


@router.post("/{instance_id}/validate")
def validate(instance_id: str, body: BenchmarkIn, user: CurrentUser, db: DB) -> dict:
    """Preflight: validate limits and report whether the run counts as high-load."""
    load_instance(db, instance_id, user)
    cfg = body.model_dump()
    cfg["confirm_high_load"] = True
    norm = bs.validate_config(cfg)
    return {
        "valid": True,
        "high_load": norm["high_load"],
        "warnings": norm["warnings"],
        "target": f"{norm['database']}.{norm['collection']}",
    }


@router.post("/{instance_id}/run", status_code=202)
def run(instance_id: str, body: BenchmarkIn, user: CurrentUser, db: DB) -> dict:
    """Start a read-only benchmark in the background; poll GET /benchmarks/{id}."""
    inst = load_instance(db, instance_id, user)
    cfg = bs.validate_config(body.model_dump())
    doc = bs.start_benchmark(db, conn.get_monitored_client(inst), instance=inst, user_id=user["_id"], cfg=cfg)
    return {"benchmark": _present(doc)}


@router.get("")
def list_benchmarks(
    user: CurrentUser, db: DB, instance_id: str | None = None, limit: int = Query(30, ge=1, le=200)
) -> dict:
    ids = [load_instance(db, instance_id, user)["_id"]] if instance_id else accessible_instance_ids(db, user)
    rows = (
        db.benchmarks.find(
            {"instance_id": {"$in": ids}}, {"result.latency_samples_ms": 0, "result.histogram": 0}
        )
        .sort("created_at", -1)
        .limit(limit)
    )
    return {"items": [_present(r) for r in rows]}


@router.post("/compare", status_code=201)
def compare(body: CompareIn, user: CurrentUser, db: DB) -> dict:
    b = _load(db, body.before_id, user)
    _load(db, body.after_id, user)
    comparison = bs.compare_benchmarks(
        db, owner_id=user["_id"], before_id=body.before_id, after_id=body.after_id
    )
    if comparison["verdict"] == "REGRESSED":
        from app.services import alert_service

        inst = db.instances.find_one({"_id": b["instance_id"]})
        alert_service.raise_alert(
            db,
            instance=inst,
            alert_type="BENCHMARK_REGRESSION",
            severity="MEDIUM",
            title=f"Benchmark regression on {b['target']}",
            message=comparison["summary"],
            evidence={"before": b["label"], "after": comparison["after_label"]},
            database=b["config"]["database"],
            collection=b["config"]["collection"],
        )
    return {"comparison": to_jsonable(comparison)}


@router.get("/comparisons")
def list_comparisons(user: CurrentUser, db: DB, limit: int = Query(30, ge=1, le=200)) -> dict:
    ids = accessible_instance_ids(db, user)
    rows = db.comparisons.find({"instance_id": {"$in": ids}}).sort("created_at", -1).limit(limit)
    return {"items": to_jsonable(list(rows))}


@router.get("/comparisons/{comparison_id}")
def get_comparison(comparison_id: str, user: CurrentUser, db: DB) -> dict:
    try:
        doc = db.comparisons.find_one({"_id": ObjectId(comparison_id)})
    except Exception as exc:
        raise NotFoundError("Comparison not found") from exc
    if not doc:
        raise NotFoundError("Comparison not found")
    load_instance(db, str(doc["instance_id"]), user)
    before = db.benchmarks.find_one({"_id": doc["before_id"]})
    after = db.benchmarks.find_one({"_id": doc["after_id"]})
    return {"comparison": to_jsonable(doc), "before": to_jsonable(before), "after": to_jsonable(after)}


@router.get("/{benchmark_id}")
def get_benchmark(benchmark_id: str, user: CurrentUser, db: DB) -> dict:
    return {"benchmark": _present(_load(db, benchmark_id, user))}


@router.post("/{benchmark_id}/cancel")
def cancel(benchmark_id: str, user: CurrentUser, db: DB) -> dict:
    _load(db, benchmark_id, user)
    return {"cancel_requested": bs.cancel_benchmark(benchmark_id)}
