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

Everything is in-memory: there is no external IdP or introspection endpoint.
The AURON session configured out-of-band is what maps each request to a
tenant; OAuth only gates access to the transport itself.

Spec: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization
"""

import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl, AnyUrl

from .config import settings

_DEFAULT_SCOPE = "mcp"


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
        subject: str = "auron-mcp",
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
            subject=self.subject,
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
        # rotate: the old refresh token is invalid after this exchange
        self._refresh_tokens.pop(refresh_token.token, None)
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

    async def revoke_token(self, token: str) -> None:
        self._access_tokens.pop(token, None)
        self._refresh_tokens.pop(token, None)

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
            subject=subject or self.subject,
        )
        self._refresh_tokens[refresh_token] = RefreshToken(
            token=refresh_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time() + self.refresh_token_ttl),
            subject=subject or self.subject,
        )
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
    provider = MemoryAuthServer(
        issuer_url=issuer_url,
        resource_server_url=resource_server_url,
        clients={client_id: client},
    )
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
