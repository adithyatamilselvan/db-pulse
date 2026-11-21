"""Investigation report generation: JSON (canonical), CSV and PDF renderings of one dataset."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from bson import ObjectId
from pymongo.database import Database

from app.services.slow_query_service import pattern_stats
from app.utils.serialization import to_jsonable, utcnow

LIMITATIONS = [
    "Observations come from the MongoDB profiler, serverStatus and explain output and are only "
    "as complete as the profiler configuration and the user's permissions allow.",
    "Explain execution time is not necessarily representative of normal production latency.",
    "A collection scan is not automatically a problem; recommendations are heuristics until "
    "validated by a benchmark.",
    "$indexStats counters reset on restart; zero accesses does not prove an index is unused.",
    "Benchmark results vary with cache state, concurrent load, hardware and data distribution.",
    "Estimated benefits are labelled ESTIMATE and are not measurements.",
]


def build_report(
    db: Database,
    instance: dict,
    *,
    start: datetime,
    end: datetime,
    comparison_id: str | None = None,
    title: str | None = None,
    include_synthetic: bool | None = None,
) -> dict[str, Any]:
    iid = instance["_id"]
    if include_synthetic is None:
        include_synthetic = bool(instance.get("is_demo"))
    src = {} if include_synthetic else {"source": "observed"}
    window = {"$gte": start, "$lte": end}

    patterns = list(
        db.query_patterns.find({"instance_id": iid, "last_seen": {"$gte": start}, **src})
        .sort("total_ms", -1)
        .limit(20)
    )
    slow_findings = [
        {
            "fingerprint": p["fingerprint"],
            "collection": f"{p['database']}.{p['collection']}",
            "operation": p["operation"],
            "source": p.get("source"),
            **pattern_stats(p),
            "last_plan_summary": p.get("last_plan_summary"),
        }
        for p in patterns
    ]
    slow_count = db.slow_queries.count_documents({"instance_id": iid, "ts": window, **src})
    scan_count = db.slow_queries.count_documents(
        {"instance_id": iid, "ts": window, "collection_scan": True, **src}
    )
    plans = list(
        db.execution_plans.find({"instance_id": iid, "created_at": window}).sort("created_at", -1).limit(10)
    )
    plan_rows, recs = [], []
    for p in plans:
        a = p.get("analysis", {})
        plan_rows.append(
            {
                "id": str(p["_id"]),
                "target": p.get("target"),
                "created_at": p["created_at"],
                "stages": a.get("stage_names"),
                "documents_examined": a.get("documents_examined"),
                "documents_returned": a.get("documents_returned"),
                "keys_examined": a.get("keys_examined"),
                "execution_time_ms": a.get("execution_time_ms"),
                "findings": a.get("finding_messages", []),
            }
        )
        r = p.get("recommendation")
        if r and r.get("action") == "create_index":
            recs.append(
                {
                    "target": p.get("target"),
                    "command": r["suggested_index"]["command"],
                    "confidence": r["confidence"],
                    "status": r["status"],
                    "problem": r["problem"],
                    "expected_benefit": r["expected_benefit"],
                    "costs": r["costs"],
                    "validation_steps": r["validation_steps"],
                }
            )
    idx_analysis = list(db.index_analyses.find({"instance_id": iid}).sort("created_at", -1).limit(5))
    cmp_q: dict[str, Any] = {"instance_id": iid, "created_at": window}
    if comparison_id:
        cmp_q = {"_id": ObjectId(comparison_id), "instance_id": iid}
    comparisons = list(db.comparisons.find(cmp_q).sort("created_at", -1).limit(10))
    alerts = list(
        db.alerts.find({"instance_id": iid, "created_at": window, **src}).sort("created_at", -1).limit(200)
    )
    metrics = list(db.metrics.find({"instance_id": iid, "ts": window, **src}).sort("ts", 1))
    conns = [
        m["server"].get("connections_current")
        for m in metrics
        if m["server"].get("connections_current") is not None
    ]
    trends = {
        "snapshots": len(metrics),
        "connections_avg": (sum(conns) / len(conns)) if conns else None,
        "connections_max": max(conns) if conns else None,
    }
    sev_counts: dict[str, int] = {}
    for a in alerts:
        sev_counts[a["severity"]] = sev_counts.get(a["severity"], 0) + 1
    synth = any(x.get("source") == "SYNTHETIC" for x in [*slow_findings, *alerts, *comparisons])

    summary = [
        f"{slow_count} slow operation(s) recorded in the period, {scan_count} of them collection scans.",
        f"{len(alerts)} alert(s) raised"
        + (f" ({', '.join(f'{k}: {v}' for k, v in sev_counts.items())})" if sev_counts else "")
        + ".",
        f"{len(recs)} index recommendation(s) are pending validation (unverified hypotheses).",
    ]
    for c in comparisons[:3]:
        summary.append(
            f"Benchmark '{c['before_label']}' vs '{c['after_label']}': {c['verdict']} - {c['summary']}"
        )
    if synth:
        summary.append(
            "NOTE: this report includes SYNTHETIC demo records, which are not real MongoDB measurements."
        )
    report = {
        "title": title or f"Performance investigation - {instance['name']}",
        "generated_at": utcnow(),
        "period": {"start": start, "end": end},
        "includes_synthetic_data": synth,
        "database_info": {
            "instance": instance["name"],
            "server_version": instance.get("server_version"),
            "host": instance.get("host_display"),
            "demo": bool(instance.get("is_demo")),
        },
        "executive_summary": summary,
        "slow_query_findings": slow_findings,
        "query_execution_analysis": plan_rows,
        "execution_plan_details": plan_rows,
        "index_analysis": [
            {
                "database": i.get("database"),
                "collection": i.get("collection"),
                "issues": [x["message"] for x in i.get("issues", [])],
                "created_at": i["created_at"],
            }
            for i in idx_analysis
        ],
        "recommendations": recs,
        "benchmark_comparisons": [
            {
                "before": c["before_label"],
                "after": c["after_label"],
                "target": c["target"],
                "verdict": c["verdict"],
                "summary": c["summary"],
                "rows": c["rows"],
                "plan": c["plan"],
                "caveats": c["caveats"],
                "source": c.get("source"),
            }
            for c in comparisons
        ],
        "alert_history": [
            {
                "type": a["type"],
                "severity": a["severity"],
                "status": a["status"],
                "title": a["title"],
                "created_at": a["created_at"],
                "occurrences": a.get("occurrences", 1),
                "source": a.get("source"),
                "target": f"{a.get('database')}.{a.get('collection')}",
            }
            for a in alerts
        ],
        "performance_trends": trends,
        "limitations": LIMITATIONS,
    }
    return to_jsonable(report)


def to_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)

    def section(name: str, header: list[str], rows: list[list[Any]]) -> None:
        w.writerow([f"# {name}"])
        w.writerow(header)
        w.writerows(rows)
        w.writerow([])

    section(
        "report",
        ["title", "generated_at", "period_start", "period_end", "includes_synthetic"],
        [
            [
                report["title"],
                report["generated_at"],
                report["period"]["start"],
                report["period"]["end"],
                report["includes_synthetic_data"],
            ]
        ],
    )
    section(
        "slow_query_findings",
        ["fingerprint", "collection", "operation", "count", "avg_ms", "p95_ms", "p99_ms", "max_ms", "source"],
        [
            [
                r["fingerprint"],
                r["collection"],
                r["operation"],
                r["count"],
                r["avg_ms"],
                r["p95_ms"],
                r["p99_ms"],
                r["max_ms"],
                r["source"],
            ]
            for r in report["slow_query_findings"]
        ],
    )
    section(
        "execution_plans",
        ["target", "stages", "docs_examined", "docs_returned", "keys_examined", "exec_ms"],
        [
            [
                r["target"],
                " > ".join(r["stages"] or []),
                r["documents_examined"],
                r["documents_returned"],
                r["keys_examined"],
                r["execution_time_ms"],
            ]
            for r in report["execution_plan_details"]
        ],
    )
    section(
        "recommendations",
        ["target", "command", "confidence", "status"],
        [[r["target"], r["command"], r["confidence"], r["status"]] for r in report["recommendations"]],
    )
    rows = []
    for c in report["benchmark_comparisons"]:
        for m in c["rows"]:
            rows.append(
                [
                    c["before"],
                    c["after"],
                    m["label"],
                    m["before"],
                    m["after"],
                    m["unit"],
                    m["change_pct"],
                    m["direction"],
                    c["verdict"],
                ]
            )
    section(
        "benchmark_comparison",
        [
            "before",
            "after",
            "metric",
            "before_value",
            "after_value",
            "unit",
            "improvement_pct",
            "direction",
            "verdict",
        ],
        rows,
    )
    section(
        "alerts",
        ["created_at", "type", "severity", "status", "target", "occurrences", "source"],
        [
            [
                a["created_at"],
                a["type"],
                a["severity"],
                a["status"],
                a["target"],
                a["occurrences"],
                a["source"],
            ]
            for a in report["alert_history"]
        ],
    )
    return buf.getvalue()


def to_pdf(report: dict[str, Any]) -> bytes:
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["BodyText"], fontSize=8, leading=10)
    story: list[Any] = []

    def p(text: Any, style: str | ParagraphStyle = "BodyText") -> Paragraph:
        return Paragraph(escape(str(text)), styles[style] if isinstance(style, str) else style)

    def table(header: list[str], rows: list[list[Any]], widths: list[float] | None = None) -> Table:
        data = [header] + [[Paragraph(escape("" if c is None else str(c)), small) for c in r] for r in rows]
        t = Table(data, colWidths=widths, repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return t

    def fmt(v: Any) -> str:
        return "unavailable" if v is None else (f"{v:.2f}" if isinstance(v, float) else str(v))

    story += [
        p(report["title"], "Title"),
        p(
            f"Generated {report['generated_at']} | Period {report['period']['start']} to "
            f"{report['period']['end']}",
            small,
        ),
        Spacer(1, 6 * mm),
    ]
    if report["includes_synthetic_data"]:
        story += [
            p(
                "This report includes SYNTHETIC demo records. They are not real MongoDB measurements.",
                "BodyText",
            ),
            Spacer(1, 3 * mm),
        ]
    story.append(p("1. Executive summary", "Heading2"))
    story += [p(f"- {s}") for s in report["executive_summary"]]
    di = report["database_info"]
    story += [
        p("2. Database information", "Heading2"),
        p(f"Instance: {di['instance']} | Server version: {fmt(di['server_version'])}"),
    ]
    story += [
        p("3-4. Slow query findings", "Heading2"),
        table(
            ["Collection", "Op", "Count", "Avg ms", "P95", "Max", "Source"],
            [
                [
                    r["collection"],
                    r["operation"],
                    r["count"],
                    fmt(r["avg_ms"]),
                    fmt(r["p95_ms"]),
                    fmt(r["max_ms"]),
                    r["source"],
                ]
                for r in report["slow_query_findings"]
            ],
        )
        if report["slow_query_findings"]
        else p("No slow query patterns in this period."),
    ]
    story.append(p("5-6. Execution plans", "Heading2"))
    for r in report["execution_plan_details"]:
        story.append(
            p(
                f"{r['target']}: {' > '.join(r['stages'] or ['n/a'])} | examined "
                f"{fmt(r['documents_examined'])}, returned {fmt(r['documents_returned'])}, "
                f"keys {fmt(r['keys_examined'])}, {fmt(r['execution_time_ms'])} ms",
                small,
            )
        )
        story += [p(f"   * {m}", small) for m in r["findings"][:6]]
    story.append(p("7-8. Index analysis and recommendations", "Heading2"))
    for ia in report["index_analysis"]:
        story += [
            p(f"{ia['database']}.{ia['collection']}: " + ("; ".join(ia["issues"]) or "no issues"), small)
        ]
    for r in report["recommendations"]:
        story += [
            p(f"{r['target']}: {r['command']} (confidence {r['confidence']}, {r['status']})", small),
            p(f"   {r['expected_benefit']}", small),
        ]
    story.append(p("9. Benchmark comparisons", "Heading2"))
    for c in report["benchmark_comparisons"]:
        story += [
            p(f"{c['before']} vs {c['after']} on {c['target']}: {c['verdict']}"),
            p(c["summary"], small),
            table(
                ["Metric", "Before", "After", "Change (improvement)", "Result"],
                [
                    [
                        m["label"],
                        fmt(m["before"]),
                        fmt(m["after"]),
                        "n/a" if m["change_pct"] is None else f"{m['change_pct']:.1f}%",
                        m["direction"],
                    ]
                    for m in c["rows"]
                ],
            ),
        ]
        story += [p(f"* {cv}", small) for cv in c["caveats"]]
    story.append(p("10. Alert history", "Heading2"))
    story.append(
        table(
            ["Time", "Severity", "Type", "Target", "Status", "x"],
            [
                [a["created_at"][:19], a["severity"], a["type"], a["target"], a["status"], a["occurrences"]]
                for a in report["alert_history"][:60]
            ],
        )
        if report["alert_history"]
        else p("No alerts in this period.")
    )
    t = report["performance_trends"]
    story += [
        p("11. Performance trends", "Heading2"),
        p(
            f"{t['snapshots']} metric snapshots; connections avg {fmt(t['connections_avg'])}, "
            f"max {fmt(t['connections_max'])}"
        ),
        p("12. Limitations and validation notes", "Heading2"),
    ]
    story += [p(f"- {x}", small) for x in report["limitations"]]
    out = io.BytesIO()
    SimpleDocTemplate(
        out,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title=report["title"],
    ).build(story)
    return out.getvalue()
