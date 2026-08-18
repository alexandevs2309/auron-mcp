from abc import ABC, abstractmethod
from typing import Any


class AdapterError(RuntimeError):
    """Safe-to-surface adapter failure.

    Only `category` (and an optional generic `detail`) may reach the model via
    the error boundary. Upstream response bodies, stack traces, tokens and
    internal URLs must never appear in messages — log them server-side only.
    """

    def __init__(self, category: str, detail: str = ""):
        self.category = category
        self.detail = detail
        super().__init__(category)


class AuronAdapter(ABC):
    """Business-data + auth adapter. Tools never touch raw HTTP/ORM details."""

    # --- auth / identity -------------------------------------------------
    @abstractmethod
    async def auth_verify(self) -> dict[str, Any]:
        """GET /api/auth/verify/ -> authenticated identity payload."""

    @abstractmethod
    async def auth_refresh(self) -> bool:
        """POST /api/auth/cookie-refresh/ -> True when cookies rotated."""

    @abstractmethod
    async def auth_login(
        self, email: str, password: str, subdomain: str | None
    ) -> dict[str, Any]:
        """POST /api/auth/login/ -> sets auth cookies."""

    @abstractmethod
    async def tenant_current(self) -> dict[str, Any]:
        """GET /api/tenants/current/ -> tenant scope for the session."""

    @abstractmethod
    def session_key(self) -> str:
        """Stable identifier of the current session for identity caching."""

    @abstractmethod
    def can_refresh(self) -> bool:
        """True when the session carries a refresh token (cookie-based auth)."""

    # --- business data ---------------------------------------------------
    @abstractmethod
    async def sales_summary(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def appointments_today(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def clients_search(self, query: str, limit: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def inventory_alerts(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def employee_performance(self, employee_id: str | None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def services_list(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def employees_list(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def branches_list(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def products_list(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def dashboard_stats(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def system_health(self) -> dict[str, Any]:
        raise NotImplementedError

    # --- controlled writes (M4) -------------------------------------------
    # Payloads are validated by the MCP tool layer (permissions, confirmation,
    # idempotency) BEFORE they reach the adapter; the adapter only talks to the
    # AURON API. Tenant is derived from AURON's own auth, never from a payload
    # field (the API's TenantMiddleware enforces it independently).
    @abstractmethod
    async def create_appointment(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/appointments/appointments/ -> created appointment."""

    @abstractmethod
    async def cancel_appointment(self, appointment_id: str) -> dict[str, Any]:
        """PATCH status -> cancelled; keeps the record for history."""

    @abstractmethod
    async def update_appointment(
        self, appointment_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """PATCH /api/appointments/appointments/{id}/ with field changes."""

    @abstractmethod
    async def reschedule_appointment(
        self, appointment_id: str, new_datetime: str
    ) -> dict[str, Any]:
        """POST /api/appointments/appointments/{id}/reschedule/."""
