# Connecting an MCP host to auron-mcp

This server speaks MCP over stdio by default (`AURON_MCP_TRANSPORT=stdio`),
which is what local hosts expect. It does not need to be "running" as a
service for local development — the host launches it as a subprocess.

## Step 1 — authenticate once

```bash
auron-manage login --email caja@salon.test --subdomain salon-uno
auron-manage status
```

This stores AURON's httpOnly auth cookies in `~/.auron-mcp/session.json`
(or `AURON_MCP_SESSION_PATH`). The MCP server picks them up automatically and
refreshes them when the access token expires. Alternatively set a static
`AURON_API_TOKEN` (Bearer) if you prefer a service account.

## opencode (local, stdio)

Add an entry to `~/.config/opencode/opencode.jsonc` (global) or the project's
`opencode.json`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "auron": {
      "type": "local",
      "command": ["/absolute/path/to/auron-mcp/.venv/bin/auron-mcp"],
      "enabled": true,
      "environment": {
        "AURON_API_BASE_URL": "http://localhost:8002"
      }
    }
  }
}
```

Restart opencode after saving. The `auron_*` tools then appear as MCP tools.

> Port note: AURON's REST API and the D'Yiya web app both want port 8000 on the
> cash computer. AURON's `docker-compose.yml` publishes the `web` service as
> `8002:8000` so both systems can run at the same time. Point
> `AURON_API_BASE_URL` at `http://localhost:8002`.

## Claude Desktop / Claude Code (local, stdio)

Add an entry to the host's MCP config (for Claude Desktop this is
`claude_desktop_config.json`; for Claude Code it's the project or user MCP
config):

```json
{
  "mcpServers": {
    "auron-mcp": {
      "command": "/absolute/path/to/auron-mcp/.venv/bin/auron-mcp",
      "env": {
        "AURON_ADAPTER": "http",
        "AURON_API_BASE_URL": "http://localhost:8002"
      }
    }
  }
}
```

For offline development against the mock identity instead:

```json
{
  "mcpServers": {
    "auron-mcp": {
      "command": "/absolute/path/to/auron-mcp/.venv/bin/auron-mcp",
      "env": {
        "AURON_ADAPTER": "mock"
      }
    }
  }
}
```

Restart the host after editing the config. On success, the host should list
`auron_sales_summary`, `auron_appointments_today`,
`auron_clients_search`, `auron_inventory_alerts`,
`auron_employee_performance`, `auron_system_health`,
`auron_services_list`, `auron_employees_list`, `auron_branches_list`,
`auron_products_list`, `auron_dashboard_stats`, `auron_daily_review`,
`auron_inventory_review`, `auron_revenue_analysis`,
`auron_employee_performance_review`, and the controlled-write tools
(`auron_preview_create_appointment`, `auron_create_appointment`,
`auron_preview_cancel_appointment`, `auron_cancel_appointment`,
`auron_update_appointment`, `auron_reschedule_appointment`) as available
tools, plus the `auron://context/current` resource and the
`daily_business_review` prompt.

## Controlled writes (appointments)

Writes are fail-closed: while `AURON_MCP_READ_ONLY` is enabled (the shipped
default) every write/preview tool answers `{"error": "read_only", ...}`. Set
`AURON_MCP_READ_ONLY=false` to enable them.

### Two-phase high-impact writes (create / cancel)

1. Call the preview tool with the intended payload. No data changes; you get
   the exact effect plus a single-use `confirmation_token` (TTL
   `AURON_MCP_CONFIRMATION_TTL_SECONDS`, default 300s).
2. Call the write tool with the same payload, the token, and your own
   `idempotency_key`. The token is bound to a canonical hash of the payload:
   reusing it for different data is rejected (`confirmation_mismatch`), and it
   cannot be replayed (`confirmation_unknown`).

### Idempotency (all writes)

Every write takes `idempotency_key`. Successful writes are remembered
(`AURON_MCP_IDEMPOTENCY_TTL_SECONDS`, default 24h): retrying with the same key
returns the original response without repeating the side effect. Failed writes
are never recorded, so a retry after a failure re-attempts the operation.

### Low-impact writes (update / reschedule)

`auron_update_appointment` (service/branch/description) and
`auron_reschedule_appointment` need only an `idempotency_key` — no
confirmation token.

### Role boundaries

Only roles with write permissions are admitted: superadmin/support/client
admin/owner/manager (full), `CAJERA`/`FRONTDESK_CASHIER` (create, update,
cancel, reschedule). Read-only roles (`ESTILISTA`, `PROFESSIONAL`,
`CLIENT_STAFF`, `UTILITY`) get `{"error": "forbidden", ...}` before any
adapter call.

## Remote / HTTP transport (Streamable HTTP, spec 2026-07-28)

Run the server over Streamable HTTP when the client cannot launch a subprocess
(e.g. a shared LAN gateway):

```bash
AURON_MCP_TRANSPORT=streamable-http \
AURON_MCP_HTTP_HOST=127.0.0.1 \
AURON_MCP_HTTP_PORT=8765 \
auron-mcp
```

Or with the container (`AURON_ADAPTER=mock` in the Dockerfile by default; set
`AURON_API_BASE_URL` and a session when using the real HTTP adapter):

