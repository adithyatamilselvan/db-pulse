# Security

| Control | Implementation | Test |
|---|---|---|
| Password storage | Argon2id, per-hash salt | `test_passwords_are_argon2_hashed_and_salted` |
| Auth | JWT (HS256), expiry, subject re-checked against DB on every request (role in token is ignored) | forged/expired/`alg:none`/escalation tests |
| Authorization | admin vs user; resources scoped to owner; foreign resources return 404 | `test_users_cannot_reach_each_others_instances_or_data` |
| Secrets | `SECRET_KEY`/`ENCRYPTION_KEY` from env, validated at startup; `.env` git-ignored | `test_config_validation_fails_fast` |
| Credential storage | Fernet-encrypted URIs; API projection whitelist; log scrub filter; error messages sanitized | secret-exposure tests |
| Query privacy | values redacted before storage; AI gets shape-only evidence; raw samples encrypted | AI prompt + record tests |
| Safe operations | read-only explain/benchmark; JS & write operators blocked; bounded depth/size/concurrency/duration/result size; high-load confirmation | validation & benchmark tests |
| Monitored-DB changes | admin + instance opt-in + explicit confirm flags; audit trail; only DB Pulse-created indexes droppable | safeguard tests |
| Abuse | in-memory sliding-window rate limit (stricter for auth); security headers; CORS allow-list | rate limit / header / CORS tests |
| Errors | uniform envelope; 500s are generic | `test_unhandled_errors_do_not_leak_internals` |

**Prompt injection**: database-derived text is wrapped as DATA with explicit instructions to ignore embedded commands; model output is parsed as JSON and only displayed — never executed.

**Known limits / recommendations**: tokens are stateless (no server-side revocation; keep expiry short); the in-memory limiter is per-process; the connection-string field lets an authenticated user make the server connect to arbitrary hosts (SSRF-like) — restrict who can register instances and network-egress in production; terminate TLS in front of the API; use read-only MongoDB users for monitoring; rotate `ENCRYPTION_KEY` by re-registering instances (changing it makes stored URIs undecryptable, which is reported clearly).
