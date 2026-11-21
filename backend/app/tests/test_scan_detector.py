from __future__ import annotations

from app.analysis.plan_parser import parse_explain
from app.analysis.scan_detector import analyze_plan, detect_findings, max_severity
from app.tests.test_plan_parser import load


def codes(name: str, **kw) -> dict[str, str]:
    return {f["code"]: f["severity"] for f in analyze_plan(load(name), **kw)["findings"]}


def test_large_selective_collscan_is_high_severity():
    c = codes("find_collscan", collection_doc_count=30000)
    assert c["COLLSCAN"] in ("MEDIUM", "HIGH") and "HIGH_EXAMINED_TO_RETURNED" in c


def test_collscan_on_small_collection_is_informational_only():
    c = codes("find_collscan", collection_doc_count=200)
    assert c.get("COLLSCAN_SMALL_COLLECTION") == "INFO" and "COLLSCAN" not in c


def test_collscan_returning_most_documents_is_not_flagged_as_problem():
    p = parse_explain(load("find_collscan"))
    p["documents_returned"], p["documents_examined"] = 29000, 30000
    p["efficiency_ratio"] = 30000 / 29000
    f = {x["code"]: x["severity"] for x in detect_findings(p, collection_doc_count=30000)}
    assert f["COLLSCAN_LOW_SELECTIVITY"] == "INFO" and "COLLSCAN" not in f


def test_good_index_plan_has_no_warnings():
    a = analyze_plan(load("find_ixscan_compound"))
    assert a["max_severity"] == "INFO"
    assert a["finding_messages"][0].startswith("Index scan used")


def test_max_severity_ordering():
    assert max_severity([{"severity": "LOW"}, {"severity": "HIGH"}, {"severity": "INFO"}]) == "HIGH"
    assert max_severity([]) == "INFO"


def test_output_has_the_documented_structure():
    a = analyze_plan(load("find_collscan"), collection_doc_count=30000)
    for key in (
        "collection_scan_detected",
        "index_scan_detected",
        "documents_examined",
        "documents_returned",
        "keys_examined",
        "execution_time_ms",
        "efficiency_ratio",
        "finding_messages",
    ):
        assert key in a
