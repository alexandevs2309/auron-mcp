"""Tests for the Streamable HTTP deployment (spec 2026-07-28).

Exercises the transport through the full middleware stack (validation +
bearer token) against the in-process SDK transport. No live backend needed:
`tools/list` never reaches the AURON adapter.
"""

import json
import os

os.environ["AURON_ADAPTER"] = "mock"

import pytest
from mcp.server.transport_security import TransportSecuritySettings

from auron_mcp.http_app import (
    StreamableHTTPValidationMiddleware,
    _decode_header_value,
    build_app,
)
from auron_mcp.server import mcp

MODERN_TOOLS_LIST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    },
}

MODERN_TOOLS_CALL = {
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
        "name": "auron_sales_summary",
        "arguments": {},
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    },
}

MODERN_RESOURCES_READ = {
    "jsonrpc": "2.0",
    "id": 3,
    "method": "resources/read",
    "params": {
        "uri": "auron://context/current",
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    },
}


def _security():
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["testserver", "127.0.0.1:*", "localhost:*"],
        allowed_origins=["http://testserver", "http://127.0.0.1:*", "http://localhost:*"],
    )


@pytest.fixture
def http_app():
    sdk_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=_security(),
    )
    return StreamableHTTPValidationMiddleware(sdk_app)


def _post(client, payload, extra_headers=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **(extra_headers or {}),
    }
    return client.post("/mcp", content=json.dumps(payload), headers=headers)


def _pv_headers(method="tools/list", name=None):
    headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    if name is not None:
        headers["Mcp-Name"] = name
    return headers


def test_non_post_methods_rejected_405(http_app):
    from starlette.testclient import TestClient

    with TestClient(http_app) as client:
        assert client.get("/mcp").status_code == 405
        assert client.delete("/mcp").status_code == 405
        assert client.put("/mcp").status_code == 405


def test_missing_protocol_version_header_rejected(http_app):
    from starlette.testclient import TestClient

    with TestClient(http_app) as client:
        response = _post(client, MODERN_TOOLS_LIST, {"Mcp-Method": "tools/list"})
        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == -32020


def test_mismatched_protocol_version_header_rejected(http_app):
    from starlette.testclient import TestClient

    with TestClient(http_app) as client:
        response = _post(
            client,
            MODERN_TOOLS_LIST,
            {"MCP-Protocol-Version": "2025-03-26", "Mcp-Method": "tools/list"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32020


def test_missing_mcp_method_header_rejected(http_app):
    from starlette.testclient import TestClient

    with TestClient(http_app) as client:
        response = _post(
            client, MODERN_TOOLS_LIST, {"MCP-Protocol-Version": "2026-07-28"}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32020


def test_mismatched_mcp_method_header_rejected(http_app):
    from starlette.testclient import TestClient

    with TestClient(http_app) as client:
        response = _post(
            client,
            MODERN_TOOLS_LIST,
            {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "resources/list"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32020


def test_missing_mcp_name_header_rejected_for_tools_call(http_app):
    from starlette.testclient import TestClient

    with TestClient(http_app) as client:
        response = _post(
            client,
            MODERN_TOOLS_CALL,
            {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": "tools/call"},
        )
        assert response.status_code == 400
        assert "Mcp-Name" in response.json()["error"]["message"]


def test_mismatched_mcp_name_header_rejected(http_app):
    from starlette.testclient import TestClient

    with TestClient(http_app) as client:
        response = _post(
            client,
            MODERN_TOOLS_CALL,
            _pv_headers(method="tools/call", name="auron_other"),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32020


def test_base64_encoded_mcp_name_header_decoded(http_app):
    from starlette.testclient import TestClient

    with TestClient(http_app) as client:
        encoded = _decode_header_value("=?base64?YXVyb246Ly9jb250ZXh0L2N1cnJlbnQ=?=")
        assert encoded == "auron://context/current"
        response = _post(
            client,
            MODERN_RESOURCES_READ,
            _pv_headers(method="resources/read", name="=?base64?YXVyb246Ly9jb250ZXh0L2N1cnJlbnQ=?="),
        )
        assert response.status_code == 200


def test_valid_modern_tools_list(http_app):
    from starlette.testclient import TestClient

    with TestClient(http_app) as client:
        response = _post(
            client,
            MODERN_TOOLS_LIST,
            _pv_headers(method="tools/list"),
        )
        assert response.status_code == 200
        tools = response.json()["result"]["tools"]
        names = {tool["name"] for tool in tools}
        assert "auron_sales_summary" in names
        assert "auron_daily_review" in names


def test_bearer_token_middleware_enforces_auth():
    from starlette.testclient import TestClient

    from auron_mcp.http_app import BearerTokenMiddleware

    sdk_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=_security(),
    )
    app = BearerTokenMiddleware(StreamableHTTPValidationMiddleware(sdk_app), "sekret")
    with TestClient(app) as client:
        assert client.post("/mcp", content="{}").status_code == 401
        response = client.post(
            "/mcp",
            content="{}",
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401
        response = client.post(
            "/mcp",
            content="{}",
            headers={"Authorization": "Bearer sekret"},
        )
        assert response.status_code != 401


def test_build_app_returns_callable():
    app = build_app()
    assert callable(app)
