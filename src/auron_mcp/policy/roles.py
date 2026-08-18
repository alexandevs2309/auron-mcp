"""MCP-level tool permissions mirroring AURON's real RBAC codenames.

The strings here are the exact Django codenames AURON enforces in its
permission classes (e.g. `pos_api.view_sale`). This table mirrors the
canonical roles of AURON (`apps/roles_api`); it is a fail-closed stand-in
so the MCP does not duplicate business logic. The ground truth remains
AURON's own RBAC: if AURON denies a call (403), the MCP reports it as an
authorization failure regardless of this table.
"""

SALES_READ = "pos_api.view_sale"
APPOINTMENTS_READ = "appointments_api.view_appointment"
CLIENTS_READ = "clients_api.view_client"
INVENTORY_READ = "inventory_api.view_product"
EMPLOYEES_READ = "reports_api.view_employee_reports"
REPORTS_READ = "reports_api.view_kpi_dashboard"
SERVICES_READ = "services_api.view_service"
EMPLOYEES_LIST_READ = "employees_api.view_employee"
BRANCHES_READ = "settings_api.view_branch"
PRODUCTS_READ = "inventory_api.view_product"
SYSTEM_READ = "system:read"

# Controlled writes (M4). Codename for the backend AppointmentViewSet /
# Appointment.Meta.permissions; AURON's own TenantPermissionByAction remains the
# ground truth — the MCP fails closed when AURON denies.
APPOINTMENTS_CREATE = "appointments_api.add_appointment"
APPOINTMENTS_CHANGE = "appointments_api.change_appointment"
APPOINTMENTS_CANCEL = "appointments_api.cancel_appointment"
APPOINTMENTS_DELETE = "appointments_api.delete_appointment"

WRITE_PERMISSIONS = frozenset(
    {
        APPOINTMENTS_CREATE,
        APPOINTMENTS_CHANGE,
        APPOINTMENTS_CANCEL,
        APPOINTMENTS_DELETE,
    }
)

FRONT_WRITE_PERMISSIONS = frozenset(
    {APPOINTMENTS_CREATE, APPOINTMENTS_CHANGE, APPOINTMENTS_CANCEL}
)

READ_PERMISSIONS = frozenset(
    {
        SALES_READ,
        APPOINTMENTS_READ,
        CLIENTS_READ,
        INVENTORY_READ,
        EMPLOYEES_READ,
        REPORTS_READ,
        SERVICES_READ,
        EMPLOYEES_LIST_READ,
        BRANCHES_READ,
        PRODUCTS_READ,
        SYSTEM_READ,
    }
)

_CATALOG_READ = frozenset(
    {SALES_READ, APPOINTMENTS_READ, CLIENTS_READ, INVENTORY_READ, SYSTEM_READ}
)
_FRONT_READ = frozenset(
    {
        SALES_READ,
        APPOINTMENTS_READ,
        CLIENTS_READ,
        INVENTORY_READ,
        SERVICES_READ,
        EMPLOYEES_LIST_READ,
        BRANCHES_READ,
        PRODUCTS_READ,
        SYSTEM_READ,
    }
)
_PROFESSIONAL_READ = frozenset(
    {
        APPOINTMENTS_READ,
        CLIENTS_READ,
        SERVICES_READ,
        EMPLOYEES_LIST_READ,
        BRANCHES_READ,
        SYSTEM_READ,
    }
)

_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "SUPERADMIN": READ_PERMISSIONS | WRITE_PERMISSIONS,
    "SUPER_ADMIN": READ_PERMISSIONS | WRITE_PERMISSIONS,
    "INTERNAL_SUPPORT": READ_PERMISSIONS | WRITE_PERMISSIONS,
    "CLIENT_ADMIN": READ_PERMISSIONS | WRITE_PERMISSIONS,
    "OWNER": READ_PERMISSIONS | WRITE_PERMISSIONS,
    "MANAGER": READ_PERMISSIONS | WRITE_PERMISSIONS,
    "CAJERA": _FRONT_READ | FRONT_WRITE_PERMISSIONS,
    "FRONTDESK_CASHIER": _FRONT_READ | FRONT_WRITE_PERMISSIONS,
    "ESTILISTA": _PROFESSIONAL_READ,
    "PROFESSIONAL": _PROFESSIONAL_READ,
    "CLIENT_STAFF": frozenset(
        {
            APPOINTMENTS_READ,
            CLIENTS_READ,
            INVENTORY_READ,
            SERVICES_READ,
            EMPLOYEES_LIST_READ,
            BRANCHES_READ,
            SYSTEM_READ,
        }
    ),
    "UTILITY": frozenset(
        {APPOINTMENTS_READ, CLIENTS_READ, SERVICES_READ, EMPLOYEES_LIST_READ, BRANCHES_READ, SYSTEM_READ}
    ),
}


def permissions_for_role(role: str, is_superuser: bool = False) -> frozenset[str]:
    """Fail-closed role -> permission resolution.

    Superusers get every read and write permission. Unknown roles get nothing.
    """
    if is_superuser:
        return READ_PERMISSIONS | WRITE_PERMISSIONS
    key = (role or "").strip().upper().replace("-", "_")
    return _ROLE_PERMISSIONS.get(key, frozenset())
