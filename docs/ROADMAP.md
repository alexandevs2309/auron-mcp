# AURON MCP Roadmap

## M0 — Foundation

- [x] MCP server skeleton
- [x] semantic tools
- [x] resource
- [x] prompt
- [x] policy abstraction
- [x] tenant isolation guard
- [x] audit abstraction
- [x] mock adapter
- [x] HTTP adapter
- [x] tests
- [x] Dockerfile

## M0.5 — Hardening pass (this revision)

- [x] Real role -> permission table (`rbac.py`); unknown roles fail closed
- [x] `require_same_tenant` actually on the request path, not just unit-tested
- [x] Per-(user, tool) rate limiting
- [x] Audit persists to JSONL, not just stdout; denials are audited too
- [x] Adapter/policy/rate-limit errors normalized before reaching the model
      (no upstream response bodies, tokens, or stack traces leak out)
- [x] HTTP adapter sends tenant via header + param, with an explicit note
      that the upstream API must independently enforce it
- [x] Integration test that exercises the real `mcp`-wired `server.py`,
      not just the isolated `AuronTools` class
- [x] `docs/CONNECTING.md` — example host config

## M1 — Real AURON identity (AURON MCP v1)

- [x] Authenticate against the real AURON API (Bearer token or the httpOnly
      auth cookies issued by `/api/auth/login/`)
- [x] Map authenticated principal to AURON user via `GET /api/auth/verify/`
- [x] Resolve tenant from trusted identity via `GET /api/tenants/current/`
- [x] Fail-closed RBAC mirror (`policy/roles.py`) over AURON's canonical roles,
      with AURON's own enforcement as ground truth
- [x] Remove development identity (`AURON_DEV_*`, `development_context`)
- [x] Operator CLI (`auron-manage login/status/clean`) + session cookie store
- [x] Error boundary: missing/expired session → clean `unauthorized` payload
- [x] Add real trace propagation (from the incoming MCP request/session, not
      a per-call UUID generated in `_context()`)

## M2 — Real Suite integration

- [x] Sales
- [x] Appointments
- [x] Clients
- [x] Employees
- [x] Services
- [x] Products
- [x] Inventory
- [x] Reports
- [x] Branches
- [x] Test that the upstream AURON API rejects a mismatched token/tenant
      pair on its own, independent of what this server sends
      (`scripts/live_isolation.py`)

## M3 — Ecosystem

- [x] AURON ECF — fiscal reads wired (`auron_ncf_sequences`, `auron_fiscal_summary`,
      `auron_sale_fiscal_detail`), NCF lifecycle mapped, `ecf.py` domain types
      (`NCFInfo`, `FiscalDaySummary`, `SaleFiscalDetail`, `ECFTemplate`,
      `ECFSubmissionResult`). DGII e-CF submission blocked on backend
      implementation; interfaces prepared.
- [ ] Hospitality — deferred until the backend implements the module.
- [ ] Future products — deferred.

## M4 — Controlled writes

- [x] Create/update/cancel tools (appointments batch: `auron_create_appointment`,
      `auron_cancel_appointment`, `auron_update_appointment`,
      `auron_reschedule_appointment`; backend contract `AppointmentViewSet` —
      POST create, PATCH update, POST `{pk}/reschedule/`, cancel via
      `status: cancelled`; also `auron_preview_create_appointment` /
      `auron_preview_cancel_appointment` as phase 1)
- [x] Idempotency (`IdempotencyStore`: success-only records keyed by a
      client-supplied `idempotency_key`, TTL 24h, bounded; a replay returns the
      original response, a failed write never poisons the key)
- [x] Confirmation for high-impact actions (`ConfirmationTokenStore`: short-TTL
      single-use tokens bound to operation + canonical payload hash; create and
      cancel require a token issued by their preview tool; update/reschedule are
      low-impact and need only idempotency)
- [x] Fail-closed read-only guard (server refuses every write while
      `AURON_MCP_READ_ONLY` is on — the shipped default)
- [x] Rollback/compensation strategies (best-effort `compensation` hook in the
      write boundary, exercised by `tests/test_writes.py`)
- [x] Audit sink abstraction (`AuditSink` ABC in `audit/logger.py`;
      `AuditLogger` = current JSONL+stdout; `CentralAuditSink` = stub ready
      for `POST /api/audit/logs/create/` when the backend drops the
      `IsSuperAdmin` requirement)
