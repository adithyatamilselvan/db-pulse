"""Playwright UI automation: the main investigation workflow, end to end, on REAL MongoDB data.

Tests in this module are ordered and share one browser session (like a real user session).
"""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

pytestmark = [pytest.mark.ui, pytest.mark.mongo]
EMAIL = "ui-admin@example.com"
PASSWORD = "Ui-Password-12345"


def test_protected_routes_redirect_to_login(ui):
    ui.goto("/alerts")
    expect(ui).to_have_url(re.compile(r"/login"))
    expect(ui.get_by_role("button", name="Sign in")).to_be_visible()


def test_login_rejects_bad_credentials_and_register_validates(ui):
    ui.goto("/login")
    ui.get_by_test_id("email").fill("nobody@example.com")
    ui.get_by_test_id("password").fill("wrong-password-1")
    ui.get_by_test_id("submit").click()
    expect(ui.get_by_test_id("auth-error")).to_contain_text("Invalid email or password")
    ui.get_by_role("tab", name="Register").click()
    ui.get_by_test_id("name").fill("UI Admin")
    ui.get_by_test_id("email").fill(EMAIL)
    ui.get_by_test_id("password").fill("short")
    ui.get_by_test_id("submit").click()  # blocked by minLength before any request
    expect(ui).to_have_url(re.compile(r"/login"))


def test_register_lands_on_dashboard_as_first_user_admin(ui):
    ui.goto("/login")
    ui.get_by_role("tab", name="Register").click()
    ui.get_by_test_id("name").fill("UI Admin")
    ui.get_by_test_id("email").fill(EMAIL)
    ui.get_by_test_id("password").fill(PASSWORD)
    ui.get_by_test_id("submit").click()
    # the app returns the user to the page they originally asked for (/alerts, from the first test)
    expect(ui.get_by_role("heading", name="Alerts center")).to_be_visible()
    ui.get_by_test_id("main-nav").get_by_role("link", name="Dashboard", exact=True).click()
    expect(ui.get_by_role("heading", name="Dashboard")).to_be_visible()
    expect(ui.get_by_test_id("user-name")).to_contain_text("admin")
    expect(ui.get_by_test_id("ai-mode")).to_contain_text("Local")
    expect(ui.get_by_test_id("empty-state").first).to_contain_text("Welcome to DB Pulse")


def test_logout_and_login_again(ui):
    ui.get_by_test_id("logout").click()
    expect(ui).to_have_url(re.compile(r"/login"))
    ui.get_by_test_id("email").fill(EMAIL)
    ui.get_by_test_id("password").fill(PASSWORD)
    ui.get_by_test_id("submit").click()
    expect(ui.get_by_role("heading", name="Dashboard")).to_be_visible()


def test_navigation_reaches_every_page(ui):
    for label, heading in [
        ("Database explorer", "Database explorer"),
        ("Slow queries", "Slow query monitor"),
        ("Execution plans", "Execution plan explorer"),
        ("Index investigator", "Index investigator"),
        ("Benchmark lab", "Benchmark laboratory"),
        ("Alerts", "Alerts center"),
        ("AI assistant", "AI investigation assistant"),
        ("Reports", "Reports"),
        ("Settings", "Settings"),
        ("Dashboard", "Dashboard"),
    ]:
        ui.get_by_test_id("main-nav").get_by_role("link", name=label).click()
        expect(ui.get_by_role("heading", name=heading, exact=True)).to_be_visible()


def test_register_database_with_connection_test(ui, ui_mongo_uri):
    ui.get_by_test_id("main-nav").get_by_role("link", name="Database explorer").click()
    ui.get_by_test_id("add-instance").click()
    ui.get_by_test_id("inst-name").fill("Local test MongoDB")
    ui.get_by_test_id("inst-uri").fill("mongodb://127.0.0.1:1/?directConnection=true")
    ui.get_by_test_id("test-connection").click()
    expect(ui.get_by_test_id("test-result")).to_contain_text(
        "Cannot reach"
    )  # unreachable server: clear error
    ui.get_by_test_id("register-instance").click()
    expect(ui.get_by_test_id("register-error")).to_be_visible()
    ui.get_by_test_id("inst-uri").fill(ui_mongo_uri)
    ui.get_by_label("Allow DB Pulse to modify this instance").check()
    ui.get_by_test_id("test-connection").click()
    expect(ui.get_by_test_id("test-result")).to_contain_text("Connected: MongoDB")
    ui.get_by_test_id("register-instance").click()
    row = ui.get_by_test_id("instance-row").filter(has_text="Local test MongoDB")
    expect(row).to_be_visible()
    expect(row).to_contain_text("connected")
    expect(ui.get_by_test_id("instance-select")).to_contain_text("Local test MongoDB")


