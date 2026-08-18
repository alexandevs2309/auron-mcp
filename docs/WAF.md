# WAF guidance for the AURON MCP Streamable HTTP endpoint

The MCP transport itself already implements several WAF-grade protections that
run **before** the request reaches the SDK (see `src/auron_mcp/http_app.py`):

| Threat | Guard | Where |
| --- | --- | --- |
| Oversized request bodies / memory exhaustion | 413 when body exceeds `AURON_MCP_HTTP_MAX_BODY_BYTES` (default 4 MiB), enforced **before buffering** | `StreamableHTTPValidationMiddleware` |
| Non-POST verbs to `/mcp` | 405 `Method Not Allowed` | `StreamableHTTPValidationMiddleware` |
| Malformed JSON | `-32700 PARSE_ERROR`, no stack trace | SDK transport |
| Unknown JSON-RPC methods | `-32601 Method not found` | SDK transport |
| Unknown tool names | Clean `isError` result, no crash | SDK tool registry |
| DNS-rebinding | `TransportSecuritySettings` allowlists (loopback or `AURON_MCP_HTTP_ALLOWED_HOSTS`) | `build_app` |
| Unauthenticated / wrong-scope calls | 401/403 `WWW-Authenticate` with `scope="mcp"` + `resource_metadata` (OAuth) or `Bearer` (static token) | OAuth SDK + `AuthChallengeMiddleware` / `BearerTokenMiddleware` |
| Secret leakage in errors/logs | `secrets.redact()` on every detail/log field | `errors`, `logging`, `tools` |

Because the deployment is **local-first** (computers of the restaurant LAN,
never public internet), the transport guards above are the primary line of
defense. If the endpoint is ever exposed beyond the LAN or through a public
hostname, add a reverse proxy (nginx) with the following hardening.

## nginx (when the endpoint is exposed beyond the LAN)

```nginx
# /etc/nginx/sites-available/auron-mcp
server {
    listen 443 ssl;
    server_name mcp.example.internal;

    ssl_certificate     /etc/ssl/certs/mcp.example.internal.crt;
    ssl_certificate_key /etc/ssl/private/mcp.example.internal.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    location /mcp {
        # Streamable HTTP is POST-only and JSON/SSE.
        limit_except POST { deny all; }

        # Slowloris / body protections (defense in depth on top of the
        # in-app 413 guard).
        client_max_body_size 4m;
        client_body_timeout 10s;
        client_header_timeout 10s;

        # Per-IP rate limit: 60 req/min, burst 30.
        limit_req zone=mcp_per_ip burst=30 nodelay;

        # Never log Authorization headers or request bodies.
        proxy_set_header Authorization "";
        access_log off;

        proxy_pass http://127.0.0.1:8765;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
```

## Deployment notes

- Keep `AURON_MCP_HTTP_HOST=127.0.0.1` and let nginx terminate TLS. This keeps
  the DNS-rebinding/Origin allowlists effective and HTTP traffic off the LAN.
- Prefer the OAuth stack (`AURON_MCP_OAUTH_ENABLED=true`) over the static
  bearer token when the endpoint is reachable from more than one device: the
  AS enforces PKCE S256, audience-bound opaque tokens and refresh rotation
  (`docs/CONNECTING.md`).
- The RFC 8414 authorization-server metadata requires **HTTPS** for any
  non-loopback issuer; the SDK enforces this (`validate_issuer_url`). On a
  pure-LAN deployment keep the issuer on `http://127.0.0.1` (single-device) or
  terminate TLS at the proxy.
- `AURON_MCP_OAUTH_CLIENT_SECRET` and any `AURON_API_TOKEN` are redacted from
  logs and errors; startup refuses to serve with the shipped-default OAuth
  secret (`secrets.check_config`).
