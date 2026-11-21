"""OAuth 2.1 authorization server + resource-server token verification.

Implements the MCP Authorization spec (2026-07-28) subset a read-only,
LAN-bound MCP endpoint needs, built on the `mcp` SDK auth components:

* OAuth 2.0 Protected Resource Metadata (RFC 9728) and Authorization Server
  Metadata (RFC 8414) - the SDK serves both documents.
* Authorization Code flow with mandatory PKCE S256 (RFC 7636) against a
  pre-registered confidential client (dynamic client registration, RFC 7591,
  is supported by the SDK but off by default).
* Opaque, audience-bound access tokens (RFC 8707 resource indicator) that the
  resource server validates; refresh tokens rotate on use.

There is no external IdP or introspection endpoint. The AURON session
configured out-of-band is what maps each request to a tenant; OAuth only gates
access to the transport itself.

State has two modes (MC-07):

* Without ``AURON_REDIS_URL``: :class:`MemoryAuthServer` (single replica, sticky
  sessions required; documented limitation).
* With ``AURON_REDIS_URL``: :class:`RedisAuthServer`, codes/access/refresh
  tokens shared by every replica, single-use enforced atomically and fail-closed
  when Redis is unavailable.

The OAuth ``sub`` is derived from ``client_id``; there is no shared fixed
subject across clients.

Spec: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
"""

import hashlib
import json
import secrets
import time
from typing import Any

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl

from .config import settings
from .logging import logger
from .writes import _TAKE_LUA, SharedStateUnavailableError, _redis_client

_DEFAULT_SCOPE = "mcp"
_OAUTH_PREFIX = "auron:mcp:oauth:v1"


def _oauth_prefix(issuer_url: str) -> str:
    """Isolate deployments that share one Redis instance."""
    return f"{_OAUTH_PREFIX}:{hashlib.sha256(issuer_url.encode()).hexdigest()[:16]}"


