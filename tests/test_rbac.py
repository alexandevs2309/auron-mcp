from auron_mcp.policy.roles import (
    APPOINTMENTS_READ,
    BRANCHES_READ,
    CLIENTS_READ,
    EMPLOYEES_LIST_READ,
    EMPLOYEES_READ,
    PRODUCTS_READ,
    REPORTS_READ,
    SALES_READ,
    SERVICES_READ,
    permissions_for_role,
)


def test_client_admin_has_full_read_access():
    perms = permissions_for_role("CLIENT_ADMIN")
    assert SALES_READ in perms
    assert EMPLOYEES_READ in perms
    assert REPORTS_READ in perms
    assert SERVICES_READ in perms
    assert EMPLOYEES_LIST_READ in perms
    assert BRANCHES_READ in perms
    assert PRODUCTS_READ in perms


def test_cajera_cannot_read_employees_or_reports():
    perms = permissions_for_role("CAJERA")
    assert SALES_READ in perms
    assert EMPLOYEES_READ not in perms
    assert REPORTS_READ not in perms
    assert SERVICES_READ in perms
    assert EMPLOYEES_LIST_READ in perms
    assert BRANCHES_READ in perms
    assert PRODUCTS_READ in perms


def test_estilista_cannot_read_sales_or_products():
    perms = permissions_for_role("ESTILISTA")
    assert APPOINTMENTS_READ in perms
    assert CLIENTS_READ in perms
    assert SALES_READ not in perms
    assert SERVICES_READ in perms
    assert EMPLOYEES_LIST_READ in perms
    assert BRANCHES_READ in perms
    assert PRODUCTS_READ not in perms


def test_unknown_role_gets_no_permissions():
    assert permissions_for_role("SOMETHING_MADE_UP") == frozenset()


def test_superuser_gets_everything():
    assert permissions_for_role("anything", is_superuser=True) == permissions_for_role(
        "SUPERADMIN"
    )


def test_roles_are_not_all_identical():
    # Guards against every role silently getting the same full permission set.
    assert permissions_for_role("CAJERA") != permissions_for_role("CLIENT_ADMIN")
    assert permissions_for_role("ESTILISTA") != permissions_for_role("CAJERA")
