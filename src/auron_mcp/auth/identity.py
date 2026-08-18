from dataclasses import dataclass


@dataclass(frozen=True)
class AuronIdentity:
    """Authenticated principal resolved from AURON, never from model input.

    Production identity always comes from a validated AURON session (Bearer
    token or auth cookies) via the IdentityProvider. The LLM cannot influence
    any of these fields; tenancy and role are authoritative AURON values.
    """

    user_id: str
    email: str
    tenant_id: str | None
    role: str
    tenant_currency: str = "DOP"
    tenant_subdomain: str | None = None
    branch_id: str | None = None
    is_superuser: bool = False
    permissions: frozenset[str] = frozenset()
