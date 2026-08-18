import httpx2 as httpx
import pytest

from auron_mcp.adapters.http import HttpAuronAdapter
from auron_mcp.auth import IdentityProvider, IdentityUnavailableError


def make_adapter(handler, **kwargs):
    adapter = HttpAuronAdapter("http://localhost:8000", **kwargs)
    adapter.client = httpx.AsyncClient(
        base_url="http://localhost:8000",
        transport=httpx.MockTransport(handler),
    )
    return adapter


def verify_payload(role="CLIENT_ADMIN", user_id="u-1"):
    return {
        "authenticated": True,
        "user": {
            "id": user_id,
            "email": "u1@auron.test",
            "role": role,
            "branch_id": "b-1",
        },
    }


def tenant_payload():
    return {
        "id": "t-1",
        "name": "Salon Uno",
        "subdomain": "salon-uno",
        "currency": "DOP",
    }


@pytest.mark.asyncio
async def test_resolve_builds_identity_from_auron():
    def handler(request):
        if request.url.path == "/api/auth/verify/":
            return httpx.Response(200, json=verify_payload())
        if request.url.path == "/api/tenants/current/":
            return httpx.Response(200, json=tenant_payload())
        return httpx.Response(404)

    provider = IdentityProvider(make_adapter(handler), cache_ttl=300)
    identity = await provider.resolve()

    assert identity.user_id == "u-1"
    assert identity.email == "u1@auron.test"
    assert identity.tenant_id == "t-1"
    assert identity.tenant_subdomain == "salon-uno"
    assert identity.tenant_currency == "DOP"
    assert identity.role == "CLIENT_ADMIN"
    assert identity.branch_id == "b-1"
    assert "pos_api.view_sale" in identity.permissions


@pytest.mark.asyncio
async def test_identity_is_cached_within_ttl():
    calls = {"verify": 0, "tenant": 0}

    def handler(request):
        if request.url.path == "/api/auth/verify/":
            calls["verify"] += 1
            return httpx.Response(200, json=verify_payload())
        if request.url.path == "/api/tenants/current/":
            calls["tenant"] += 1
            return httpx.Response(200, json=tenant_payload())
        return httpx.Response(404)

    provider = IdentityProvider(make_adapter(handler), cache_ttl=300)
    await provider.resolve()
    await provider.resolve()
    assert calls["verify"] == 1
    assert calls["tenant"] == 1


@pytest.mark.asyncio
async def test_expired_session_triggers_refresh_then_retry():
    verify_calls = {"n": 0}

    def handler(request):
        if request.url.path == "/api/auth/verify/":
            verify_calls["n"] += 1
            if verify_calls["n"] == 1:
                return httpx.Response(401, json={"detail": "token expired"})
            return httpx.Response(200, json=verify_payload())
        if request.url.path == "/api/auth/cookie-refresh/":
            return httpx.Response(200, json={"message": "refreshed"})
        if request.url.path == "/api/tenants/current/":
            return httpx.Response(200, json=tenant_payload())
        return httpx.Response(404)

    adapter = make_adapter(handler)
    adapter.client.cookies.set("refresh_token", "rt", domain="localhost", path="/")
    identity = await IdentityProvider(adapter).resolve()
    assert identity.user_id == "u-1"
    assert verify_calls["n"] == 2


@pytest.mark.asyncio
async def test_no_session_raises_identity_unavailable():
    def handler(request):
        return httpx.Response(401, json={"detail": "no token"})

    provider = IdentityProvider(make_adapter(handler))
    with pytest.raises(IdentityUnavailableError):
        await provider.resolve()


@pytest.mark.asyncio
async def test_non_superuser_without_tenant_is_rejected():
    def handler(request):
        if request.url.path == "/api/auth/verify/":
            return httpx.Response(200, json=verify_payload(role="CAJERA"))
        if request.url.path == "/api/tenants/current/":
            return httpx.Response(404, json={"error": "No tenant assigned"})
        return httpx.Response(404)

    provider = IdentityProvider(make_adapter(handler))
    with pytest.raises(IdentityUnavailableError):
        await provider.resolve()


@pytest.mark.asyncio
async def test_superuser_without_tenant_allowed_only_when_configured():
    def handler(request):
        if request.url.path == "/api/auth/verify/":
            return httpx.Response(200, json=verify_payload(role="SUPER_ADMIN"))
        if request.url.path == "/api/tenants/current/":
            return httpx.Response(404)
        return httpx.Response(404)

    adapter = make_adapter(handler)
    with pytest.raises(IdentityUnavailableError):
        await IdentityProvider(adapter).resolve()

    allowed = await IdentityProvider(
        adapter, allow_superuser_without_tenant=True
    ).resolve()
    assert allowed.role == "SUPER_ADMIN"
    assert allowed.tenant_id is None
