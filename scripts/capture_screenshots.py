"""Capture README screenshots from a RUNNING DB Pulse (backend + built frontend).

Run scripts/demo_optimization.py and scripts/generate_demo_workload.py first so the pages show
real data (profiled queries, a real before/after benchmark comparison, plans, alerts).

    python scripts/capture_screenshots.py --web http://127.0.0.1:4173 --demo-db dbpulse_demo
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--web", default="http://127.0.0.1:5173")
    p.add_argument("--email", default="demo@example.com")
    p.add_argument("--password", default="Demo-password-123")
    p.add_argument("--demo-db", default="dbpulse_demo")
    p.add_argument("--out", default="docs/screenshots")
    args = p.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(base_url=args.web, viewport={"width": 1440, "height": 900})
        ctx.set_default_timeout(30_000)
        page = ctx.new_page()

        def shot(name: str, full: bool = False) -> None:
            # The app scrolls inside <main>, so "full page" = a taller viewport, scrolled to the top.
            if full:
                page.set_viewport_size({"width": 1440, "height": 2200})
            page.wait_for_timeout(900)
            page.evaluate("document.querySelector('main')?.scrollTo(0, 0)")
            page.wait_for_timeout(200)
            page.screenshot(path=str(out / f"{name}.png"))
            if full:
                page.set_viewport_size({"width": 1440, "height": 900})
            print("captured", name)

        def nav(label: str) -> None:
            page.get_by_test_id("main-nav").get_by_role("link", name=label, exact=True).click()

        page.goto("/login")
        shot("00-login")
        page.get_by_test_id("email").fill(args.email)
        page.get_by_test_id("password").fill(args.password)
        page.get_by_test_id("submit").click()
        # the demo instance has both real profiled queries and labelled SYNTHETIC history
        expect(page.get_by_test_id("trend-chart")).to_be_visible()
        page.wait_for_timeout(1200)
        shot("01-dashboard")

        nav("Database explorer")
        page.get_by_test_id("db-list").get_by_role("button", name=re.compile(args.demo_db)).click()
        page.get_by_test_id("collections-table").get_by_text("orders", exact=True).click()
        expect(page.get_by_text(f"Indexes on {args.demo_db}.orders")).to_be_visible()
        shot("02-explorer", full=True)

        nav("Slow queries")
        page.get_by_test_id("collect-now").click()
        expect(page.get_by_test_id("slow-row").first).to_be_visible()
        shot("03-slow-queries")

        page.get_by_role("tab", name=re.compile("Patterns")).click()
        # open a REAL (non-synthetic) pattern: those can be explained
        rows = page.get_by_test_id("pattern-table").locator("tbody tr")
        expect(rows.first).to_be_visible()
        for i in range(rows.count()):
            if "synthetic" not in rows.nth(i).inner_text().lower():
                rows.nth(i).click()
                break
        expect(page.get_by_role("heading", name="Query investigation")).to_be_visible()
        page.get_by_test_id("run-explain").click()
        expect(page.get_by_test_id("recommendation")).to_be_visible()
        page.get_by_role("button", name="Why is this query slow?", exact=True).first.click()
        expect(page.get_by_test_id("ai-answer")).to_be_visible()
        shot("04-investigation", full=True)

        nav("Execution plans")
        page.get_by_test_id("db-select").select_option(args.demo_db)
        page.get_by_test_id("coll-select").select_option("orders")
        page.get_by_test_id("run-explain").click()
        expect(page.get_by_test_id("plan-tree")).to_be_visible()
        shot("05-execution-plan", full=True)

        nav("Index investigator")
        page.get_by_test_id("db-select").select_option(args.demo_db)
        page.get_by_test_id("coll-select").select_option("orders")
        page.get_by_test_id("rec-filter").fill('{ "customer_id": 1001, "status": "COMPLETED" }')
        page.get_by_test_id("recommend").click()
        expect(page.get_by_test_id("suggested-index")).to_be_visible()
        shot("06-indexes", full=True)

        nav("Benchmark lab")
        history = page.get_by_test_id("bench-history")
        expect(history).to_be_visible()
        labels = ["baseline", "optimized"]
        for lab in labels:
            history.get_by_label(f"Select {lab}").first.check()
        page.get_by_test_id("compare-btn").click()
        expect(page.get_by_test_id("verdict")).to_be_visible()
        shot("07-benchmark-comparison", full=True)

        nav("Alerts")
        expect(page.get_by_test_id("alert-row").first).to_be_visible()
        shot("08-alerts")

        nav("AI assistant")
        page.get_by_test_id("ctx-pattern").select_option(index=1)
        page.get_by_role("button", name="Could an index improve this query?", exact=True).last.click()
        expect(page.get_by_test_id("ai-answer").first).to_be_visible()
        shot("09-ai-assistant", full=True)

        nav("Reports")
        page.get_by_test_id("generate-report").click()
        expect(page.get_by_test_id("report-row").first).to_be_visible()
        shot("10-reports")

        nav("Settings")
        expect(page.get_by_test_id("settings-form")).to_be_visible()
        shot("11-settings", full=True)
        page.get_by_test_id("theme-toggle").click()
        nav("Dashboard")
        page.wait_for_timeout(1500)
        shot("12-dashboard-light")
        browser.close()


if __name__ == "__main__":
    main()
