"""Tests for the OAuth authorization layer (MCP spec 2026-07-28).

Exercises the authorization server metadata, protected resource metadata, the
authorization-code + PKCE flow, token validation and the 401/403 challenge
responses through the full HTTP stack. No live backend needed: `tools/list`
never reaches the AURON adapter.
"""

import base64
import hashlib
import json
import os

os.environ["AURON_ADAPTER"] = "mock"

import pytest
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyHttpUrl, AnyUrl

from auron_mcp.http_app import build_app
from auron_mcp.oauth import (
    MemoryAuthServer,
    OpaqueTokenVerifier,
    build_auth_dependencies,
)

ISSUER = "http://127.0.0.1:8765"
RESOURCE = "http://127.0.0.1:8765/mcp"
CLIENT_ID = "test-client"
CLIENT_SECRET = "test-secret"
REDIRECT_URI = "http://127.0.0.1:6274/api/callback"


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


def _authorize(client, *, challenge, scope="mcp", resource=None, state=None):
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": scope,
    }
    if resource is not None:
        params["resource"] = resource
    if state is not None:
        params["state"] = state
    return client.get("/authorize", params=params, follow_redirects=False)


def _exchange_code(client, *, code, verifier, resource=None):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code_verifier": verifier,
    }
    if resource is not None:
        data["resource"] = resource
    return client.post("/token", data=data)


