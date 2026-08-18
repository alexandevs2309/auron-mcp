# AURON MCP Architecture

## Objective

Create a stable semantic boundary between AI agents and the AURON ecosystem.

The MCP server should own:

- protocol exposure
- request identity
- tenant/scope resolution
- policy checks
- rate limiting
- tool schemas/descriptions
- audit events
- adapters to AURON services

It should **not** become a second business-logic monolith.

## Identity

The model does not decide:

- tenant
- user
- role
- permissions

Those come from the authenticated principal.

A model can provide filters such as a date range or search string, but the server
must intersect them with the authenticated scope.

## Request path

Every tool call goes through the same pipeline, in this order, implemented in
`AuronTools._call`:

```text
rate limiter  ->  policy.require_permission
      |                    |
    reject              reject
   (audited            (audited
    as denied)          as denied)
      |                    |
      +--------------------+
             v
     adapter call (mock or http)
             v
     audit.record(outcome="success")
```

Tenant scoping is delegated entirely to the upstream AURON API: the adapter
sends no `tenant_id` and no `X-Auron-Tenant` header — AURON derives the tenant
from the session's own auth. `PolicyEngine.resolve_tenant` exists defensively
(and is unit-tested) to reject any model-supplied cross-tenant override, but no
current tool accepts a tenant input at all.

Denials are audited with the same shape as successes (`outcome="denied"`),
because a spike in denied calls is itself a signal worth being able to see
later — silently swallowing it would hide misuse or a misconfigured client.

## Tool taxonomy

### Read tools

- sales summaries
- appointment lists
- client search
- inventory alerts
- employee metrics
- reports
- health

### Write tools — future

- create appointment
- cancel appointment
- create client
- adjust inventory
- create promotion

### High-risk tools — future

- issue/cancel fiscal documents
- change compensation
- modify permissions
- delete records
- financial actions

These should require explicit authorization and preferably confirmation.

## Why semantic tools

Avoid:

```text
auron_api_get(path="/api/foo/bar/?x=...")
```

Prefer:

```text
auron_sales_summary()
auron_clients_search(query, limit)
```

The semantic layer lets the AURON REST API evolve internally without forcing every
MCP client to understand implementation details.

## Adapter strategy

The current adapter abstraction supports a mock adapter and an HTTP adapter.
A production implementation can use the real AURON REST APIs or application services.

Do not connect the MCP server directly to arbitrary database tables.

Adapter failures (HTTP errors, timeouts, connection failures) are caught inside
the adapter and re-raised as `AdapterError` with a short, safe message
(e.g. `"upstream_error:403"`). The original response body is never attached to
that message — see principle #7 in `README.md`. `server.py` catches
`AdapterError` at the tool boundary and returns a clean payload to the model.

## Multi-tenant invariant

```text
authenticated_identity
        +
server_side_policy
        +
tenant_scope
        =
authorized_query
```

Never:

```text
LLM supplied tenant_id
        =
authorization
```

### This invariant does not stop at the MCP boundary

The adapter authenticates exactly like AURON's own clients: either a Bearer
token or the httpOnly auth cookies (`access_token` / `refresh_token`) issued
by `/api/auth/login/`. It NEVER sends a `tenant_id` param or an
`X-Auron-Tenant` header — doing so would hand the model a way to steer
authorization, which this gateway refuses (see `docs/mcp-integration-audit.md`).
The AURON REST API independently derives and enforces the tenant from its own
auth (`TenantMiddleware`), the exact same discipline this MCP server applies
to the model. M2 (real Suite integration) should still include a test that
hits the real API with a mismatched token/tenant pair and confirms the API
itself rejects it — not just this server.

## Identity

Identity is resolved on every request by `IdentityProvider.resolve()`:

```text
GET /api/auth/verify/           -> user {id, email, role, branch_id}
GET /api/tenants/current/       -> tenant {id, subdomain, currency, ...}
        |
        v
AuronIdentity (immutable)       -> permissions from policy/roles.py (fail-closed)
```

A session carries the Bearer token (static, via `AURON_API_TOKEN`) or the
httpOnly cookies stored by `auron-manage login`. On a 401 from `verify/`,
the provider attempts `/api/auth/cookie-refresh/` once (cookie sessions only)
and retries; a still-expired/absent session surfaces to the model as a clean
`{"error": "unauthorized", ...}` payload. Identities are cached per session
for `AURON_IDENTITY_CACHE_TTL` seconds.

## RBAC

`policy/roles.py` mirrors AURON's canonical roles
(SUPER_ADMIN, CLIENT_ADMIN, CLIENT_STAFF, ESTILISTA, CAJERA, MANAGER, UTILITY,
...) to read-permission codenames, fail-closed (unknown roles → nothing). It
is a stand-in mirror, **not** a second source of truth: AURON's own
authorization (a 403 on the upstream call) always wins. If AURON's RBAC is
exposed as data in the future, `permissions_for_role` should be replaced by a
lookup against that data.
