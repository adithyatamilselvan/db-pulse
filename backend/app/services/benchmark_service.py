"""Controlled, read-only benchmark engine.

Only find / count / aggregate (whitelisted stages) are executed, with per-operation timeouts,
bounded concurrency/iterations/duration, and a capped result size. Every number reported is
measured here; nothing is simulated. Plan details come from a real ``explain(executionStats)``
run once after the measured phase (outside the timed region).
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from bson import ObjectId
from pymongo import MongoClient
from pymongo import errors as pe
from pymongo.database import Database

from app.analysis.performance_analyzer import compare_runs
from app.analysis.scan_detector import analyze_plan
from app.config import get_settings
from app.core.exceptions import NotFoundError, ValidationFailed
from app.services import explain_service
from app.services.connection_service import classify_error
from app.utils.query_validation import MAX_RESULT_DOCS, validate_query_spec
from app.utils.serialization import utcnow
from app.utils.stats import summarize_latencies

log = logging.getLogger("dbpulse.benchmark")
METHODOLOGY = (
    "Warm-up iterations are run and discarded. Measured iterations time the full execution "
    "including draining the cursor. Latency is wall-clock per operation as seen by DB Pulse "
    "(network included). Throughput = successful operations / total wall-clock seconds. "
    "Documents/keys examined come from one explain(executionStats) after the measured phase. "
    "Results vary with cache state, concurrent load, hardware and data distribution; compare "
    "runs made back-to-back and repeat them before drawing conclusions."
)

_cancel_flags: dict[str, threading.Event] = {}
_progress: dict[str, dict[str, int]] = {}
_lock = threading.Lock()


def validate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a benchmark request; enforce limits and high-load confirmation."""
    s = get_settings()
    spec = validate_query_spec(cfg)

    def opt(key: str, default: Any) -> Any:
        v = cfg.get(key)  # API models dump unset optionals as None: treat that as "use default"
        return default if v is None else v

    iterations = int(opt("iterations", 100))
    warmup = int(opt("warmup_iterations", 5))
    concurrency = int(opt("concurrency", 1))
    duration = cfg.get("duration_s")
    timeout_ms = int(opt("timeout_ms", min(10_000, s.benchmark_op_timeout_ms)))
    if iterations < 1 or warmup < 0 or concurrency < 1:
        raise ValidationFailed("iterations/concurrency must be >= 1 and warmup >= 0")
    if iterations > s.benchmark_max_iterations:
        raise ValidationFailed(f"iterations exceeds the limit of {s.benchmark_max_iterations}")
    if warmup > 1000:
        raise ValidationFailed("warmup_iterations exceeds 1000")
    if concurrency > s.benchmark_max_concurrency:
        raise ValidationFailed(f"concurrency exceeds the limit of {s.benchmark_max_concurrency}")
    if duration is not None:
        duration = float(duration)
        if not 0 < duration <= s.benchmark_max_duration_s:
            raise ValidationFailed(f"duration_s must be within (0, {s.benchmark_max_duration_s}]")
    if not 100 <= timeout_ms <= s.benchmark_op_timeout_ms:
        raise ValidationFailed(f"timeout_ms must be within [100, {s.benchmark_op_timeout_ms}]")
    warnings = []
    if spec["operation"] == "find":
        spec["limit"] = min(spec["limit"] or MAX_RESULT_DOCS, MAX_RESULT_DOCS)
        if not cfg.get("limit"):
            warnings.append(f"No limit given: result size capped at {MAX_RESULT_DOCS} documents.")
    high_load = (
        iterations > s.benchmark_high_load_iterations
        or concurrency > s.benchmark_high_load_concurrency
        or (duration or 0) > 60
    )
    if high_load and not cfg.get("confirm_high_load"):
        raise ValidationFailed(
            "This is a high-load benchmark against a monitored database. Review the target and "
            "resubmit with confirm_high_load=true.",
            details={
                "high_load": True,
                "iterations": iterations,
                "concurrency": concurrency,
                "duration_s": duration,
            },
        )
    return {
        **spec,
        "iterations": iterations,
        "warmup_iterations": warmup,
        "concurrency": concurrency,
        "duration_s": duration,
        "timeout_ms": timeout_ms,
        "label": str(cfg.get("label") or "run")[:60],
        "warnings": warnings,
        "high_load": high_load,
    }