def _mcp_payload():
    return {
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


def _mcp_headers(token=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
        "Mcp-Method": "tools/list",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


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


def _oauth_flow(client, challenge, verifier, *, scope="mcp"):
    response = _authorize(client, challenge=challenge, scope=scope)
    assert response.status_code == 302
    location = response.headers["location"]
    from urllib.parse import parse_qs, urlparse

    query = parse_qs(urlparse(location).query)
    assert "code" in query
    token = _exchange_code(client, code=query["code"][0], verifier=verifier)
    assert token.status_code == 200
    return token.json()


def test_protected_resource_metadata(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        response = client.get("/.well-known/oauth-protected-resource/mcp")
        assert response.status_code == 200
        metadata = response.json()
        assert metadata["resource"] == RESOURCE
        assert metadata["authorization_servers"] == [ISSUER + "/"]
        assert metadata["scopes_supported"] == ["mcp"]
        assert metadata["bearer_methods_supported"] == ["header"]


def test_authorization_server_metadata(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        response = client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200
        metadata = response.json()
        assert metadata["issuer"] == ISSUER + "/"
        assert metadata["authorization_endpoint"].startswith(ISSUER)
        assert metadata["token_endpoint"].startswith(ISSUER)
        assert "S256" in metadata["code_challenge_methods_supported"]
        assert "authorization_code" in metadata["grant_types_supported"]
        assert "mcp" in metadata["scopes_supported"]


def test_mcp_requires_authentication_with_scope_challenge(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        response = client.post("/mcp", content=json.dumps(_mcp_payload()), headers=_mcp_headers())
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert 'error="invalid_token"' in challenge
        assert 'scope="mcp"' in challenge
        assert 'resource_metadata="' in challenge


def test_invalid_token_rejected_401(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        response = client.post(
            "/mcp",
            content=json.dumps(_mcp_payload()),
            headers=_mcp_headers(token="not-a-real-token"),
        )
        assert response.status_code == 401


def test_full_oauth_flow_grants_access(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        verifier, challenge = _pkce()
        token = _oauth_flow(client, challenge, verifier)
        assert token["token_type"] == "Bearer"
        assert token["scope"] == "mcp"
        response = client.post(
            "/mcp",
            content=json.dumps(_mcp_payload()),
            headers=_mcp_headers(token=token["access_token"]),
        )
        assert response.status_code == 200
        names = {tool["name"] for tool in response.json()["result"]["tools"]}
        assert "auron_sales_summary" in names


def test_wrong_code_verifier_rejected(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        _, challenge = _pkce()
        response = _authorize(client, challenge=challenge)
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]
        token = _exchange_code(client, code=code, verifier="wrong-verifier")
        assert token.status_code == 400
        assert token.json()["error"] == "invalid_grant"


def test_authorization_code_single_use(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        verifier, challenge = _pkce()
        response = _authorize(client, challenge=challenge)
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]
        first = _exchange_code(client, code=code, verifier=verifier)
        assert first.status_code == 200
        second = _exchange_code(client, code=code, verifier=verifier)
        assert second.status_code == 400
        assert second.json()["error"] == "invalid_grant"


def test_wrong_client_secret_rejected(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        verifier, challenge = _pkce()
        response = _authorize(client, challenge=challenge)
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(response.headers["location"]).query)["code"][0]
        token = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": "wrong-secret",
                "code_verifier": verifier,
            },
        )
        assert token.status_code == 401
        assert token.json()["error"] == "invalid_client"


def test_insufficient_scope_returns_403_with_challenge():
    from starlette.testclient import TestClient

    client_info = OAuthClientInformationFull(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uris=[AnyUrl(REDIRECT_URI)],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        scope="mcp.other",
    )
    provider = MemoryAuthServer(
        issuer_url=ISSUER,
        resource_server_url=RESOURCE,
        clients={CLIENT_ID: client_info},
    )
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(ISSUER),
        resource_server_url=AnyHttpUrl(RESOURCE),
        required_scopes=["mcp"],
    )
    app = build_app(
        auth=auth,
        auth_server_provider=provider,
        token_verifier=OpaqueTokenVerifier(provider),
        transport_security=_security(),
    )
    with TestClient(app) as client:
        verifier, challenge = _pkce()
        token = _oauth_flow(client, challenge, verifier, scope="mcp.other")
        assert token["scope"] == "mcp.other"
        response = client.post(
            "/mcp",
            content=json.dumps(_mcp_payload()),
            headers=_mcp_headers(token=token["access_token"]),
        )
        assert response.status_code == 403
        challenge_header = response.headers["www-authenticate"]
        assert 'error="insufficient_scope"' in challenge_header
        assert 'scope="mcp"' in challenge_header
        assert 'resource_metadata="' in challenge_header


def test_unknown_resource_rejected(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        _, challenge = _pkce()
        response = _authorize(
            client,
            challenge=challenge,
            resource="http://127.0.0.1:9999/other",
        )
        assert response.status_code == 302
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(response.headers["location"]).query)
        assert query["error"] == ["invalid_request"]
        assert "code" not in query


def test_expired_token_rejected():
    from starlette.testclient import TestClient

    auth, provider, verifier = build_auth_dependencies(
        enabled=True,
        issuer_url=ISSUER,
        resource_server_url=RESOURCE,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uris=(REDIRECT_URI,),
        required_scopes=("mcp",),
    )
    provider.access_token_ttl = -1
    app = build_app(
        auth=auth,
        auth_server_provider=provider,
        token_verifier=verifier,
        transport_security=_security(),
    )
    with TestClient(app) as client:
        verifier_value, challenge = _pkce()
        token = _oauth_flow(client, challenge, verifier_value)
        response = client.post(
            "/mcp",
            content=json.dumps(_mcp_payload()),
            headers=_mcp_headers(token=token["access_token"]),
        )
        assert response.status_code == 401


def test_refresh_token_rotation(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        verifier, challenge = _pkce()
        token = _oauth_flow(client, challenge, verifier)
        refresh = token["refresh_token"]
        rotated = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": "mcp",
            },
        )
        assert rotated.status_code == 200
        assert rotated.json()["access_token"] != token["access_token"]
        reused = client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": "mcp",
            },
        )
        assert reused.status_code == 400
        assert reused.json()["error"] == "invalid_grant"


def test_audience_binding_blocks_other_resource():
    _, provider, _ = build_auth_dependencies(
        enabled=True,
        issuer_url=ISSUER,
        resource_server_url=RESOURCE,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uris=(REDIRECT_URI,),
        required_scopes=("mcp",),
    )

    async def _run():
        from mcp.server.auth.provider import AuthorizationParams

        client = await provider.get_client(CLIENT_ID)
        redirect = await provider.authorize(
            client,
            AuthorizationParams(
                state=None,
                scopes=["mcp"],
                code_challenge="x" * 43,
                redirect_uri=AnyUrl(REDIRECT_URI),
                redirect_uri_provided_explicitly=True,
                resource=None,
            ),
        )
        from urllib.parse import parse_qs, urlparse

        code = parse_qs(urlparse(redirect).query)["code"][0]
        auth_code = await provider.load_authorization_code(client, code)
        token = await provider.exchange_authorization_code(client, auth_code)
        # token is valid for the configured resource server
        assert await provider.load_access_token(token.access_token) is not None
        # a different resource server must not accept it (audience binding)
        provider.resource_server_url = "http://127.0.0.1:9999/mcp"
        assert await provider.load_access_token(token.access_token) is None

    import anyio

    anyio.run(_run)


def test_dynamic_client_registration_off_by_default(oauth_app):
    from starlette.testclient import TestClient

    with TestClient(oauth_app) as client:
        response = client.post(
            "/register",
            json={"client_name": "x", "redirect_uris": [REDIRECT_URI]},
        )
        assert response.status_code == 404
