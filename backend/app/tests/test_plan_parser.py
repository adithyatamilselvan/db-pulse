"""Plan parser tests against REAL explain output recorded from MongoDB (see fixtures/)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bson import json_util

from app.analysis.plan_parser import parse_explain, parse_plan_summary_string
from app.analysis.scan_detector import analyze_plan

FIX = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json_util.loads((FIX / f"explain_{name}.json").read_text(encoding="utf-8"))


def raw_stats(doc: dict) -> dict:
    return doc.get("executionStats") or doc["stages"][0]["$cursor"]["executionStats"]


def test_collscan_matches_server_reported_numbers():
    doc = load("find_collscan")
    p = parse_explain(doc)
    es = raw_stats(doc)
    assert p["parsed_ok"] and p["collection_scan_detected"] and not p["index_scan_detected"]
    assert p["documents_examined"] == es["totalDocsExamined"] == 30000
    assert p["documents_returned"] == es["nReturned"]
    assert p["keys_examined"] == es["totalKeysExamined"] == 0
    assert p["execution_time_ms"] == es["executionTimeMillis"]
    assert p["efficiency_ratio"] == pytest.approx(30000 / es["nReturned"])


def test_compound_index_plan_detects_ixscan_and_index_name():
    p = parse_explain(load("find_ixscan_compound"))
    assert p["index_scan_detected"] and not p["collection_scan_detected"]
    assert p["index_names"] == ["customer_id_1_status_1"]
    assert p["fetch_detected"]
    assert p["documents_examined"] == p["documents_returned"]  # perfectly selective index
    assert "IXSCAN" in p["plan_summary"]


def test_covered_query_examines_zero_documents():
    p = parse_explain(load("find_covered"))
    assert p["index_scan_detected"] and p["documents_examined"] == 0
    assert p["keys_examined"] > 0


def test_sort_without_index_flags_sort_stage():
    doc = load("find_sort_no_index")
    assert parse_explain(doc)["sort_stage_detected"]
    assert "IN_MEMORY_SORT" in {f["code"] for f in analyze_plan(doc)["findings"]}


def test_count_explain_is_parsed():
    p = parse_explain(load("count_collscan"))
    assert p["parsed_ok"] and p["collection_scan_detected"]


def test_aggregation_explain_is_parsed():
    for name in ("aggregate_collscan", "aggregate_indexed"):
        p = parse_explain(load(name))
        assert p["parsed_ok"], name
        assert p["documents_returned"] is not None
    assert parse_explain(load("aggregate_collscan"))["collection_scan_detected"]
    assert parse_explain(load("aggregate_indexed"))["index_scan_detected"]


def test_or_query_parses():
    p = parse_explain(load("find_or"))
    assert p["parsed_ok"]
    assert p["stage_names"].count("IXSCAN") + p["stage_names"].count("COLLSCAN") >= 1


def test_empty_result_does_not_divide_by_zero():
    p = parse_explain(load("find_empty_result"))
    assert p["documents_returned"] == 0
    analyze_plan(load("find_empty_result"))  # must not raise


@pytest.mark.parametrize(
    "garbage",
    [None, {}, {"foo": 1}, [], "x", {"queryPlanner": {}}, {"queryPlanner": {"winningPlan": "oops"}}],
)
def test_unsupported_or_missing_explain_never_raises(garbage):
    assert parse_explain(garbage)["parsed_ok"] is False
    assert analyze_plan(garbage)["findings"][0]["code"] == "PLAN_UNPARSEABLE"


def test_query_planner_only_output_has_no_invented_numbers():
    doc = load("find_collscan")
    doc.pop("executionStats")
    p = parse_explain(doc)
    assert p["parsed_ok"] and p["has_execution_stats"] is False
    assert p["documents_examined"] is None and p["documents_returned"] is None
    assert "NO_EXECUTION_STATS" in {f["code"] for f in analyze_plan(doc)["findings"]}


def test_sbe_style_wrapper_and_stage_names_are_canonicalized():
    sbe = {
        "queryPlanner": {
            "namespace": "d.c",
            "winningPlan": {
                "isCached": False,
                "queryPlan": {
                    "stage": "FETCH",
                    "inputStage": {"stage": "IXSCAN", "indexName": "a_1", "keyPattern": {"a": 1}},
                },
                "slotBasedPlan": {"stages": "..."},
            },
            "rejectedPlans": [],
        },
        "executionStats": {
            "nReturned": 1,
            "executionTimeMillis": 1,
            "totalKeysExamined": 1,
            "totalDocsExamined": 1,
            "executionStages": {
                "stage": "project",
                "inputStage": {"stage": "ixseek", "indexName": "a_1", "keysExamined": 1},
            },
        },
    }
    p = parse_explain(sbe)
    assert p["engine"] == "sbe" and p["index_scan_detected"]
    assert p["stage_names"] == ["project", "IXSCAN"]


def test_sharded_shards_array_is_traversed():
    doc = {
        "queryPlanner": {
            "winningPlan": {
                "stage": "SHARD_MERGE",
                "shards": [
                    {"shardName": "a", "winningPlan": {"stage": "COLLSCAN"}},
                    {"shardName": "b", "winningPlan": {"stage": "FETCH", "inputStage": {"stage": "IXSCAN"}}},
                ],
            }
        }
    }
    p = parse_explain(doc)
    assert p["collection_scan_detected"] and p["index_scan_detected"]


def test_rejected_plans_are_reported():
    doc = load("find_ixscan_compound")
    doc["queryPlanner"]["rejectedPlans"] = [{"stage": "COLLSCAN"}]
    p = parse_explain(doc)
    assert p["rejected_plans_count"] == 1 and p["rejected_plans"][0]["stages"] == ["COLLSCAN"]


def test_result_is_json_serialisable():
    json.dumps(parse_explain(load("find_ixscan_compound")), default=str)


def test_plan_summary_string():
    assert parse_plan_summary_string("COLLSCAN")["collection_scan_detected"]
    r = parse_plan_summary_string("IXSCAN { a: 1 }")
    assert r["index_scan_detected"] and not r["collection_scan_detected"]
    assert parse_plan_summary_string(None)["plan_summary"] == ""