def _make_op(client: MongoClient, cfg: dict[str, Any]):
    coll = client[cfg["database"]][cfg["collection"]]
    t = cfg["timeout_ms"]
    op = cfg["operation"]
    if op == "find":

        def run() -> int:
            cur = coll.find(cfg["filter"], cfg.get("projection"), max_time_ms=t)
            if cfg.get("sort"):
                cur = cur.sort(list(cfg["sort"].items()))
            n = 0
            for _ in cur.limit(cfg["limit"]):
                n += 1
            return n
    elif op == "count":

        def run() -> int:
            return coll.count_documents(cfg["filter"], maxTimeMS=t)
    else:

        def run() -> int:
            return sum(1 for _ in coll.aggregate(cfg["pipeline"], maxTimeMS=t))

    return run


def _downsample(values: list[float], n: int = 600) -> list[float]:
    if len(values) <= n:
        return [round(v, 3) for v in values]
    step = len(values) / n
    return [round(values[int(i * step)], 3) for i in range(n)]


def _histogram(values: list[float], bins: int = 20) -> dict[str, list]:
    if not values:
        return {"edges": [], "counts": []}
    lo, hi = min(values), max(values)
    if hi == lo:
        return {"edges": [lo, hi], "counts": [len(values)]}
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in values:
        counts[min(int((v - lo) / width), bins - 1)] += 1
    return {"edges": [round(lo + i * width, 3) for i in range(bins + 1)], "counts": counts}


def execute(
    client: MongoClient, cfg: dict[str, Any], cancel: threading.Event, progress: dict[str, int]
) -> dict[str, Any]:
    """Run warm-up + measured phase and return the result summary (pure measurement)."""
    run = _make_op(client, cfg)
    errors: dict[str, int] = {}
    latencies: list[float] = []
    docs_seen: list[int] = []
    lat_lock = threading.Lock()

    for _ in range(cfg["warmup_iterations"]):
        if cancel.is_set():
            break
        with contextlib.suppress(pe.PyMongoError):
            run()  # warm-up failures are reported if they also occur in the measured phase

    deadline = time.perf_counter() + cfg["duration_s"] if cfg.get("duration_s") else None
    counter = {"next": 0}

    def one() -> bool:
        with lat_lock:
            if cancel.is_set() or (deadline and time.perf_counter() >= deadline):
                return False
            if not deadline and counter["next"] >= cfg["iterations"]:
                return False
            counter["next"] += 1
        t0 = time.perf_counter()
        try:
            n = run()
            ms = (time.perf_counter() - t0) * 1000
            with lat_lock:
                latencies.append(ms)
                docs_seen.append(n)
                progress["completed"] = len(latencies)
        except pe.PyMongoError as exc:
            _, msg = classify_error(exc)
            with lat_lock:
                errors[msg] = errors.get(msg, 0) + 1
                progress["failed"] = sum(errors.values())
        return True

    def worker() -> None:
        while one():
            pass

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=cfg["concurrency"]) as pool:
        futures = [pool.submit(worker) for _ in range(cfg["concurrency"])]
        for f in futures:
            f.result()
    wall = time.perf_counter() - started
    failed = sum(errors.values())
    ok = len(latencies)
    return {
        "latency_ms": summarize_latencies(latencies),
        "throughput_ops": (ok / wall) if wall > 0 else None,
        "wall_clock_s": wall,
        "successful": ok,
        "failed": failed,
        "errors": [{"message": m, "count": c} for m, c in sorted(errors.items(), key=lambda kv: -kv[1])[:5]],
        "docs_returned": docs_seen[-1] if docs_seen else None,
        "latency_samples_ms": _downsample(latencies),
        "histogram": _histogram(latencies),
        "cancelled": cancel.is_set(),
    }


def _run_job(internal: Database, client: MongoClient, bench_id: ObjectId, cfg: dict[str, Any]) -> None:
    key = str(bench_id)
    cancel, progress = _cancel_flags[key], _progress[key]
    try:
        internal.benchmarks.update_one(
            {"_id": bench_id}, {"$set": {"status": "running", "started_at": utcnow()}}
        )
        result = execute(client, cfg, cancel, progress)
        plan_block: dict[str, Any] = {"plan": None, "docs_examined": None, "keys_examined": None}
        try:
            ex = explain_service.explain_and_analyze(client, cfg)
            analysis = ex["analysis"]
            plan_block = {
                "plan": analysis,
                "docs_examined": analysis["documents_examined"],
                "keys_examined": analysis["keys_examined"],
            }
            if analysis.get("documents_returned") is not None and result["docs_returned"] is None:
                result["docs_returned"] = analysis["documents_returned"]
        except Exception as exc:
            log.warning("explain after benchmark failed: %s", type(exc).__name__)
            plan_block["plan_error"] = str(exc)[:200]
        status = (
            "cancelled" if result["cancelled"] else ("failed" if result["successful"] == 0 else "completed")
        )
        internal.benchmarks.update_one(
            {"_id": bench_id},
            {"$set": {"status": status, "finished_at": utcnow(), "result": {**result, **plan_block}}},
        )
    except Exception as exc:
        log.exception("benchmark %s crashed", key)
        internal.benchmarks.update_one(
            {"_id": bench_id},
            {
                "$set": {
                    "status": "failed",
                    "finished_at": utcnow(),
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                }
            },
        )
    finally:
        with _lock:
            _cancel_flags.pop(key, None)
            _progress.pop(key, None)


