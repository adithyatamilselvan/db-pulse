# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| API exits at startup with a validation error | `.env` missing or `SECRET_KEY` < 32 chars / bad `ENCRYPTION_KEY`. Run `python scripts/init_env.py`. |
| `Address already in use` (port 8000) | Another process owns it. Use `--port` and start the frontend with `VITE_API_PROXY=http://127.0.0.1:<port>`. |
| UI shows "Cannot reach the DB Pulse API" | Backend not running / wrong proxy target. |
| Tests skip with "Test MongoDB instances are not running" | Run `scripts/dev_mongo.ps1` (ports 27117/27118). |
| "Profiling is off and system.profile does not exist" | Expected until profiling is enabled (Settings → Profiler, admin + "allow modifications"), or enable it yourself with `db.setProfilingLevel(1, {slowms: 100})`. |
| Profiler collected nothing | Queries are below the instance threshold (Settings → slow threshold), or came from an app named `dbpulse*` (DB Pulse excludes its own traffic). |
| "not authorized" messages | The MongoDB user lacks a privilege; see the permissions table in the README. Affected metrics show "—". |
| "Stored credential cannot be decrypted" | `ENCRYPTION_KEY` changed. Re-register the instance. |
| Index creation refused | Needs admin role, instance "allow modifications", and both confirmation checkboxes. |
| Benchmark rejected as high load | Tick the high-load confirmation, or lower iterations/concurrency. |
| Cohere answers say "Showing the local rule-based analysis" | Read the reason shown (invalid key, rate limit, wrong `COHERE_MODEL`, service error). The app keeps working. |
| 429 Too Many Requests | Rate limit; raise `RATE_LIMIT_PER_MINUTE` / `AUTH_RATE_LIMIT_PER_MINUTE` for load tests only. |
| Playwright tests fail to start | `playwright install chromium`, ensure Node on PATH and `npm install` in `frontend/`. |
| `docker compose` problems | The Compose stack was not executed during development; check `docker compose logs backend worker`. |