class MemoryAuthServer(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """In-memory OAuth 2.1 authorization server.

    Issues opaque, audience-bound tokens for pre-registered clients and
    validates them on the resource-server side (``load_access_token``).
    """

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_server_url: str,
        clients: dict[str, OAuthClientInformationFull] | None = None,
        subject: str | None = None,
        access_token_ttl: int = 3600,
        refresh_token_ttl: int = 7 * 86400,
        code_ttl: int = 600,
    ) -> None:
        self.issuer_url = issuer_url
        self.resource_server_url = resource_server_url
        self.subject = subject
        self.access_token_ttl = access_token_ttl
        self.refresh_token_ttl = refresh_token_ttl
        self.code_ttl = code_ttl
        self._clients: dict[str, OAuthClientInformationFull] = dict(clients or {})
        self._codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        self._token_pairs: dict[str, str] = {}

    def _subject_for(self, client_id: str) -> str:
        """MC-07: never issue a global fixed subject for every client."""
        return self.subject or f"client:{client_id}"

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource is not None and params.resource != self.resource_server_url:
            raise AuthorizeError(
                "invalid_request",
                f"Unsupported resource: {params.resource}",
            )
        scopes = params.scopes or [_DEFAULT_SCOPE]
        code = secrets.token_urlsafe(32)
        self._codes[code] = AuthorizationCode(
            client_id=client.client_id,
            code=code,
            code_challenge=params.code_challenge,
            expires_at=int(time.time() + self.code_ttl),
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            scopes=scopes,
            subject=self._subject_for(client.client_id),
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        stored = self._codes.get(authorization_code)
        if stored is None or stored.client_id != client.client_id:
            return None
        return stored

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        # single-use: the handler already validated existence/expiry/PKCE
        self._codes.pop(authorization_code.code, None)
        return self._issue_tokens(
            client.client_id,
            authorization_code.scopes,
            authorization_code.resource,
        )

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        stored = self._refresh_tokens.get(refresh_token)
        if stored is None or stored.client_id != client.client_id:
            return None
        return stored

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # rotate: the old refresh token and its access token stop working
        self._refresh_tokens.pop(refresh_token.token, None)
        old_access = self._token_pairs.pop(refresh_token.token, None)
        if old_access:
            self._token_pairs.pop(old_access, None)
            self._access_tokens.pop(old_access, None)
        return self._issue_tokens(client.client_id, scopes, None, subject=refresh_token.subject)

    async def load_access_token(self, token: str) -> AccessToken | None:
        stored = self._access_tokens.get(token)
        if stored is None:
            return None
        if stored.expires_at is not None and stored.expires_at < time.time():
            self._access_tokens.pop(token, None)
            return None
        if stored.resource is not None and stored.resource != self.resource_server_url:
            return None
        return stored

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke the presented token (and its counterpart), per the SDK contract."""
        presented = token.token
        self._access_tokens.pop(presented, None)
        self._refresh_tokens.pop(presented, None)
        counterpart = self._token_pairs.pop(presented, None)
        if counterpart:
            self._token_pairs.pop(counterpart, None)
            self._access_tokens.pop(counterpart, None)
            self._refresh_tokens.pop(counterpart, None)

    def _issue_tokens(
        self,
        client_id: str,
        scopes: list[str],
        resource: str | None,
        subject: str | None = None,
    ) -> OAuthToken:
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        self._access_tokens[access_token] = AccessToken(
            token=access_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time() + self.access_token_ttl),
            resource=resource or self.resource_server_url,
            subject=subject or self._subject_for(client_id),
        )
        self._refresh_tokens[refresh_token] = RefreshToken(
            token=refresh_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time() + self.refresh_token_ttl),
            subject=subject or self._subject_for(client_id),
        )
        self._token_pairs[access_token] = refresh_token
        self._token_pairs[refresh_token] = access_token
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self.access_token_ttl,
            scope=" ".join(scopes),
            refresh_token=refresh_token,
        )


class RedisAuthServer(MemoryAuthServer):
    """OAuth state shared by all gateway replicas (requires the ``redis`` extra).

    Authorization codes and refresh tokens are taken with GET+DEL in one Lua
    step, so a code cannot be exchanged twice even if two replicas load it
    concurrently. Redis errors deny the request; no token or connection detail
    reaches the caller. Falls back to no in-memory state at all when configured.
    """

    def __init__(
        self,
        *,
        issuer_url: str,
        resource_server_url: str,
        redis_url: str = "",
        client: Any | None = None,
        clients: dict[str, OAuthClientInformationFull] | None = None,
        subject: str | None = None,
        access_token_ttl: int = 3600,
        refresh_token_ttl: int = 7 * 86400,
        code_ttl: int = 600,
        timeout_seconds: float = 1.0,
    ) -> None:
        super().__init__(
            issuer_url=issuer_url,
            resource_server_url=resource_server_url,
            clients=clients,
            subject=subject,
            access_token_ttl=access_token_ttl,
            refresh_token_ttl=refresh_token_ttl,
            code_ttl=code_ttl,
        )
        self._redis = client or _redis_client(redis_url, timeout_seconds)
        self._take = self._redis.register_script(_TAKE_LUA)
        self._prefix = _oauth_prefix(issuer_url)

    @staticmethod
    def _digest(value: str) -> str:
        # Tokens are secrets: never put the raw value in a Redis key.
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]

    def _key(self, kind: str, value: str) -> str:
        return f"{self._prefix}:{kind}:{self._digest(value)}"

    async def _store(
        self, kind: str, value: str, model: Any, *, pair: str | None = None, ttl: int
    ) -> None:
        record = json.dumps({"model": model.model_dump_json(), "pair": pair})
        try:
            await self._redis.set(self._key(kind, value), record, ex=ttl)
        except Exception as exc:
            # OAuth state must fail closed; str(exc) may carry Redis credentials.
            logger.error("oauth.redis_unavailable", error_type=type(exc).__name__)
            raise SharedStateUnavailableError("OAuth shared state is unavailable") from exc

    async def _load(
        self, kind: str, value: str, model_cls: Any, *, take: bool = False
    ) -> tuple[Any, dict] | None:
        key = self._key(kind, value)
        try:
            raw = await self._take(keys=[key], args=[]) if take else await self._redis.get(key)
        except Exception as exc:  # noqa: BLE001 - deny, never leak the Redis URL
            logger.error("oauth.redis_unavailable", error_type=type(exc).__name__)
            return None
        if not raw:
            return None
        try:
            record = json.loads(raw)
            return model_cls.model_validate_json(record["model"]), record
        except (ValueError, KeyError, TypeError):
            return None

    async def aclose(self) -> None:
        await self._redis.aclose()

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        configured = await super().get_client(client_id)
        if configured is not None:
            return configured
        loaded = await self._load("client", client_id, OAuthClientInformationFull)
        return loaded[0] if loaded else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        await self._store(
            "client",
            client_info.client_id,
            client_info,
            ttl=self.refresh_token_ttl,
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource is not None and params.resource != self.resource_server_url:
            raise AuthorizeError("invalid_request", f"Unsupported resource: {params.resource}")
        scopes = params.scopes or [_DEFAULT_SCOPE]
        code = secrets.token_urlsafe(32)
        record = AuthorizationCode(
            client_id=client.client_id,
            code=code,
            code_challenge=params.code_challenge,
            expires_at=int(time.time() + self.code_ttl),
            redirect_uri=str(params.redirect_uri),
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            scopes=scopes,
            subject=self._subject_for(client.client_id),
        )
        try:
            await self._store("code", code, record, ttl=self.code_ttl)
        except SharedStateUnavailableError as exc:
            raise AuthorizeError("server_error", "Authorization state unavailable") from exc
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        loaded = await self._load("code", authorization_code, AuthorizationCode)
        if loaded is None or loaded[0].client_id != client.client_id:
            return None
        return loaded[0]

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # GET+DEL: only one replica can win this exchange.
        loaded = await self._load("code", authorization_code.code, AuthorizationCode, take=True)
        if (
            loaded is None
            or loaded[0].client_id != client.client_id
            or loaded[0].expires_at < time.time()
        ):
            raise TokenError("invalid_grant", "Authorization code already used or unknown")
        code = loaded[0]
        return await self._issue_tokens(client.client_id, code.scopes, code.resource, subject=code.subject)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        loaded = await self._load("refresh", refresh_token, RefreshToken)
        if loaded is None or loaded[0].client_id != client.client_id:
            return None
        return loaded[0]

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        loaded = await self._load("refresh", refresh_token.token, RefreshToken, take=True)
        if loaded is None or loaded[0].client_id != client.client_id:
            raise TokenError("invalid_grant", "Refresh token already used or unknown")
        if loaded[1].get("pair"):
            try:
                await self._redis.delete(self._key("access", loaded[1]["pair"]))
            except Exception as exc:  # noqa: BLE001
                logger.error("oauth.redis_unavailable", error_type=type(exc).__name__)
        return await self._issue_tokens(
            client.client_id, scopes, None, subject=refresh_token.subject
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        loaded = await self._load("access", token, AccessToken)
        if loaded is None:
            return None
        stored = loaded[0]
        if stored.expires_at is not None and stored.expires_at < time.time():
            return None
        if stored.resource is not None and stored.resource != self.resource_server_url:
            return None
        return stored

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        for kind, model_cls, counterpart in (
            ("access", AccessToken, "refresh"),
            ("refresh", RefreshToken, "access"),
        ):
            loaded = await self._load(kind, token.token, model_cls)
            if loaded is None:
                continue
            keys = [self._key(kind, token.token)]
            pair = loaded[1].get("pair")
            if pair:
                keys.append(self._key(counterpart, pair))
            try:
                await self._redis.delete(*keys)
            except Exception as exc:  # noqa: BLE001
                logger.error("oauth.redis_unavailable", error_type=type(exc).__name__)
            return

    async def _issue_tokens(
        self,
        client_id: str,
        scopes: list[str],
        resource: str | None,
        subject: str | None = None,
    ) -> OAuthToken:
        # Async override of the memory helper: names are tokens, Redis is shared.
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        subject = subject or self._subject_for(client_id)
        access = AccessToken(
            token=access_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time() + self.access_token_ttl),
            resource=resource or self.resource_server_url,
            subject=subject,
        )
        refresh = RefreshToken(
            token=refresh_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time() + self.refresh_token_ttl),
            subject=subject,
        )
        await self._store("access", access_token, access, pair=refresh_token, ttl=self.access_token_ttl)
        await self._store("refresh", refresh_token, refresh, pair=access_token, ttl=self.refresh_token_ttl)
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_in=self.access_token_ttl,
            scope=" ".join(scopes),
            refresh_token=refresh_token,
        )


class OpaqueTokenVerifier:
    """Resource-server token verifier backed by :class:`MemoryAuthServer`."""

    def __init__(self, provider: MemoryAuthServer) -> None:
        self.provider = provider

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self.provider.load_access_token(token)


def _build_client(
    client_id: str,
    client_secret: str,
    redirect_uris: tuple[str, ...],
    scopes: tuple[str, ...],
) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uris=[AnyUrl(u) for u in redirect_uris] or None,
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        scope=" ".join(scopes),
    )


def build_auth_dependencies(
    *,
    enabled: bool | None = None,
    issuer_url: str | None = None,
    resource_server_url: str | None = None,
    service_documentation_url: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uris: tuple[str, ...] | None = None,
    dynamic_client_registration: bool | None = None,
    required_scopes: tuple[str, ...] = (_DEFAULT_SCOPE,),
    redis_client: Any | None = None,
) -> tuple[AuthSettings | None, MemoryAuthServer | None, OpaqueTokenVerifier | None]:
    """Build the OAuth stack from settings (or explicit overrides for tests)."""
    if enabled is None:
        enabled = settings.oauth_enabled
    if not enabled:
        return None, None, None

    issuer_url = issuer_url or settings.oauth_issuer_url
    resource_server_url = resource_server_url or settings.oauth_resource_url
    service_documentation_url = (
        service_documentation_url or settings.oauth_service_documentation_url
    )
    client_id = client_id or settings.oauth_client_id
    client_secret = client_secret or settings.oauth_client_secret
    redirect_uris = (
        redirect_uris if redirect_uris is not None else settings.oauth_client_redirect_uris
    )
    if dynamic_client_registration is None:
        dynamic_client_registration = settings.oauth_dynamic_client_registration

    client = _build_client(client_id, client_secret, redirect_uris, required_scopes)
    common = {
        "issuer_url": issuer_url,
        "resource_server_url": resource_server_url,
        "clients": {client_id: client},
    }
    if settings.redis_url or redis_client is not None:
        # MC-07: share OAuth state when Redis is configured; otherwise the
        # caller must pin replicas to one process (sticky sessions).
        provider: MemoryAuthServer = RedisAuthServer(
            **common,
            redis_url=settings.redis_url,
            client=redis_client,
            timeout_seconds=settings.redis_timeout_seconds,
        )
    else:
        provider = MemoryAuthServer(**common)
    auth = AuthSettings(
        issuer_url=AnyHttpUrl(issuer_url),
        service_documentation_url=(
            AnyHttpUrl(service_documentation_url) if service_documentation_url else None
        ),
        resource_server_url=AnyHttpUrl(resource_server_url),
        required_scopes=list(required_scopes),
        client_registration_options=ClientRegistrationOptions(
            enabled=dynamic_client_registration,
            valid_scopes=list(required_scopes),
            default_scopes=list(required_scopes),
        ),
    )
    return auth, provider, OpaqueTokenVerifier(provider)
