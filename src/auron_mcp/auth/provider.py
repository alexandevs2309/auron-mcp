import hashlib
import time

from ..adapters.base import AdapterError
from ..policy.roles import permissions_for_role
from .identity import AuronIdentity


class IdentityUnavailableError(RuntimeError):
    """No valid AURON identity could be resolved for the current session.

    Raised when there is no session, the session is expired/revoked, or the
    authenticated principal has no tenant. Message text is safe to surface to
    the model (it never contains tokens or internal details).
    """


class IdentityProvider:
    """Resolves the authenticated AuronIdentity from the real AURON API.

    Calls ``GET /api/auth/verify/`` and ``GET /api/tenants/current/`` through
    the adapter (Bearer token or the access_token cookie). The identity is
    cached briefly per session and is derived strictly from AURON, so a model
    can never fabricate a tenant, user, role or permission.
    """

    def __init__(
        self,
        adapter,
        cache_ttl: float = 300.0,
        allow_superuser_without_tenant: bool = False,
    ):
        self._adapter = adapter
        self._cache_ttl = cache_ttl
        self._allow_superuser_without_tenant = allow_superuser_without_tenant
        self._cache: dict[str, tuple[float, AuronIdentity]] = {}

    async def resolve(self) -> AuronIdentity:
        key = self._adapter.session_key()
        hit = self._cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < self._cache_ttl:
            return hit[1]
        identity = await self._fetch()
        self._cache[key] = (time.monotonic(), identity)
        return identity

    async def _fetch(self) -> AuronIdentity:
        verify = await self._verify_with_refresh()
        user = verify.get("user") or {}
        role = (user.get("role") or "UNKNOWN").strip().upper()
        is_superuser = role in ("SUPERADMIN", "SUPER_ADMIN")

        tenant_id: str | None = None
        tenant_currency = "DOP"
        tenant_subdomain: str | None = None
        try:
            tenant = await self._adapter.tenant_current()
            if tenant:
                tenant_id = str(tenant["id"]) if tenant.get("id") else None
                tenant_currency = str(tenant.get("currency") or "DOP").upper()
                tenant_subdomain = tenant.get("subdomain")
        except AdapterError:
            tenant_id = None

        if tenant_id is None and not (
            is_superuser and self._allow_superuser_without_tenant
        ):
            raise IdentityUnavailableError("No tenant assigned to this identity")

        branch_id = user.get("branch_id")
        return AuronIdentity(
            user_id=str(user.get("id") or ""),
            email=user.get("email") or "",
            tenant_id=tenant_id,
            tenant_currency=tenant_currency,
            tenant_subdomain=tenant_subdomain,
            role=role,
            branch_id=str(branch_id) if branch_id else None,
            is_superuser=is_superuser,
            permissions=permissions_for_role(role, is_superuser),
        )

    async def _verify_with_refresh(self) -> dict:
        try:
            return await self._adapter.auth_verify()
        except AdapterError as exc:
            if exc.category == "unauthorized" and self._adapter.can_refresh():
                refreshed = await self._adapter.auth_refresh()
                if not refreshed:
                    raise IdentityUnavailableError(
                        "Session expired; run `auron-manage login`"
                    ) from None
                return await self._adapter.auth_verify()
            raise IdentityUnavailableError(
                "Not authenticated; run `auron-manage login`"
            ) from None

    @staticmethod
    def _token_key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