def start_benchmark(
    internal: Database,
    client: MongoClient,
    *,
    instance: dict,
    user_id: Any,
    cfg: dict[str, Any],
    background: bool = True,
) -> dict[str, Any]:
    if not client[cfg["database"]].list_collection_names(filter={"name": cfg["collection"]}):
        raise NotFoundError(f"Collection '{cfg['database']}.{cfg['collection']}' does not exist")
    stored = {k: v for k, v in cfg.items() if k != "limit_capped"}
    doc = {
        "instance_id": instance["_id"],
        "owner_id": instance["owner_id"],
        "user_id": user_id,
        "label": cfg["label"],
        "status": "queued",
        "config": stored,
        "target": f"{cfg['database']}.{cfg['collection']}",
        "source": "observed",
        "created_at": utcnow(),
        "result": None,
        "warnings": cfg["warnings"],
        "methodology": METHODOLOGY,
    }
    bench_id = internal.benchmarks.insert_one(doc).inserted_id
    key = str(bench_id)
    with _lock:
        _cancel_flags[key] = threading.Event()
        _progress[key] = {"completed": 0, "failed": 0}
    if background:
        threading.Thread(
            target=_run_job, args=(internal, client, bench_id, cfg), daemon=True, name=f"bench-{key[:6]}"
        ).start()
    else:
        _run_job(internal, client, bench_id, cfg)
    return internal.benchmarks.find_one({"_id": bench_id})


def cancel_benchmark(bench_id: str) -> bool:
    with _lock:
        ev = _cancel_flags.get(bench_id)
    if ev is None:
        return False
    ev.set()
    return True


def get_progress(bench_id: str) -> dict[str, int] | None:
    with _lock:
        p = _progress.get(bench_id)
        return dict(p) if p else None


def comparison_side(bench: dict) -> dict[str, Any]:
    r = bench.get("result") or {}
    return {
        "latency_ms": r.get("latency_ms", {}),
        "throughput_ops": r.get("throughput_ops"),
        "failed": r.get("failed", 0),
        "docs_examined": r.get("docs_examined"),
        "keys_examined": r.get("keys_examined"),
        "docs_returned": r.get("docs_returned"),
        "plan": r.get("plan"),
    }


def compare_benchmarks(internal: Database, *, owner_id: Any, before_id: str, after_id: str) -> dict[str, Any]:
    docs = []
    for bid in (before_id, after_id):
        try:
            d = internal.benchmarks.find_one({"_id": ObjectId(bid)})
        except Exception as exc:
            raise NotFoundError("Benchmark not found") from exc
        if not d:
            raise NotFoundError("Benchmark not found")
        if d["status"] not in ("completed",):
            raise ValidationFailed(f"Benchmark {bid} is '{d['status']}', not completed")
        docs.append(d)
    b, a = docs
    comparison = compare_runs(comparison_side(b), comparison_side(a))
    same_target = b["target"] == a["target"]
    if not same_target:
        comparison["caveats"].append("The two benchmarks target different collections.")
    same_query = all(
        b["config"].get(k) == a["config"].get(k)
        for k in ("operation", "filter", "sort", "projection", "pipeline", "limit")
    )
    if not same_query:
        comparison["caveats"].append(
            "The two benchmarks use different query definitions; the comparison may be misleading."
        )
    doc = {
        "instance_id": b["instance_id"],
        "owner_id": owner_id,
        "before_id": b["_id"],
        "after_id": a["_id"],
        "before_label": b["label"],
        "after_label": a["label"],
        "target": b["target"],
        "same_query": same_query,
        **comparison,
        "source": "observed" if b.get("source") == a.get("source") == "observed" else "SYNTHETIC",
        "created_at": utcnow(),
    }
    doc["_id"] = internal.comparisons.insert_one(doc).inserted_id
    return doc


def analyze_for_regression(before: dict, after: dict) -> bool:
    cmp_ = compare_runs(comparison_side(before), comparison_side(after))
    return cmp_["verdict"] == "REGRESSED"


__all__ = [
    "validate_config",
    "start_benchmark",
    "cancel_benchmark",
    "compare_benchmarks",
    "get_progress",
    "execute",
    "analyze_plan",
    "METHODOLOGY",
]
