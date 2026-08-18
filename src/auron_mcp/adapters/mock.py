from typing import Any

from .base import AdapterError, AuronAdapter


class MockAuronAdapter(AuronAdapter):
    """Deterministic local adapter for MCP development and tests."""

    def __init__(self, email: str = "dev@auron.test", role: str = "CLIENT_ADMIN"):
        self._email = email
        self._role = role
        self._appointments: dict[str, dict[str, Any]] = {}
        self._next_appointment_id = 1

    async def create_appointment(self, payload: dict[str, Any]):
        required = ["client", "stylist", "date_time"]
        missing = [field for field in required if not payload.get(field)]
        if missing:
            raise AdapterError(
                "validation_failed", f"Missing required fields: {', '.join(missing)}"
            )
        appointment = {
            "id": str(self._next_appointment_id),
            "status": "scheduled",
            "tenant_id": "t-mock-1",
            "client": payload.get("client"),
            "stylist": payload.get("stylist"),
            "service": payload.get("service"),
            "branch": payload.get("branch"),
            "description": payload.get("description"),
            "date_time": payload.get("date_time"),
        }
        self._next_appointment_id += 1
        self._appointments[appointment["id"]] = appointment
        return appointment

    async def cancel_appointment(self, appointment_id: str):
        appointment = self._appointments.get(str(appointment_id))
        if appointment is None:
            raise AdapterError("not_found", "Appointment not found")
        appointment["status"] = "cancelled"
        return appointment

    async def update_appointment(self, appointment_id: str, payload: dict[str, Any]):
        appointment = self._appointments.get(str(appointment_id))
        if appointment is None:
            raise AdapterError("not_found", "Appointment not found")
        for field in ("service", "branch", "description"):
            if field in payload:
                appointment[field] = payload[field]
        return appointment

    async def reschedule_appointment(self, appointment_id: str, new_datetime: str):
        appointment = self._appointments.get(str(appointment_id))
        if appointment is None:
            raise AdapterError("not_found", "Appointment not found")
        appointment["date_time"] = new_datetime
        return appointment

    # --- auth / identity -------------------------------------------------
    async def auth_verify(self) -> dict[str, Any]:
        return {
            "authenticated": True,
            "user": {
                "id": "u-mock-1",
                "email": self._email,
                "role": self._role,
                "branch_id": None,
            },
        }

    async def auth_refresh(self) -> bool:
        return False

    async def auth_login(self, email: str, password: str, subdomain: str | None) -> dict[str, Any]:
        raise AdapterError("login_failed", "Mock adapter has no login")

    async def tenant_current(self) -> dict[str, Any]:
        return {
            "id": "t-mock-1",
            "name": "Mock Salon",
            "subdomain": "mock",
            "currency": "DOP",
        }

    def session_key(self) -> str:
        return "mock"

    def can_refresh(self) -> bool:
        return False

    # --- business data ---------------------------------------------------
    async def sales_summary(self) -> dict[str, Any]:
        return {
            "currency": "DOP",
            "period": "today",
            "sales_count": 0,
            "total": "0.00",
            "status": "mock",
        }

    async def appointments_today(self) -> dict[str, Any]:
        return {"appointments": [], "status": "mock"}

    async def clients_search(self, query: str, limit: int) -> dict[str, Any]:
        return {"query": query, "results": [], "limit": limit, "status": "mock"}

    async def inventory_alerts(self) -> dict[str, Any]:
        return {"alerts": [], "status": "mock"}

    async def employee_performance(self, employee_id: str | None) -> dict[str, Any]:
        return {"employee_id": employee_id, "results": [], "status": "mock"}

    async def services_list(self) -> dict[str, Any]:
        return {"services": [], "status": "mock"}

    async def employees_list(self) -> dict[str, Any]:
        return {"employees": [], "status": "mock"}

    async def branches_list(self) -> dict[str, Any]:
        return {"branches": [], "status": "mock"}

    async def products_list(self) -> dict[str, Any]:
        return {"products": [], "status": "mock"}

    async def dashboard_stats(self) -> dict[str, Any]:
        return {"currency": "DOP", "status": "mock"}

    async def system_health(self) -> dict[str, Any]:
        return {"status": "ok", "adapter": "mock", "service": "auron-mcp"}