def test_explorer_databases_collections_indexes(ui, orders_db):
    ui.reload()
    ui.get_by_test_id("db-list").get_by_role("button", name=re.compile(orders_db)).click()
    table = ui.get_by_test_id("collections-table")
    expect(table).to_contain_text("orders")
    expect(table).to_contain_text("60,000")
    table.get_by_text("orders", exact=True).click()
    expect(ui.get_by_text(f"Indexes on {orders_db}.orders")).to_be_visible()
    expect(ui.get_by_text("_id_").first).to_be_visible()


def test_configure_threshold_and_profiler_status_in_settings(ui, orders_db):
    ui.get_by_test_id("main-nav").get_by_role("link", name="Settings").click()
    form = ui.get_by_test_id("settings-form")
    form.get_by_label("Slow query threshold (ms)").fill("1")
    form.get_by_label("Monitored databases (comma-separated; empty = all non-system)").fill(orders_db)
    ui.get_by_test_id("save-settings").click()
    expect(ui.get_by_test_id("saved")).to_be_visible()
    ui.get_by_role("tab", name="Profiler").click()
    ui.get_by_test_id("profiler-db").select_option(orders_db)
    expect(ui.get_by_test_id("profiler-status")).to_contain_text("level 2")
    ui.get_by_role("tab", name="AI provider").click()
    expect(ui.get_by_text("Local rule-based").first).to_be_visible()


def test_slow_query_monitor_collect_filter_and_open_investigation(ui, shop_app, orders_db):
    for cid in (31, 32, 33, 31):
        list(shop_app[orders_db].orders.find({"customer_id": cid, "status": "COMPLETED"}))
    ui.get_by_test_id("main-nav").get_by_role("link", name="Slow queries").click()
    expect(
        ui.get_by_test_id("empty-state").first
    ).to_be_visible()  # nothing collected yet: honest empty state
    ui.get_by_test_id("collect-now").click()
    expect(ui.get_by_test_id("collect-result")).to_contain_text(f"{orders_db}:")
    rows = ui.get_by_test_id("slow-row")
    expect(rows.first).to_be_visible()
    assert rows.count() >= 4
    expect(rows.first).to_contain_text("COLLSCAN")
    ui.get_by_test_id("search").fill("no-such-thing-xyz")
    expect(ui.get_by_test_id("empty-state").first).to_be_visible()
    ui.get_by_test_id("search").fill("")
    ui.get_by_role("button", name=re.compile("Duration")).click()  # sort control works
    ui.get_by_role("tab", name=re.compile("Patterns")).click()
    expect(ui.get_by_test_id("pattern-table")).to_contain_text("orders")
    ui.get_by_test_id("pattern-table").locator("tbody tr").first.click()
    expect(ui.get_by_role("heading", name="Query investigation")).to_be_visible()


def test_query_investigation_runs_real_explain_and_shows_recommendation(ui):
    ui.get_by_test_id("run-explain").click()
    plan = ui.get_by_test_id("plan-view")
    expect(plan).to_be_visible()
    expect(plan.get_by_test_id("plan-stage-COLLSCAN")).to_be_visible()
    expect(plan.get_by_test_id("plan-metrics")).to_contain_text("60,000")
    expect(plan.get_by_test_id("findings")).to_contain_text("COLLSCAN")
    rec = ui.get_by_test_id("recommendation")
    expect(rec).to_contain_text("Unverified hypothesis")
    expect(ui.get_by_test_id("suggested-index")).to_contain_text("createIndex")
    ui.get_by_role("button", name="Why is this query slow?").click()
    answer = ui.get_by_test_id("ai-answer")
    expect(answer).to_be_visible()
    expect(answer.get_by_test_id("answer-mode")).to_contain_text("Local rule-based")
    expect(answer).to_contain_text("Observed evidence (measured)")
    expect(answer).to_contain_text("60000 documents examined")


