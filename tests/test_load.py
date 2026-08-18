"""Load / concurrency sanity tests (marker: `load`).

The streamable-http stack must serve concurrent sessions without errors, hold
up under sequential throughput, and let the in-process rate limiter kick in
under a burst. Deliberately fast and deterministic; run with
`pytest -m load` or as part of the full suite.
"""

import asyncio
import base64
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

os.environ["AURON_ADAPTER"] = "mock"

import pytest
from mcp.server.transport_security import TransportSecuritySettings
from starlette.testclient import TestClient

from auron_mcp.adapters.mock import MockAuronAdapter
from auron_mcp.audit import AuditLogger
from auron_mcp.auth import AuronIdentity
from auron_mcp.context import new_context
from auron_mcp.http_app import build_app
from auron_mcp.oauth import build_auth_dependencies
from auron_mcp.policy import PolicyEngine
from auron_mcp.policy.roles import permissions_for_role
from auron_mcp.ratelimit import RateLimiter
from auron_mcp.tools import AuronTools

pytestmark = pytest.mark.load

ISSUER = "http://127.0.0.1:8765"
RESOURCE = "http://127.0.0.1:8765/mcp"
CLIENT_ID = "load-client"
CLIENT_SECRET = "load-secret"
REDIRECT_URI = "http://127.0.0.1:6274/api/callback"
PROTOCOL = "2026-07-28"
CONCURRENCY = 60


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


def _mcp_payload():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL,
                "io.modelcontextprotocol/clientInfo": {"name": "load", "version": "1"},
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }


def _mcp_headers(token):
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL,
        "Mcp-Method": "tools/list",
        "Authorization": f"Bearer {token}",
    }


@pytest.fixture
def oauth_app():
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


async def test_concurrent_sessions_all_succeed(oauth_app):
    with TestClient(oauth_app) as client:
        token = _obtain_token(client)
        headers = _mcp_headers(token)
        payload = json.dumps(_mcp_payload())

        def fire():
            response = client.post("/mcp", content=payload, headers=headers)
            return response

        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            results = list(pool.map(lambda _i: fire(), range(CONCURRENCY)))
        for response in results:
            assert response.status_code == 200
            body = response.json()
            names = {tool["name"] for tool in body["result"]["tools"]}
            assert "auron_sales_summary" in names


async def test_sequential_throughput_sanity(oauth_app):
    with TestClient(oauth_app) as client:
        token = _obtain_token(client)
        headers = _mcp_headers(token)
        payload = json.dumps(_mcp_payload())

        started = time.monotonic()
        for _ in range(100):
            response = client.post("/mcp", content=payload, headers=headers)
            assert response.status_code == 200
        elapsed = time.monotonic() - started
        assert elapsed < 10.0, f"100 tools/list took {elapsed:.2f}s (over 10s budget)"


async def test_rate_limit_kicks_in_under_burst():
    class CountingAdapter(MockAuronAdapter):
        def __init__(self):
            self.calls = 0

        async def sales_summary(self):
            self.calls += 1
            return {"status": "mock"}

    adapter = CountingAdapter()
    tools = AuronTools(
        adapter,
        PolicyEngine(),
        AuditLogger(sink=lambda _line: None),
        RateLimiter(max_calls=5, window_seconds=60),
    )
    identity = AuronIdentity(
        user_id="u-load",
        email="u-load@auron.test",
        tenant_id="t-load",
        role="CLIENT_ADMIN",
        permissions=permissions_for_role("CLIENT_ADMIN"),
    )
    ctx = new_context(identity)

    results = await asyncio.gather(
        *[tools.sales_summary(ctx) for _ in range(20)],
        return_exceptions=True,
    )
    ok = [r for r in results if isinstance(r, dict) and r.get("status") == "mock"]
    limited = [
        r for r in results if isinstance(r, Exception) and type(r).__name__ == "RateLimitError"
    ]
    assert len(ok) == 5
    assert len(limited) == 15
    assert adapter.calls == 5
