# Database design (internal MongoDB)

Monitored databases are never mixed with these collections. All monitoring-scoped documents carry `instance_id` and `owner_id`; records carry `source` (`observed` | `SYNTHETIC`).

| Collection | Purpose | Notable indexes |
|---|---|---|
| `users` | email, Argon2 hash, role, active | unique `email` |
| `instances` | name, **`uri_enc`** (Fernet), host (no creds), status, `allow_modifications`, `enabled`, `settings`, `checkpoints` (per-DB profiler cursor), `is_demo` | unique `(owner_id, name)` |
| `metrics` | timestamped snapshots: server stats, rates, op latencies, per-DB stats, `unavailable[]` | `(instance_id, ts desc)` |
| `slow_queries` | one record per slow op: redacted command, shape, duration, docs/keys examined, plan summary, severity | unique `(instance_id, dedupe_key)`, `(instance_id, ts)`, `(instance_id, fingerprint)` |
| `query_patterns` | per-fingerprint aggregates, recent durations, **`sample_enc`** (encrypted latest raw command, server-side use only) | unique `(instance_id, fingerprint, source)` |
| `execution_plans` | parsed analysis + findings + recommendation + **`raw_explain_enc`** | `(instance_id, created_at)` |
| `index_analyses` | index issues snapshots | `(instance_id, created_at)` |
| `alerts` | type, severity, status lifecycle, evidence, `dedupe_key`, `occurrences` | `(instance_id, dedupe_key, status)` |
| `benchmarks` | config, status, result (latency stats, samples, histogram, plan), methodology | `(instance_id, created_at)` |
| `comparisons` | before/after rows, plan change, verdict, caveats | `(owner_id, created_at)` |
| `reports` | generated report content (JSON) | `(owner_id, created_at)` |
| `investigations` | AI conversations | `(owner_id, updated_at)` |
| `index_changes`, `profiler_changes` | audit trail of every change DB Pulse made to a monitored DB (enables rollback) | `(instance_id, created_at)` |

**Retention**: per-instance `retention_days` removes old metrics, slow queries, plans and resolved alerts (hourly in the worker, or on demand).
**Privacy**: no literal query values at rest except the encrypted pattern sample and encrypted raw explain.
Referential consistency (no orphans; cascade on instance delete) is covered by `automation/database_tests`.
