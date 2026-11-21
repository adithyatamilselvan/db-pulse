"""DB Pulse end-to-end demonstration: optimizing a slow MongoDB query.

Drives the REAL DB Pulse API against REAL MongoDB. Nothing here is simulated: every number
printed comes from explain(executionStats) and measured benchmark runs on this machine.

  1. Baseline      - explain + benchmark of  orders.find({customer_id, status})
  2. Investigation - plan analysis, existing indexes, recommendation (an unverified hypothesis)
  3. Optimization  - asks for your approval, then creates the recommended index
  4. Re-test       - same explain + same benchmark
  5. Compare       - before/after plan, latency, P95/P99, throughput, docs/keys examined
  6. Report        - JSON + CSV + PDF written to ./demo-output

If MongoDB still chooses a COLLSCAN after the index exists, that is what gets reported.

Usage (backend running, see README):
    python scripts/demo_optimization.py --yes              # non-interactive
    python scripts/demo_optimization.py --orders 500000    # bigger dataset
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

CUSTOMER_ID = 1001
STATUS = "COMPLETED"


def fmt(v: object, unit: str = "") -> str:
    if v is None:
        return "unavailable"
    return f"{v:,.2f}{unit}" if isinstance(v, float) else f"{v:,}{unit}"


class Api:
    def __init__(self, base: str) -> None:
        self.c = httpx.Client(base_url=base.rstrip("/") + "/api/v1", timeout=600)

    def call(self, method: str, path: str, **kw: object) -> httpx.Response:
        r = self.c.request(method, path, **kw)
        if r.status_code >= 400:
            sys.exit(f"API error {r.status_code} on {method} {path}: {r.text[:400]}")
        return r

    def login(self, email: str, password: str) -> None:
        r = self.c.post("/auth/register", json={"email": email, "password": password, "name": "Demo"})
        if r.status_code == 409:
            r = self.c.post("/auth/login", json={"email": email, "password": password})
        if r.status_code >= 400:
            sys.exit(f"Cannot authenticate: {r.text[:300]}")
        self.c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        if r.json()["user"]["role"] != "admin":
            sys.exit("Index creation needs an administrator. Use the first registered account.")


def benchmark(api: Api, iid: str, db: str, label: str, iters: int) -> dict:
    body = {
        "database": db,
        "collection": "orders",
        "operation": "find",
        "filter": {"customer_id": CUSTOMER_ID, "status": STATUS},
        "label": label,
        "warmup_iterations": 10,
        "iterations": iters,
        "concurrency": 4,
    }
    bid = api.call("POST", f"/benchmarks/{iid}/run", json=body).json()["benchmark"]["id"]
    import time

    while True:
        b = api.call("GET", f"/benchmarks/{bid}").json()["benchmark"]
        if b["status"] in ("completed", "failed", "cancelled"):
            return b
        time.sleep(0.5)


def explain(api: Api, iid: str, db: str) -> dict:
    body = {"database": db, "collection": "orders", "filter": {"customer_id": CUSTOMER_ID, "status": STATUS}}
    return api.call("POST", f"/execution-plans/{iid}/explain", json=body).json()["plan"]


def show_plan(title: str, plan: dict) -> None:
    a = plan["analysis"]
    print(f"\n--- {title} ---")
    print(f"  stages            : {' > '.join(a['stage_names'])}")
    print(f"  index used        : {', '.join(a['index_names']) or 'none'}")
    print(f"  documents examined: {fmt(a['documents_examined'])}")
    print(f"  documents returned: {fmt(a['documents_returned'])}")
    print(f"  keys examined     : {fmt(a['keys_examined'])}")
    print(
        f"  explain time      : {fmt(a['execution_time_ms'], ' ms')} (explain time is not production latency)"
    )
    for f in a["findings"]:
        print(f"  [{f['severity']}] {f['message']}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default="http://127.0.0.1:8000")
    p.add_argument("--email", default="demo@example.com")
    p.add_argument("--password", default="Demo-password-123")
    p.add_argument("--orders", type=int, default=200_000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--iterations", type=int, default=200)
    p.add_argument("--yes", action="store_true", help="auto-approve the index creation")
    p.add_argument("--rollback", action="store_true", help="drop the created index afterwards")
    p.add_argument("--out", default="demo-output")
    args = p.parse_args()

    out = Path(args.out)
    out.mkdir(exist_ok=True)
    api = Api(args.api)
    api.login(args.email, args.password)

    print(f"Creating deterministic demo dataset ({args.orders:,} orders, seed {args.seed}) ...")
    setup = api.call(
        "POST", "/demo/setup", json={"orders": args.orders, "seed": args.seed, "confirm": True}
    ).json()
    iid = setup["instance"]["id"]
    db = setup["real_dataset"]["database"]
    print(f"  REAL data: {setup['real_dataset']}")
    print(f"  {setup['notice']}")
    version = setup["instance"].get("server_version")
    print(f"  MongoDB version: {version}")

    print("\n[1/6] BASELINE")
    base_plan = explain(api, iid, db)
    show_plan("baseline explain", base_plan)
    base = benchmark(api, iid, db, "baseline", args.iterations)
    print(
        f"  benchmark P95 {fmt(base['result']['latency_ms']['p95'], ' ms')}, "
        f"throughput {fmt(base['result']['throughput_ops'], ' ops/s')}"
    )

    print("\n[2/6] INVESTIGATION")
    rec = base_plan["recommendation"]
    print(f"  existing indexes: {[i['name'] for i in rec['existing_indexes']]}")
    if rec["action"] != "create_index":
        print(f"  No index recommended: {rec.get('reason')}")
        return
    print(f"  problem   : {rec['problem']}")
    print(
        f"  suggestion: {rec['suggested_index']['command']}   (confidence {rec['confidence']}, {rec['status']})"
    )
    print(f"  evidence  : {'; '.join(rec['supporting_evidence'])}")
    print(f"  {rec['expected_benefit']}")
    for c in rec["costs"]:
        print(f"  cost: {c}")

    print("\n[3/6] OPTIMIZATION (modifies the monitored database)")
    if not args.yes:
        ans = input(f"  Create index {rec['suggested_index']['key_doc']} on {db}.orders? Type 'yes': ")
        if ans.strip().lower() != "yes":
            print("  Not approved; stopping. Nothing was changed.")
            return
    keys = rec["suggested_index"]["keys"]
    change = api.call(
        "POST",
        f"/indexes/{iid}/create",
        json={
            "database": db,
            "collection": "orders",
            "keys": keys,
            "confirm": True,
            "acknowledge_write_cost": True,
        },
    ).json()["change"]
    print(f"  created index {change['index_name']}")

    print("\n[4/6] RE-TEST")
    after_plan = explain(api, iid, db)
    show_plan("explain after index", after_plan)
    after = benchmark(api, iid, db, "optimized", args.iterations)

    print("\n[5/6] COMPARE (measured on this machine, this run)")
    cmp_ = api.call(
        "POST", "/benchmarks/compare", json={"before_id": base["id"], "after_id": after["id"]}
    ).json()["comparison"]
    print(f"  {'metric':<20}{'before':>16}{'after':>16}{'improvement':>14}")
    for r in cmp_["rows"]:
        ch = "n/a" if r["change_pct"] is None else f"{r['change_pct']:+.1f}%"
        print(f"  {r['label']:<20}{fmt(r['before']):>16}{fmt(r['after']):>16}{ch:>14}")
    print(
        f"  plan: {' > '.join(cmp_['plan']['before_stages'])}  ->  {' > '.join(cmp_['plan']['after_stages'])}"
    )
    print(f"\n  VERDICT: {cmp_['verdict']} - {cmp_['summary']}")
    for c in cmp_["caveats"]:
        print(f"  note: {c}")

    print("\n[6/6] REPORT")
    rep = api.call(
        "POST",
        "/reports",
        json={
            "instance_id": iid,
            "period": "24h",
            "comparison_id": cmp_["id"],
            "title": "Optimizing orders.find({customer_id, status})",
            "include_synthetic": False,
        },
    ).json()["report"]
    for fmt_name in ("json", "csv", "pdf"):
        r = api.call("GET", f"/reports/{rep['id']}/download", params={"format": fmt_name})
        path = out / f"investigation-report.{fmt_name}"
        path.write_bytes(r.content)
        print(f"  wrote {path}")
    (out / "comparison.json").write_text(json.dumps(cmp_, indent=2), encoding="utf-8")

    if args.rollback:
        api.call("POST", f"/indexes/{iid}/rollback", json={"change_id": change["id"], "confirm": True})
        print("  index rolled back; database restored to its original state")


if __name__ == "__main__":
    main()
