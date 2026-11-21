# Testing strategy

Principle: **test against real MongoDB**, never fabricate explain output. Isolation: every run creates uniquely named `dbpulse_test_<id>_*` databases and a throw-away internal DB, and drops them afterwards.

| Layer | Location | What |
|---|---|---|
| Unit / regression | `backend/app/tests` | plan parser against **recorded real explain output** (MongoDB 8.2.1, `fixtures/`, regenerate with `scripts/capture_explain_fixtures.py`), scan detector, fingerprinting, ESR recommendations, index analysis, stats (percentiles vs numpy), comparison polarity, query validation, redaction |
| API | `automation/api_tests` | auth, instances (multi-instance, invalid URI, unreachable, bad credentials), monitoring, profiler controls, benchmarks (limits, cancel, errors, high-load), alerts, AI (local, stub provider, error fallbacks, mocked Cohere SDK), reports, demo |
| Database | `automation/database_tests` | stored records, indexes, encryption at rest, referential consistency, dedupe/cooldown, retention, permission-failure handling |
| Integration | `automation/integration_tests` | profiler → collector → detector → explain → recommendation → alert; before/after optimization workflow; worker tasks/scheduler |
| Security | `automation/security_tests` | see SECURITY.md |
| UI | `automation/ui_tests` | 17 Playwright tests: login/register/logout, route guards, every page, instance registration, explorer, slow queries, investigation, plans, index creation with confirmation, benchmark baseline→index→compare, alert ack/resolve, report downloads, AI chat, theme |
| Performance | `automation/performance_tests` | Locust: dashboard, slow-query/pattern/alert history, multi-session login, benchmark workload |

## Last recorded results (development machine)

- `pytest`: **all passing** (unit 91 + API/DB/integration/security ≈ 105, plus UI 17); `ruff check` clean; frontend `tsc` + `eslint` clean.
- Locust (20 users, 40 s, local MongoDB, 5k-order demo): **1,526 requests, 0 failures**, ~39 req/s, median 14 ms, P95 44 ms, P99 190 ms (login ≈ 210 ms due to Argon2). Small-scale, single machine — indicative only.
- Bugs found *by these tests* and fixed: profiler `op:"query"` fingerprint mismatch; `None` optionals in benchmark config; synthetic/real pattern index collision and evidence leakage; pattern-detail ambiguity; connectivity outages masked as "unavailable metrics".

## Running

`pytest` (all) · `pytest backend/app/tests` (no DB) · `pytest automation/ui_tests` · see README for Locust. Run Locust only against a disposable backend started with raised rate limits.

## Not covered / honest gaps

Docker Compose stack and live Cohere API were not executed; no tests against auth-enabled or sharded MongoDB (permission failures are simulated with stubs); CommandListener integration is not implemented (documented limitation in README).
