import pytest

from auron_mcp.auth import AuronIdentity
from auron_mcp.policy import AuthorizationError, PolicyEngine


def identity(**overrides):
    defaults = {
        "user_id": "u1",
        "email": "u1@auron.test",
        "tenant_id": "t1",
        "role": "CLIENT_ADMIN",
        "permissions": frozenset({"pos_api.view_sale"}),
    }
    defaults.update(overrides)
    return AuronIdentity(**defaults)


def test_permission_is_required():
    PolicyEngine().require_permission(identity(), "pos_api.view_sale")


def test_missing_permission_is_rejected():
    with pytest.raises(AuthorizationError):
        PolicyEngine().require_permission(identity(), "reports_api.view_kpi_dashboard")


def test_superuser_is_never_blocked_by_permission_table():
    PolicyEngine().require_permission(
        identity(permissions=frozenset(), is_superuser=True),
        "anything.not.in.table",
    )


def test_direct_tenant_override_is_rejected():
    # The model must never steer tenancy: requesting a different tenant than
    # the authenticated one is denied outright.
    with pytest.raises(AuthorizationError):
        PolicyEngine().resolve_tenant(identity(), "t2")


def test_same_tenant_override_is_allowed():
    assert PolicyEngine().resolve_tenant(identity(), "t1") == "t1"


def test_none_requested_uses_authenticated_tenant():
    assert PolicyEngine().resolve_tenant(identity(), None) == "t1"


def test_no_tenant_rejected_unless_superuser_allowed():
    no_tenant = identity(tenant_id=None, is_superuser=False)
    with pytest.raises(AuthorizationError):
        PolicyEngine().resolve_tenant(no_tenant, None)

    superuser = identity(tenant_id=None, is_superuser=True)
    assert (
        PolicyEngine(allow_superuser_without_tenant=True).resolve_tenant(
            superuser, None
        )
        is None
    )
    with pytest.raises(AuthorizationError):
        PolicyEngine(allow_superuser_without_tenant=False).resolve_tenant(
            superuser, None
        )
