"""Transport-level security tests for the Streamable HTTP endpoint.

Exercises the full HTTP stack (middleware order: request-body cap and header
validation OUTSIDE auth, SDK transport, OAuth challenge) with the mock adapter:
oversized bodies, malformed JSON, unknown methods/tools, method restrictions and
leak-free error responses. The body-size cap is monkeypatched to 2 KiB so the
tests stay fast.
"""

import base64
import hashlib
import json
import os

os.environ["AURON_ADAPTER"] = "mock"

import pytest
from mcp.server.transport_security import TransportSecuritySettings

from auron_mcp.http_app import build_app
from auron_mcp.oauth import build_auth_dependencies

ISSUER = "http://127.0.0.1:8765"
RESOURCE = "http://127.0.0.1:8765/mcp"
CLIENT_ID = "test-client"
CLIENT_SECRET = "test-secret"
REDIRECT_URI = "http://127.0.0.1:6274/api/callback"
PROTOCOL = "2026-07-28"


def _security():
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["testserver", "127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://testserver", "http://127.0.0.1:*", "http://localhost:*"],
    )


def _pkce():
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    return verifier, challenge


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr("auron_mcp.http_app._MAX_REQUEST_BODY_BYTES", 2048)
    auth, provider, verifier = build_auth_dependencies(
        enabled=True,
        issuer_url=ISSUER,
        resource_server_url=RESOURCE,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uris=(REDIRECT_URI,),
        required_scopes=("mcp",),
    )
    return build_app(
        auth=auth,
        auth_server_provider=provider,
        token_verifier=verifier,
        transport_security=_security(),
    )


def _obtain_token(client):
    verifier, challenge = _pkce()
    resp = client.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "scope": "mcp",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302
    from urllib.parse import parse_qs, urlparse

    code = parse_qs(urlparse(resp.headers["location"]).query)["code"][0]
    token = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code_verifier": verifier,
        },
    )
    assert token.status_code == 200
    return token.json()["access_token"]


def _payload(method: str, name: str | None = None, arguments: dict | None = None) -> dict:
    params: dict = {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL,
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    }
    if method == "tools/call":
        params["name"] = name
        params["arguments"] = arguments or {}
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}


def _post(client, body, *, method="tools/list", name=None, token=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": method,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    content = body if isinstance(body, str) else json.dumps(body)
    return client.post("/mcp", content=content, headers=headers)


def test_oversized_body_rejected_413_before_buffering(app):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        token = _obtain_token(client)
        oversized = json.dumps(_payload("tools/list")) + " " * 4096
        response = _post(client, oversized, token=token)
        assert response.status_code == 413


def test_malformed_json_returns_parse_error_without_traceback(app):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        token = _obtain_token(client)
        response = _post(client, "{ this is definitely not valid json !!!", token=token)
        body = response.json()
        assert body["jsonrpc"] == "2.0"
        assert body["error"]["code"] == -32700
        assert "Traceback" not in response.text


def test_unknown_method_returns_method_not_found(app):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        token = _obtain_token(client)
        response = _post(
            client,
            _payload("bogus/not-a-real-method"),
            method="bogus/not-a-real-method",
            token=token,
        )
        body = response.json()
        assert body["error"]["code"] == -32601
        assert "Traceback" not in response.text


def test_unknown_tool_returns_clean_error(app):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        token = _obtain_token(client)
        response = _post(
            client,
            _payload("tools/call", name="nonexistent_tool", arguments={}),
            method="tools/call",
            name="nonexistent_tool",
            token=token,
        )
        assert response.status_code == 200
        result = response.json()["result"]
        assert result.get("isError") is True
        assert "Traceback" not in response.text
        assert "nonexistent_tool" in response.text


def test_get_request_rejected_405(app):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        response = client.get("/mcp")
        assert response.status_code == 405


def test_invalid_bearer_token_not_echoed_in_error(app):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        response = _post(
            client,
            _payload("tools/list"),
            token="super-secret-token-xyz",
        )
        assert response.status_code == 401
        assert "super-secret-token-xyz" not in response.text


def test_invalid_authorization_value_not_echoed(app):
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            content=json.dumps(_payload("tools/list")),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": PROTOCOL,
                "Mcp-Method": "tools/list",
                "Authorization": "Basic dXNlcjpwYXNzd29yZA==",
            },
        )
        assert response.status_code == 401
        assert "password" not in response.text.lower()
