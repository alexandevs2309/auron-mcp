# AURON MCP — v1 (read-only, multi-tenant, production-lean)

[![Puntuación M8ven](https://m8ven.ai/badge/mcp/alexandevs2309-auron-mcp-j6bwmi?v=02a3bad47c276bec9efeedd001932591)](https://m8ven.ai/mcp/alexandevs2309-auron-mcp-j6bwmi?s=readme)

MCP gateway between AI hosts/agents and the **AURON Suite** backend
(`api_peluqueria`). Identity, tenancy, and RBAC are resolved from the real
AURON REST API — the model can never steer them.

## What this is

- **Read-only**: every tool is a GET against a verified AURON endpoint. No
  create/update/delete is exposed.
- **Real identity**: resolves the authenticated AURON principal via
  `GET /api/auth/verify/` + `GET /api/tenants/current/` (Bearer token or the
  httpOnly auth cookies that `/api/auth/login/` issues).
- **Fail-closed RBAC**: role → permission mirror (`policy/roles.py`) derived
  from AURON's canonical roles. Unknown roles get zero permissions; AURON's own
  authorization (403) still overrides the table.
- **Tenancy delegated to AURON**: the adapter NEVER sends `tenant_id` or an
  `X-Auron-Tenant` header. The upstream derives the tenant from the session via
  its `TenantMiddleware`, exactly like AURON's own clients.
- **Error boundary**: nothing internal (messages, tokens, upstream bodies,
  stack traces) reaches the model — see `errors/normalize_error`.
- **Per-call audit**: every tool call (success or denied) is written to an
  append-only JSONL audit log and stdout.
- **Rate limiting**: per-(user, tool) sliding-window limiter checked before
  policy on every call.

## Tools

| Tool | Endpoint |
|---|---|
| `auron_sales_summary` | `GET /api/pos/summary/daily/` |
| `auron_appointments_today` | `GET /api/appointments/appointments/today/` |
| `auron_clients_search(query, limit)` | `GET /api/clients/clients/?search=&limit=` (max 100) |
| `auron_inventory_alerts` | `GET /api/inventory/alerts/low-stock/` |
| `auron_employee_performance(employee_id)` | `GET /api/reports/employees/` |
| `auron_services_list` | `GET /api/services/services/` |
| `auron_employees_list` | `GET /api/employees/employees/` |
| `auron_branches_list` | `GET /api/settings/branches/` |
| `auron_products_list` | `GET /api/inventory/products/` |
| `auron_dashboard_stats` | `GET /api/pos/dashboard/stats/` |
| `auron_daily_review` | Compound: sales + appointments + inventory + employee performance |
| `auron_inventory_review` | Compound: products + low-stock alerts |
| `auron_revenue_analysis` | Compound: dashboard stats + today's sales |
| `auron_employee_performance_review` | Compound: performance + employee roster |
| `auron_system_health` | `GET /api/healthz/` |

Plus a `auron://context/current` resource and a `daily_business_review` prompt.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and set the real values:

```bash
AURON_ADAPTER=http
AURON_API_BASE_URL=http://localhost:8000
```

Authenticate once (this stores AURON's httpOnly auth cookies for the gateway):

```bash
auron-manage login --email caja@salon.test --subdomain salon-uno
auron-manage status        # verify the resolved identity/role
```

Run the server:

```bash
auron-mcp          # stdio (default; what local MCP hosts expect)
```

Run over Streamable HTTP (spec 2026-07-28) instead:

```bash
AURON_MCP_TRANSPORT=streamable-http \
AURON_MCP_HTTP_HOST=127.0.0.1 \
AURON_MCP_HTTP_PORT=8765 \
auron-mcp          # serves the MCP endpoint at http://127.0.0.1:8765/mcp
```

The HTTP deployment validates the `MCP-Protocol-Version` / `Mcp-Method` /
`Mcp-Name` request headers against the JSON-RPC body (`400` + `-32020`
`HeaderMismatch`), rejects non-POST with `405`, and enables DNS-rebinding
protection. See `docs/CONNECTING.md` for the full endpoint contract and
`src/auron_mcp/http_app.py` for the spec layer on top of the SDK transport.

With `AURON_ADAPTER=mock` (default) the server works offline with a fixed
dev identity (`u-mock-1` / `t-mock-1` / `CLIENT_ADMIN`) for development and
testing only.

## Tests

```bash
pytest -q          # 78 tests: policy, rbac, audit, ratelimit, adapters,
                   # identity provider, tools, MCP stdio handshake, and the
                   # 2026-07-28 Streamable HTTP header rules
ruff check src tests
```

## Layout

```text
src/auron_mcp/
  auth/          identity.py (AuronIdentity) + provider.py (resolve via AURON)
  policy/        roles.py (RBAC mirror) + engine.py (PolicyEngine, fail-closed)
  audit/         AuditLogger (JSONL + stdout)
  adapters/      base.py (interface) / http.py (httpx2 + cookies) / mock.py
  tools/         AuronTools (policy + rate-limit + audit around every read)
  errors/        normalize_error (model-safe error boundary)
  server.py      MCPServer wiring (stdio / streamable-http / sse)
  http_app.py    Streamable HTTP deployment: 2026-07-28 header validation,
                 DNS-rebinding settings, optional bearer token
  manage.py      operator CLI: login / status / clean
```

## Security principles

1. Never trust a tenant ID supplied by an LLM as an authorization boundary.
2. Resolve tenant/scope from authenticated identity and server-side policy.
3. Tools are read-only; side effects are intentionally out of scope for v1.
4. Every sensitive action is auditable — including denied attempts.
5. MCP calls stable AURON application APIs, never raw SQL.
6. Never expose secrets, JWTs, upstream response bodies, or stack traces to
   the model.
7. A tool call has a cost — rate-limit it.

See `docs/mcp-integration-audit.md` (audit of the real AURON backend),
`docs/ARCHITECTURE.md`, `docs/CONNECTING.md`, and `docs/ROADMAP.md`.
