# API documentation

Interactive docs: `http://localhost:8000/docs` · machine-readable: [openapi.json](openapi.json) (57 paths). Base path `/api/v1`. Auth: `Authorization: Bearer <JWT>` from `/auth/login` or `/auth/register`.

**Error envelope** (all errors): `{"error": {"code": "...", "message": "...", "details": {}}}` — codes: `validation_failed` 422, `unauthorized` 401, `forbidden` 403, `not_found` 404 (also for other users' resources), `conflict` 409, `connection_failed` 502, `unsupported` 422, `rate_limited` 429 (+`Retry-After`), `internal_error` 500 (generic, no internals).

| Area | Endpoints |
|---|---|
| auth | `POST /auth/register`, `/auth/login`, `/auth/logout`; `GET/PATCH /auth/me` |
| databases | `POST /databases/test`; `POST/GET /databases`; `GET/PATCH/DELETE /databases/{id}`; `POST /{id}/check\|connect\|disconnect`; `GET/PUT /{id}/settings`; explorer: `GET /{id}/explorer`, `/explorer/{db}/collections`, `/explorer/{db}/{coll}/indexes` |
| monitoring | `POST /monitoring/{id}/collect\|collect-slow\|cleanup`; `GET /{id}/metrics?period=15m\|1h\|24h\|7d\|30d\|custom`; `GET/POST /{id}/profiler`, `POST /{id}/profiler/restore`; `GET /monitoring/dashboard` |
| queries | `GET /queries/slow` (filters, sort, paging), `GET /queries/patterns`, `GET /queries/patterns/{inst}/{fp}?source=`, `POST …/investigate` |
| execution-plans | `POST /execution-plans/{inst}/explain`, `GET /execution-plans`, `GET /execution-plans/{id}` |
| indexes | `GET /indexes/{inst}/{db}/{coll}`, `POST /recommend`, `POST /create` (admin, confirm), `POST /rollback` (admin, confirm), `GET /changes` |
| benchmarks | `POST /benchmarks/{inst}/validate\|run`, `GET /benchmarks`, `GET /benchmarks/{id}`, `POST /{id}/cancel`, `POST /compare`, `GET /comparisons[/{id}]` |
| alerts | `GET /alerts`, `GET /alerts/summary`, `GET /alerts/{id}`, `POST /{id}/acknowledge\|resolve` |
| ai | `GET /ai/status`, `POST /ai/investigate`, `GET /ai/conversations[/{id}]` |
| reports | `POST /reports`, `GET /reports[/{id}]`, `GET /reports/{id}/download?format=pdf\|csv\|json` |
| demo | `POST /demo/setup`, `POST /demo/reset` (admin, confirm) |
| health | `GET /health`, `GET /health/ready` |

## Examples

```bash
TOKEN=$(curl -s localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"me@example.com","password":"..."}' | jq -r .access_token)
curl -s localhost:8000/api/v1/execution-plans/$INSTANCE/explain -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"database":"shop","collection":"orders","filter":{"customer_id":1001,"status":"COMPLETED"}}'
```

Query specs accept filters as JSON objects or extended-JSON strings. Rejected: `$where`, `$function`, `$accumulator`, `$out`, `$merge`, `$lookup`, `$unionWith`, `$facet`, `$graphLookup`, admin/local/config databases, `system.*` collections, nesting > 12, > 20k chars.