def test_execution_plan_explorer_form_and_error_state(ui, orders_db):
    ui.get_by_test_id("main-nav").get_by_role("link", name="Execution plans").click()
    ui.get_by_test_id("db-select").select_option(orders_db)
    ui.get_by_test_id("coll-select").select_option("orders")
    ui.get_by_test_id("filter").fill('{ "customer_id": 5, "$where": "1" }')
    ui.get_by_test_id("run-explain").click()
    expect(ui.get_by_test_id("explain-error")).to_contain_text("not allowed")
    ui.get_by_test_id("filter").fill("{ not json")
    ui.get_by_test_id("run-explain").click()
    expect(ui.get_by_text("Filter:")).to_be_visible()
    ui.get_by_test_id("filter").fill('{ "customer_id": 5 }')
    ui.get_by_test_id("run-explain").click()
    expect(ui.get_by_test_id("plan-tree")).to_be_visible()
    expect(ui.get_by_role("heading", name="Raw explain output (unmodified)")).to_be_visible()
    expect(ui.get_by_test_id("json-view")).to_contain_text("executionStats")


def test_benchmark_baseline_then_index_then_compare(ui, orders_db):
    # --- baseline ---
    ui.get_by_test_id("main-nav").get_by_role("link", name="Benchmark lab").click()
    ui.get_by_test_id("db-select").select_option(orders_db)
    ui.get_by_test_id("coll-select").select_option("orders")
    ui.get_by_test_id("bench-filter").fill('{ "customer_id": 7, "status": "COMPLETED" }')
    ui.get_by_test_id("bench-label").fill("baseline")
    ui.get_by_test_id("bench-iterations").fill("40")
    ui.get_by_test_id("run-benchmark").click()
    result = ui.get_by_test_id("bench-result")
    expect(result).to_be_visible(timeout=60_000)
    expect(result).to_contain_text("P95")
    expect(result.get_by_test_id("latency-chart")).to_be_visible()
    expect(result.get_by_test_id("plan-stage-COLLSCAN").first).to_be_visible()

    # --- explicitly authorized optimization (Index investigator) ---
    ui.get_by_test_id("main-nav").get_by_role("link", name="Index investigator").click()
    ui.get_by_test_id("db-select").select_option(orders_db)
    ui.get_by_test_id("coll-select").select_option("orders")
    expect(ui.get_by_test_id("index-table")).to_contain_text("_id_")
    expect(ui.get_by_test_id("index-issues")).to_contain_text("No duplicate")
    ui.get_by_test_id("rec-filter").fill('{ "customer_id": 7, "status": "COMPLETED" }')
    ui.get_by_test_id("recommend").click()
    expect(ui.get_by_test_id("suggested-index")).to_contain_text("createIndex")
    ui.get_by_test_id("apply-index").click()
    confirm = ui.get_by_test_id("confirm-action")
    expect(confirm).to_be_disabled()  # cannot proceed without explicit acknowledgements
    ui.get_by_test_id("ack-modify").check()
    expect(confirm).to_be_disabled()
    ui.get_by_test_id("ack-cost").check()
    expect(confirm).to_be_enabled()
    confirm.click()
    expect(ui.get_by_test_id("index-table")).to_contain_text("customer_id_1_status_1")

    # --- re-test and compare ---
    ui.get_by_test_id("main-nav").get_by_role("link", name="Benchmark lab").click()
    ui.get_by_test_id("db-select").select_option(orders_db)
    ui.get_by_test_id("coll-select").select_option("orders")
    ui.get_by_test_id("bench-filter").fill('{ "customer_id": 7, "status": "COMPLETED" }')
    ui.get_by_test_id("bench-label").fill("with-index")
    ui.get_by_test_id("bench-iterations").fill("40")
    ui.get_by_test_id("run-benchmark").click()
    expect(ui.get_by_test_id("bench-result").get_by_test_id("plan-stage-IXSCAN").first).to_be_visible(
        timeout=60_000
    )
    history = ui.get_by_test_id("bench-history")
    expect(history).to_contain_text("baseline")
    history.get_by_label("Select baseline").check()
    history.get_by_label("Select with-index").check()
    ui.get_by_test_id("compare-btn").click()
    comp = ui.get_by_test_id("comparison")
    expect(comp).to_be_visible()
    expect(comp.get_by_test_id("verdict-text")).to_have_text(re.compile("IMPROVED|UNCHANGED|REGRESSED"))
    expect(comp.get_by_test_id("comparison-table")).to_contain_text("P99 latency")
    expect(comp.get_by_test_id("comparison-table")).to_contain_text("Documents examined")
    expect(comp.get_by_test_id("compare-chart")).to_be_visible()
    expect(comp.get_by_test_id("plan-stage-COLLSCAN")).to_be_visible()  # before plan
    expect(comp.get_by_test_id("plan-stage-IXSCAN")).to_be_visible()  # after plan


