from ..auth.identity import AuronIdentity


class AuthorizationError(PermissionError):
    """Raised for missing permissions, cross-tenant attempts, or missing tenant."""


class PolicyEngine:
    """Policy boundary derived from the authenticated AURON identity.

    The model can never override tenant, user, role or permissions: those
    come from `AuronIdentity` resolved by the auth provider. Tenancy is
    enforced here AND by AURON's own `TenantMiddleware` + `TenantQuerySetMixin`.
    """

    def __init__(self, allow_superuser_without_tenant: bool = False):
        self._allow_superuser_without_tenant = allow_superuser_without_tenant

    def require_permission(self, identity: AuronIdentity, permission: str) -> None:
        if identity.is_superuser:
            return
        if permission not in identity.permissions:
            raise AuthorizationError(f"Permission denied for '{permission}'")

    def resolve_tenant(
        self, identity: AuronIdentity, requested_tenant: str | None = None
    ) -> str | None:
        """Return the authoritative tenant_id for the query.

        A model-supplied tenant is never accepted as authorization. If a
        caller insists on a different tenant than the authenticated one, the
        request is denied outright.
        """
        if requested_tenant is not None and requested_tenant != identity.tenant_id:
            raise AuthorizationError("Cross-tenant access denied")
        if identity.tenant_id is None:
            if identity.is_superuser and self._allow_superuser_without_tenant:
                return None
            raise AuthorizationError("No tenant assigned to this identity")
        return identity.tenant_id