```bash
docker run -p 8765:8765 -e AURON_ADAPTER=http \
  -e AURON_API_BASE_URL=http://host.docker.internal:8002 auron-mcp
```

### Endpoint contract (2026-07-28)

The server exposes a single POST-only endpoint (default `/mcp`):

- Every JSON-RPC request is its own HTTP POST. The response is either a single
  JSON object or a request-scoped SSE stream.
- Requests MUST carry the mirrored metadata headers `MCP-Protocol-Version`,
  `Mcp-Method` and (for `tools/call`, `resources/read`, `prompts/get`)
  `Mcp-Name`; header values MUST match the JSON-RPC body or the server
  rejects with `400` + `-32020 HeaderMismatch`. `Mcp-Name` values are
  Base64-decoded when sent in the `=?base64?...?=` sentinel format.
- GET/DELETE/any non-POST to the endpoint → `405 Method Not Allowed`.
- DNS-rebinding protection is enabled: Host/Origin are validated against the
  configured allow-lists (`AURON_MCP_HTTP_ALLOWED_HOSTS`; localhost defaults).
- Request bodies larger than `AURON_MCP_HTTP_MAX_BODY_BYTES` (default 4 MiB)
  are rejected with `413` before being buffered; malformed JSON returns a
  clean `-32700 PARSE_ERROR`. See `docs/WAF.md` for the full transport-guard
  matrix and LAN nginx hardening.

### OAuth (optional, spec 2026-07-28)

Set `AURON_MCP_OAUTH_ENABLED=true` to protect the endpoint with OAuth instead
of the simple bearer token. Two simpler transport guards remain available
without it: `AURON_MCP_HTTP_BEARER_TOKEN` (static `Authorization: Bearer`),
or no transport auth at all when the endpoint only binds localhost.

```bash
AURON_MCP_TRANSPORT=streamable-http \
AURON_MCP_HTTP_HOST=0.0.0.0 \
AURON_MCP_OAUTH_ENABLED=true \
AURON_MCP_OAUTH_ISSUER_URL=http://192.168.1.50:8765 \
AURON_MCP_OAUTH_RESOURCE_URL=http://192.168.1.50:8765/mcp \
AURON_MCP_OAUTH_CLIENT_SECRET=change-me \
AURON_MCP_OAUTH_CLIENT_REDIRECT_URIS=http://127.0.0.1:6274/api/callback \
auron-mcp
```

What you get:

- `GET /.well-known/oauth-protected-resource/mcp` — RFC 9728 protected resource
  metadata (`resource`, `authorization_servers`, `scopes_supported: ["mcp"]`).
- `GET /.well-known/oauth-authorization-server` — RFC 8414 AS metadata
  (authorization/token endpoints, PKCE S256, supported grants).
- Authorization-code flow with mandatory PKCE S256 against the pre-registered
  confidential client (`AURON_MCP_OAUTH_CLIENT_ID` /
  `AURON_MCP_OAUTH_CLIENT_SECRET` / `AURON_MCP_OAUTH_CLIENT_REDIRECT_URIS`).
  Dynamic client registration (RFC 7591) is supported by the SDK but off by
  default; enable with `AURON_MCP_OAUTH_DYNAMIC_CLIENT_REGISTRATION=true`.
- Opaque, audience-bound access tokens (RFC 8707): the token is only accepted
  when its `resource` matches `AURON_MCP_OAUTH_RESOURCE_URL`; the client sends
  the same `resource` in authorize and token requests.
- Refresh tokens that rotate on use (`grant_types: ["authorization_code",
  "refresh_token"]`).
- `401`/`403` challenges carry `WWW-Authenticate: Bearer error=...,
  resource_metadata="...", scope="mcp"` so clients know which scopes to request.

The authorization server is in-memory (no external IdP): the AURON session
configured out-of-band remains the credential that maps each request to a
tenant. The `iss` parameter (RFC 9207) in authorization responses is not
emitted yet (SHOULD, not MUST); the SDK does not expose a hook for it.

The SDK (`mcp` 2.0.0) does not implement the 2026-07-28 header rules, nor the
`scope` parameter on auth challenges, so the ASGI app in
`src/auron_mcp/http_app.py` adds both on top of the SDK transport
(`StreamableHTTPValidationMiddleware` + `AuthChallengeMiddleware`); the OAuth
provider/token verifier live in `src/auron_mcp/oauth.py`. Remaining M6
hardening (WAF, secret management) is tracked in `docs/ROADMAP.md`.

Example request:

```http
POST /mcp HTTP/1.1
Content-Type: application/json
Accept: application/json, text/event-stream
MCP-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: auron_daily_review

{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "auron_daily_review",
    "arguments": {},
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientInfo": {"name": "host", "version": "1"},
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

The SDK (`mcp` 2.0.0) does not implement the 2026-07-28 header rules, so the
ASGI app in `src/auron_mcp/http_app.py` adds them on top of the SDK transport
(`StreamableHTTPValidationMiddleware`). Remaining M6 hardening (WAF, OAuth
metadata, secret management) is tracked in `docs/ROADMAP.md`.

## Development inspector

For interactive debugging without a full host:

```bash
uv run mcp dev src/auron_mcp/server.py
```
