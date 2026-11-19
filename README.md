# AURON MCP — gateway multi-tenant con escrituras controladas

[![M8ven Score](https://m8ven.ai/badge/mcp/alexandevs2309-auron-mcp-j6bwmi?v=a9ba0e4087a38628fc410c46b9a22f66)](https://m8ven.ai/mcp/alexandevs2309-auron-mcp-j6bwmi?s=readme)

MCP gateway between AI hosts/agents and the **AURON Suite** backend
(`api_peluqueria`). Identity, tenancy, and RBAC are resolved from the real
AURON REST API — the model can never steer them.

## What this is

- **26 tools**: 11 lecturas simples, 4 compuestas, 6 escrituras de citas y 5
  fiscales (incluye void sale). Las escrituras están deshabilitadas por defecto
  (`AURON_MCP_READ_ONLY=true`) y requieren preview + `confirmation_token` de un
  solo uso + `idempotency_key`.
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
- **Rate limiting**: per-(tenant, user, tool) sliding-window limiter checked
  before policy on every call. Con `AURON_REDIS_URL`, el estado compartido
  (rate limit, confirmaciones e idempotencia) vive en Redis; sin esa variable
  todo es en memoria y solo se admite una réplica. OAuth sigue en memoria.

## Tools

El catálogo vigente tiene 26 tools; la fuente de verdad es `src/auron_mcp/server.py`
y los contratos HTTP están en `src/auron_mcp/adapters/http.py`. Incluye lecturas
simples y compuestas, seis escrituras de citas (`auron_create_appointment`,
`auron_update_appointment`, `auron_cancel_appointment`, `auron_reschedule_appointment`,
etc.) y cinco tools fiscales (`auron_fiscal_summary`, `auron_void_sale`, …).
También expone el resource `auron://context/current` y el prompt
`daily_business_review`. `docs/CONNECTING.md` describe el contrato completo.

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

With `AURON_ADAPTER=mock` the server works offline with a fixed dev identity
(`u-mock-1` / `t-mock-1` / `CLIENT_ADMIN`) for development and testing only.
The default adapter is `http`.

## Tests

```bash
pytest -q          # 163 tests: policy, rbac, audit, ratelimit, adapters,
                   # identidad, tools, handshake stdio, reglas HTTP 2026-07-28
                   # y estado distribuido con Redis (tests/test_distributed_state.py)
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
3. Writes are opt-in and gated: read-only by default, preview + single-use
   confirmation token + idempotency key for every side effect.
4. Every sensitive action is auditable — including denied attempts.
5. MCP calls stable AURON application APIs, never raw SQL.
6. Never expose secrets, JWTs, upstream response bodies, or stack traces to
   the model.
7. A tool call has a cost — rate-limit it.

See `docs/mcp-integration-audit.md` (audit of the real AURON backend),
`docs/ARCHITECTURE.md`, `docs/CONNECTING.md`, and `docs/ROADMAP.md`.