- [x] Rate-limit backend abstraction (`RateLimitBackend` ABC in `ratelimit.py`;
      `InMemoryBackend` = current per-process; `RedisBackend` = stub ready
      for Redis sorted-set sliding window when the deployment has Redis)
- [ ] Replace JSONL audit sink with AURON's centralized audit/event pipeline —
      deferred: backend `POST /audit/logs/create/` requires `IsSuperAdmin`,
      which no MCP operator identity has; needs a backend change to expose a
      per-user audit endpoint
- [ ] Distributed rate limiting (shared store, not per-process memory) —
      deferred until a Redis-backed deployment exists
- [x] Controlled-write tests (`tests/test_writes.py`: two-phase flow, single-use
      and expired tokens, payload-tamper mismatch, idempotent replay, failed
      writes don't poison idempotency, read-only guard, role boundaries,
      compensation, cashier write access)
- [x] Fiscal write: void sale (`auron_preview_void_sale` / `auron_void_sale` —
      two-phase confirmation, NCF remains consumed per DGII rules)

## M5 — Agent workflows

- [x] Daily business review (`auron_daily_review` compound tool + prompt)
- [x] Revenue analysis (`auron_revenue_analysis`)
- [x] Inventory review (`auron_inventory_review`)
- [x] Employee performance review (`auron_employee_performance_review`)
- [ ] Compliance review
- [ ] Cross-product ecosystem workflows

## M6 — Production hardening

- [x] Streamable HTTP deployment (spec 2026-07-28: stateless single POST
      endpoint, `MCP-Protocol-Version`/`Mcp-Method`/`Mcp-Name` header↔body
      validation with `-32020 HeaderMismatch`, 405 for GET/DELETE, DNS-rebinding
      protection, optional bearer token, Base64 `=?base64?...?=` Mcp-Name)
- [x] OAuth metadata (spec 2026-07-28: RFC 9728 protected-resource metadata,
      RFC 8414 AS metadata, authorization-code + mandatory PKCE S256, opaque
      audience-bound access tokens, refresh-token rotation, RFC 6750 `scope`
      parameter on 401/403 `WWW-Authenticate` challenges; in-memory provider
      in `oauth.py`, on by default off via `AURON_MCP_OAUTH_ENABLED`)
- [x] WAF (transport guards: 413 on oversized bodies via
      `AURON_MCP_HTTP_MAX_BODY_BYTES` enforced before buffering, 405 non-POST,
      413/400 clean JSON-RPC errors, OAuth 401/403 challenges; LAN nginx
      guidance in `docs/WAF.md`)
- [x] Secret management (`secrets.py`: fail-fast `check_config` at startup —
      refuses shipped-default OAuth client secret and missing `AURON_API_BASE_URL`;
      `redact()` applied to every log/error detail so configured secrets never
      surface)
- [x] OpenTelemetry (`otel.py`, extra `pip install '.[otel]'`:
      `AURON_MCP_OTEL_ENABLED` + `AURON_MCP_OTEL_ENDPOINT` (OTLP/HTTP), spans
      for every tool call keyed by the audit `trace_id` with tenant/role/outcome
      attributes and error status; lazy import so core installs need no SDK;
      `tests/test_otel.py` with in-memory exporter)
- [x] Structured logs (`logging.py`: single-line JSON records for tool errors,
      unhandled exceptions and startup; redaction applied to all string fields;
      stdio→stderr / HTTP→stdout stream selection)
- [x] Security tests (`tests/test_security.py`: 413 body cap, PARSE_ERROR
      without tracebacks, -32601 unknown methods, unknown-tool clean errors,
      405, tokens/auth headers never echoed in errors)
- [x] Tenant isolation tests (`tests/test_tenant_isolation.py`: per-tenant data
      isolation, arguments cannot select/impersonate tenants, role boundaries
      fail closed before the adapter, denied calls never reach the adapter)
- [x] Load tests (`tests/test_load.py`, marker `load`: 60 concurrent OAuth
      sessions over the HTTP stack, 100 sequential `tools/list` under a
      time budget, rate-limiter burst → 5 success / 15 `RateLimitError`,
      adapter reached only by admitted calls)
- [x] Prompt-injection / data-exfiltration tests (`tests/test_prompt_injection.py`:
      arguments passed verbatim as data, no command output/leakage, secrets
      redacted from errors, logs, prompt and resource)