def test_alert_acknowledge_and_resolve(ui):
    ui.get_by_test_id("main-nav").get_by_role("link", name="Alerts").click()
    table = ui.get_by_test_id("alerts-table")
    expect(table).to_be_visible()
    first = ui.get_by_test_id("alert-row").first
    title = first.locator("button").first.inner_text()
    n_open = ui.get_by_test_id("alert-row").count()
    first.get_by_test_id("ack").click()
    expect(ui.get_by_test_id("alert-row")).to_have_count(n_open - 1)  # leaves the "open" filter
    ui.get_by_test_id("status-filter").select_option("acknowledged")
    expect(ui.get_by_test_id("alerts-table")).to_contain_text(title)
    ui.get_by_test_id("alert-row").first.get_by_test_id("resolve").click()
    ui.get_by_test_id("status-filter").select_option("resolved")
    expect(ui.get_by_test_id("alerts-table")).to_contain_text(title)
    ui.get_by_test_id("alert-row").first.locator("button").first.click()
    expect(ui.get_by_role("dialog")).to_contain_text("Supporting evidence")
    ui.keyboard.press("Escape")


def test_report_generation_and_downloads(ui, tmp_path):
    ui.get_by_test_id("main-nav").get_by_role("link", name="Reports").click()
    ui.get_by_test_id("report-title").fill("UI automation report")
    ui.get_by_test_id("generate-report").click()
    row = ui.get_by_test_id("report-row").filter(has_text="UI automation report")
    expect(row).to_be_visible()
    for fmt, magic in (("pdf", b"%PDF"), ("csv", b"# report"), ("json", b'"executive_summary"')):
        with ui.expect_download() as dl:
            row.get_by_test_id(f"download-{fmt}").click()
        path = tmp_path / f"r.{fmt}"
        dl.value.save_as(path)
        assert magic in path.read_bytes()[:400000], fmt


def test_ai_assistant_conversation_with_context(ui):
    ui.get_by_test_id("main-nav").get_by_role("link", name="AI assistant").click()
    expect(ui.get_by_test_id("assistant-mode")).to_contain_text("Local")
    ui.get_by_test_id("ctx-pattern").select_option(index=1)
    ui.get_by_test_id("question").fill("What should I investigate next?")
    ui.get_by_test_id("send").click()
    expect(ui.get_by_test_id("ai-answer").first).to_be_visible()
    expect(ui.get_by_test_id("user-msg")).to_contain_text("What should I investigate next?")
    ui.get_by_role("button", name="Why was COLLSCAN selected?").click()
    expect(ui.get_by_test_id("ai-answer")).to_have_count(2)
    # 2 conversations: the one started earlier from the Investigation page + this one
    expect(ui.get_by_test_id("conversation-list").locator("li")).to_have_count(2)


def test_theme_toggle_persists_and_dashboard_shows_real_data(ui):
    ui.get_by_test_id("theme-toggle").click()
    expect(ui.locator("html")).to_have_attribute("data-theme", "light")
    ui.reload()
    expect(ui.locator("html")).to_have_attribute("data-theme", "light")
    ui.get_by_test_id("theme-toggle").click()
    expect(ui.locator("html")).to_have_attribute("data-theme", "dark")
    ui.get_by_test_id("main-nav").get_by_role("link", name="Dashboard").click()
    expect(ui.get_by_test_id("trend-chart")).to_be_visible()
    expect(ui.get_by_test_id("synthetic-notice")).to_have_count(0)  # no synthetic data in this account


def test_disconnect_and_delete_instance(ui):
    ui.get_by_test_id("main-nav").get_by_role("link", name="Database explorer").click()
    row = ui.get_by_test_id("instance-row").first
    row.get_by_test_id("disconnect").click()
    expect(row).to_contain_text("disconnected")
    row.get_by_test_id("delete-instance").click()
    ui.get_by_test_id("confirm-action").click()
    expect(ui.get_by_text("No instances yet")).to_be_visible()
