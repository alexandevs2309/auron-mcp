"""Streamable HTTP deployment for the AURON MCP server (spec 2026-07-28).

The `mcp` SDK 2.0.0 provides the Streamable HTTP transport core: a single POST
endpoint, stateless per-request handling, JSON/SSE responses, and
Origin/DNS-rebinding protection via ``TransportSecuritySettings`` (auto-enabled
for localhost binds). It does NOT implement the 2026-07-28 per-request metadata
header rules, so this module wraps the SDK app with the spec-mandated behavior
that is missing:

* GET/DELETE (any non-POST) to the MCP endpoint -> 405 Method Not Allowed.
* Modern POST requests MUST carry ``MCP-Protocol-Version``, ``Mcp-Method`` and
  ``Mcp-Name`` headers whose values match the JSON-RPC body. Mismatches or
  missing required headers are rejected with HTTP 400 and a ``HeaderMismatch``
  error (code -32020). ``Mcp-Name`` supports the ``=?base64?...?=`` encoding.

Legacy (handshake-era) requests that omit ``MCP-Protocol-Version`` are passed
through untouched for the SDK to route; the header rules only apply to modern
requests carrying an ``io.modelcontextprotocol/protocolVersion`` in ``_meta``.

Spec: https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
"""

import base64
import json
import re
from typing import Any

from .config import settings
from .server import mcp

_MCP_ENDPOINT = settings.http_path or "/mcp"
_HEADER_MISMATCH_CODE = -32020
_BASE64_SENTINEL = re.compile(r"^=\?base64\?([A-Za-z0-9+/]+=*)\?=$")
_MAX_REQUEST_BODY_BYTES = settings.http_max_body_bytes or 4_194_304

_MCP_NAME_METHODS = {"tools/call", "resources/read", "prompts/get"}


def _decode_header_value(value: str) -> str:
    """Decode a Base64-sentinel header value (``=?base64?...?=``) if present."""
    match = _BASE64_SENTINEL.match(value)
    if match:
        try:
            return base64.b64decode(match.group(1)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return value
    return value


async def _read_body(receive: Any, max_bytes: int) -> bytes | None:
    """Read a request body up to ``max_bytes``; return None when it exceeds.

    Enforcing the cap here (not only in the SDK) means an oversized body is
    rejected with 413 before it is buffered into memory — a cheap guard
    against memory-exhaustion attempts over the LAN.
    """
    chunks = []
    total = 0
    while True:
        message = await receive()
        chunk = message.get("body", b"")
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            return None
        if not message.get("more_body"):
            return b"".join(chunks)


async def _send_response(send: Any, status: int, payload: dict | bytes) -> None:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _jsonrpc_error(code: int, message: str, request_id: Any = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id if request_id is not None else None,
        "error": {"code": code, "message": message},
    }


def _validate_request_headers(scope: dict, body: bytes) -> tuple[int, dict] | None:
    """Return a ``(status, jsonrpc_error)`` rejection, or None if valid.

    Applies the 2026-07-28 request-metadata rules to modern requests. The body
    is the source of truth; the mirrored headers must match it.
    """
    if not body:
        return 400, _jsonrpc_error(_HEADER_MISMATCH_CODE, "Missing request body")
    try:
        message = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        # Malformed JSON is the transport's concern (PARSE_ERROR); pass through.
        return None
    if not isinstance(message, dict) or "method" not in message:
        return None

    params = message.get("params")
    meta = params.get("_meta") if isinstance(params, dict) else None
    body_protocol_version = None
    if isinstance(meta, dict):
        body_protocol_version = meta.get("io.modelcontextprotocol/protocolVersion")

    headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
    header_protocol_version = headers.get("mcp-protocol-version")

    if body_protocol_version is None:
        # Legacy (handshake-era) request: no mirrored-metadata contract.
        return None

    if header_protocol_version is None:
        return 400, _jsonrpc_error(
            _HEADER_MISMATCH_CODE,
            "Header mismatch: missing required 'MCP-Protocol-Version' header",
            message.get("id"),
        )
    if header_protocol_version != body_protocol_version:
        return 400, _jsonrpc_error(
            _HEADER_MISMATCH_CODE,
            "Header mismatch: MCP-Protocol-Version header value "
            f"'{header_protocol_version}' does not match body value "
            f"'{body_protocol_version}'",
            message.get("id"),
        )

    header_method = headers.get("mcp-method")
    if header_method is None:
        return 400, _jsonrpc_error(
            _HEADER_MISMATCH_CODE,
            "Header mismatch: missing required 'Mcp-Method' header",
            message.get("id"),
        )
    if header_method != message["method"]:
        return 400, _jsonrpc_error(
            _HEADER_MISMATCH_CODE,
            "Header mismatch: Mcp-Method header value "
            f"'{header_method}' does not match body method '{message['method']}'",
            message.get("id"),
        )

    if message["method"] in _MCP_NAME_METHODS:
        params = message.get("params") or {}
        body_name = None
        if message["method"] == "resources/read":
            body_name = params.get("uri") if isinstance(params, dict) else None
        else:
            body_name = params.get("name") if isinstance(params, dict) else None
        header_name_raw = headers.get("mcp-name")
        if header_name_raw is None:
            return 400, _jsonrpc_error(
                _HEADER_MISMATCH_CODE,
                "Header mismatch: missing required 'Mcp-Name' header",
                message.get("id"),
            )
        header_name = _decode_header_value(header_name_raw)
        if body_name is not None and header_name != body_name:
            return 400, _jsonrpc_error(
                _HEADER_MISMATCH_CODE,
                "Header mismatch: Mcp-Name header value "
                f"'{header_name}' does not match body value '{body_name}'",
                message.get("id"),
            )

    return None


class StreamableHTTPValidationMiddleware:
    """Spec-compliant request-metadata validation on top of the SDK transport."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") != _MCP_ENDPOINT:
            await self.app(scope, receive, send)
            return

        method = scope["method"].upper()
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        if method != "POST":
            await send(
                {
                    "type": "http.response.start",
                    "status": 405,
                    "headers": [
                        (b"allow", b"POST"),
                        (b"content-type", b"application/json"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": json.dumps(
                        _jsonrpc_error(
                            -32600, "Method Not Allowed: the MCP endpoint accepts POST only"
                        )
                    ).encode("utf-8"),
                }
            )
            return

        body = await _read_body(receive, _MAX_REQUEST_BODY_BYTES)
        if body is None:
            await _send_response(
                send,
                413,
                _jsonrpc_error(-32600, "Request body too large"),
            )
            return
        rejection = _validate_request_headers(scope, body)
        if rejection is not None:
            status, payload = rejection
            await _send_response(send, status, payload)
            return

        async def cached_receive() -> dict:
            nonlocal body
            if body is not None:
                chunk, body = body, None
                return {"type": "http.request", "body": chunk, "more_body": False}
            return await receive()

        await self.app(scope, cached_receive, send)


class BearerTokenMiddleware:
    """Optional bearer-token auth for the MCP endpoint.

    The AURON session the server uses is configured out-of-band; this protects
    the transport itself when the endpoint is reachable beyond localhost.
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") != _MCP_ENDPOINT:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        authorization = headers.get("authorization", "")
        expected = f"Bearer {self.token}"
        if authorization != expected:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"www-authenticate", b"Bearer"),
                        (b"content-type", b"application/json"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": json.dumps(_jsonrpc_error(-32001, "Unauthorized")).encode("utf-8"),
                }
            )
            return
        await self.app(scope, receive, send)


