"""MC-07 — OAuth state compartido entre réplicas y ``subject`` por cliente."""

import base64
import hashlib
import os
from urllib.parse import parse_qs, urlparse

import pytest
from mcp.server.auth.provider import (
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from auron_mcp.oauth import (
    MemoryAuthServer,
    OpaqueTokenVerifier,
    RedisAuthServer,
    build_auth_dependencies,
)

ISSUER = "http://127.0.0.1:8765"
RESOURCE = "http://127.0.0.1:8765/mcp"
REDIRECT = "http://127.0.0.1:6274/api/callback"


class FakeRedis:
    """Async client mínimo compartido por varias instancias del proveedor."""

    def __init__(self, fail: bool = False):
        self.data: dict[str, str] = {}
        self.fail = fail

    def _check(self):
        if self.fail:
            raise RuntimeError("redis://user:super-secret@redis.internal:6379/0")

    async def get(self, key: str) -> str | None:
        self._check()
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._check()
        self.data[key] = value

    async def delete(self, *keys: str) -> int:
        self._check()
        deleted = 0
        for key in keys:
            if self.data.pop(key, None) is not None:
                deleted += 1
        return deleted

    def register_script(self, script: str):
        async def take(keys: list[str], args: list) -> str | None:
            self._check()
            return self.data.pop(keys[0], None)

        return take

    async def aclose(self) -> None:
        return None


def _client(client_id: str = "client-a") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="secret-de-prueba",
        redirect_uris=[AnyUrl(REDIRECT)],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        scope="mcp",
    )


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


def _params(client: OAuthClientInformationFull, challenge: str) -> AuthorizationParams:
    return AuthorizationParams(
        state="state-1",
        scopes=["mcp"],
        code_challenge=challenge,
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
    )


async def _authorize(provider, client: OAuthClientInformationFull) -> tuple[str, str]:
    verifier, challenge = _pkce()
    url = await provider.authorize(client, _params(client, challenge))
    code = parse_qs(urlparse(url).query)["code"][0]
    return code, verifier


def _replica(shared: FakeRedis) -> RedisAuthServer:
    return RedisAuthServer(
        issuer_url=ISSUER,
        resource_server_url=RESOURCE,
        client=shared,
        clients={"client-a": _client()},
    )


async def test_authorization_code_is_single_use_across_replicas():
    shared = FakeRedis()
    issuer, consumer = _replica(shared), _replica(shared)
    client = _client()
    code, _verifier = await _authorize(issuer, client)

    stored = await consumer.load_authorization_code(client, code)
    assert stored is not None and stored.code == code

    tokens = await consumer.exchange_authorization_code(client, stored)
    with pytest.raises(TokenError):
        await issuer.exchange_authorization_code(client, stored)

    verifier_obj = OpaqueTokenVerifier(issuer)
    assert (await verifier_obj.verify_token(tokens.access_token)) is not None


async def test_refresh_rotates_and_old_access_is_revoked_across_replicas():
    shared = FakeRedis()
    first, second = _replica(shared), _replica(shared)
    client = _client()
    code, _ = await _authorize(first, client)
    issued = await first.exchange_authorization_code(
        client, await first.load_authorization_code(client, code)
    )

    rotated = await second.exchange_refresh_token(
        client,
        await second.load_refresh_token(client, issued.refresh_token),
        ["mcp"],
    )
    assert rotated.refresh_token != issued.refresh_token
    assert await first.load_refresh_token(client, issued.refresh_token) is None
    assert await OpaqueTokenVerifier(first).verify_token(issued.access_token) is None
    assert (await OpaqueTokenVerifier(first).verify_token(rotated.access_token)) is not None


async def test_oauth_subject_is_derived_from_client_not_fixed():
    shared = FakeRedis()
    provider = _replica(shared)
    other = _client("client-b")
    await provider.register_client(other)

    code_a, _ = await _authorize(provider, _client())
    code_b, _ = await _authorize(provider, other)
    subject_a = (await provider.load_authorization_code(_client(), code_a)).subject
    subject_b = (await provider.load_authorization_code(other, code_b)).subject

    assert subject_a != subject_b
    assert "client-a" in subject_a and "client-b" in subject_b
    assert "auron-mcp" not in subject_a


async def test_memory_provider_remains_single_replica_only():
    """Sin Redis, una segunda réplica no ve el código: requiere sticky sessions."""
    client = _client()
    first = MemoryAuthServer(
        issuer_url=ISSUER, resource_server_url=RESOURCE, clients={"client-a": client}
    )
    second = MemoryAuthServer(
        issuer_url=ISSUER, resource_server_url=RESOURCE, clients={"client-a": client}
    )
    code, _ = await _authorize(first, client)
    assert await second.load_authorization_code(client, code) is None


async def test_redis_failure_fails_closed_without_leaking_connection_details():
    shared = FakeRedis(fail=True)
    provider = RedisAuthServer(
        issuer_url=ISSUER,
        resource_server_url=RESOURCE,
        client=shared,
        clients={"client-a": _client()},
    )
    assert await provider.load_access_token("opaque-token") is None
    assert await provider.get_client("client-a") is not None  # config local
    with pytest.raises(AuthorizeError) as exc:
        await _authorize(provider, _client())
    assert "super-secret" not in str(exc.value)
    assert "redis.internal" not in str(exc.value)
    stale = RefreshToken(token="fake-refresh", client_id="client-a", scopes=["mcp"])
    with pytest.raises(TokenError) as token_exc:
        await provider.exchange_refresh_token(_client(), stale, ["mcp"])
    assert "super-secret" not in str(token_exc.value)


def test_build_auth_dependencies_selects_shared_provider_when_redis_is_configured():
    auth, provider, verifier = build_auth_dependencies(
        enabled=True,
        issuer_url=ISSUER,
        resource_server_url=RESOURCE,
        client_id="client-a",
        client_secret="secret-de-prueba",
        redirect_uris=(REDIRECT,),
        redis_client=FakeRedis(),
    )
    assert auth is not None and verifier is not None
    assert isinstance(provider, RedisAuthServer)