class AuthChallengeMiddleware:
    """Add the RFC 6750 ``scope`` parameter to ``WWW-Authenticate`` challenges.

    The SDK's ``RequireAuthMiddleware`` emits 401/403 challenges without the
    ``scope`` parameter; the MCP Authorization spec (2026-07-28) requires
    servers to include it so clients know which scopes to request (RFC 6750
    section 3). Only touches the MCP endpoint's 401/403 responses.
    """

    def __init__(self, app: Any, required_scopes: list[str]) -> None:
        self.app = app
        self.scope_value = " ".join(required_scopes)

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") != _MCP_ENDPOINT:
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start" and message.get("status") in (401, 403):
                headers = list(message.get("headers") or [])
                for index, (key, value) in enumerate(headers):
                    if key.lower() == b"www-authenticate":
                        value = value.decode("latin-1")
                        if "scope=" not in value:
                            headers[index] = (
                                b"www-authenticate",
                                (value + f', scope="{self.scope_value}"').encode("latin-1"),
                            )
                        message = {**message, "headers": headers}
                        break
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _default_security_settings() -> Any:
    from mcp.server.transport_security import TransportSecuritySettings

    host = settings.http_host
    if host in ("127.0.0.1", "localhost", "::1"):
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.http_allowed_hosts),
        allowed_origins=["http://localhost:*"],
    )


def build_app(
    *,
    json_response: bool = False,
    stateless_http: bool = True,
    auth: Any = None,
    auth_server_provider: Any = None,
    token_verifier: Any = None,
    transport_security: Any = None,
) -> Any:
    """Build the Streamable HTTP ASGI app for the AURON MCP server.

    ``auth``/``auth_server_provider``/``token_verifier`` are optional: when
    omitted they are resolved from settings (OAuth enabled via
    ``AURON_MCP_OAUTH_ENABLED``). ``transport_security`` defaults to the
    settings-derived DNS-rebinding protection. OAuth takes precedence over the
    simple bearer token, which is only applied when no OAuth stack is
    configured.
    """
    if auth is None and auth_server_provider is None and token_verifier is None:
        from .oauth import build_auth_dependencies

        auth, auth_server_provider, token_verifier = build_auth_dependencies()

    # `MCPServer.streamable_http_app` only forwards auth configured at server
    # construction; call the low-level builder directly so auth can be injected
    # per-app (settings-driven in prod, explicit in tests).
    sdk_server = mcp._lowlevel_server
    sdk_app = sdk_server.streamable_http_app(
        streamable_http_path=_MCP_ENDPOINT,
        json_response=json_response,
        stateless_http=stateless_http,
        transport_security=transport_security or _default_security_settings(),
        auth=auth,
        token_verifier=token_verifier,
        auth_server_provider=auth_server_provider,
    )
    wrapped = sdk_app
    if auth is not None:
        wrapped = AuthChallengeMiddleware(wrapped, auth.required_scopes or [])
    wrapped = StreamableHTTPValidationMiddleware(wrapped)
    if settings.http_bearer_token and token_verifier is None:
        wrapped = BearerTokenMiddleware(wrapped, settings.http_bearer_token)
    return wrapped


app = build_app()


def main() -> None:
    import uvicorn

    from .logging import logger
    from .otel import setup_otel
    from .secrets import ConfigurationError, check_config

    try:
        check_config(settings)
    except ConfigurationError as exc:
        logger.error("config.invalid", error_message=str(exc))
        raise
    logger.info(
        "http.listen",
        host=settings.http_host,
        port=settings.http_port,
        path=_MCP_ENDPOINT,
        transport_security="enabled",
        oauth_enabled=settings.oauth_enabled,
    )
    setup_otel()
    uvicorn.run(app, host=settings.http_host, port=settings.http_port, log_level="info")


if __name__ == "__main__":
    main()
