from typing import Any

import httpx2 as httpx

from .base import AdapterError, AuronAdapter


class HttpAuronAdapter(AuronAdapter):
    """AURON REST adapter.

    Authenticates the same way AURON's own clients do: either an `Authorization:
    Bearer <token>` header (AURON_API_TOKEN) or the httpOnly auth cookies set by
    `/api/auth/login/` (stored by `auron-manage login`). The AURON API derives
    the tenant from its own auth (TenantMiddleware), so this adapter NEVER sends
    a `tenant_id` param or an `X-Auron-Tenant` header. If it did, that value
    would be a model-influenced authorization signal — exactly what this gateway
    refuses to accept (see docs/mcp-integration-audit.md).
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 15.0,
        bearer_token: str = "",
        cookies: list[dict] | None = None,
    ):
        if not base_url:
            raise ValueError("AURON_API_BASE_URL is required for the HTTP adapter")
        self.base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, headers=headers)
        for cookie in cookies or []:
            self.client.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )

    # --- session helpers -------------------------------------------------
    def session_key(self) -> str:
        if self._bearer_token:
            return f"bearer:{self._bearer_token}"
        for cookie in self.client.cookies.jar:
            if cookie.name == "access_token":
                return f"cookie:{cookie.value}"
        return "anonymous"

    def can_refresh(self) -> bool:
        return not self._bearer_token and any(
            c.name == "refresh_token" for c in self.client.cookies.jar
        )

    def export_cookies(self) -> list[dict]:
        return [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
            for c in self.client.cookies.jar
        ]

    # --- auth / identity -------------------------------------------------
    async def auth_login(self, email: str, password: str, subdomain: str | None) -> dict[str, Any]:
        body: dict[str, Any] = {"email": email, "password": password}
        if subdomain:
            body["subdomain"] = subdomain
        response = await self._post("/api/auth/login/", body)
        if response.status_code == 200:
            return response.json()
        if response.status_code in (400, 401, 403):
            raise AdapterError("login_failed")
        if response.status_code == 429:
            raise AdapterError("rate_limited")
        raise AdapterError(f"upstream_error:{response.status_code}")

    async def auth_verify(self) -> dict[str, Any]:
        return await self._get("/api/auth/verify/")

    async def auth_refresh(self) -> bool:
        refresh = next(
            (c.value for c in self.client.cookies.jar if c.name == "refresh_token"),
            None,
        )
        body = {"refresh_token": refresh} if refresh else {}
        response = await self._post("/api/auth/cookie-refresh/", body)
        return response.status_code == 200

    async def tenant_current(self) -> dict[str, Any]:
        return await self._get("/api/tenants/current/")

    # --- business data ---------------------------------------------------
    async def sales_summary(self) -> dict[str, Any]:
        return await self._get("/api/pos/summary/daily/")

    async def appointments_today(self) -> dict[str, Any]:
        return await self._get("/api/appointments/appointments/today/")

    async def clients_search(self, query: str, limit: int) -> dict[str, Any]:
        return await self._get("/api/clients/clients/", search=query, limit=limit)

    async def inventory_alerts(self) -> dict[str, Any]:
        return await self._get("/api/inventory/alerts/low-stock/")

    async def employee_performance(self, employee_id: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if employee_id:
            params["employee_id"] = employee_id
        return await self._get("/api/reports/employees/", **params)

    async def services_list(self) -> dict[str, Any]:
        return await self._get("/api/services/services/")

    async def employees_list(self) -> dict[str, Any]:
        return await self._get("/api/employees/employees/")

    async def branches_list(self) -> dict[str, Any]:
        return await self._get("/api/settings/branches/")

    async def products_list(self) -> dict[str, Any]:
        return await self._get("/api/inventory/products/")

    async def dashboard_stats(self) -> dict[str, Any]:
        return await self._get("/api/pos/dashboard/stats/")

    async def system_health(self) -> dict[str, Any]:
        return await self._get("/api/healthz/")

    # --- controlled writes (M4) -------------------------------------------
    async def create_appointment(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._post("/api/appointments/appointments/", payload)
        return self._decode(response, success_codes=(200, 201))

    async def cancel_appointment(self, appointment_id: str) -> dict[str, Any]:
        response = await self._patch(
            f"/api/appointments/appointments/{appointment_id}/",
            {"status": "cancelled"},
        )
        return self._decode(response)

    async def update_appointment(
        self, appointment_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        response = await self._patch(f"/api/appointments/appointments/{appointment_id}/", payload)
        return self._decode(response)

    async def reschedule_appointment(
        self, appointment_id: str, new_datetime: str
    ) -> dict[str, Any]:
        response = await self._post(
            f"/api/appointments/appointments/{appointment_id}/reschedule/",
            {"new_datetime": new_datetime},
        )
        return self._decode(response)

    # --- plumbing --------------------------------------------------------
    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        try:
            response = await self.client.get(path, params=params or None)
        except httpx.RequestError:
            raise AdapterError("upstream_unavailable") from None
        return self._decode(response)

    async def _post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        try:
            return await self.client.post(path, json=body)
        except httpx.RequestError:
            raise AdapterError("upstream_unavailable") from None

    async def _patch(self, path: str, body: dict[str, Any]) -> httpx.Response:
        try:
            return await self.client.patch(path, json=body)
        except httpx.RequestError:
            raise AdapterError("upstream_unavailable") from None

    def _decode(
        self,
        response: httpx.Response,
        success_codes: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        if response.status_code in success_codes:
            try:
                return response.json()
            except ValueError:
                return {}
        if response.status_code in (401, 403):
            raise AdapterError("unauthorized") from None
        if response.status_code == 404:
            raise AdapterError("not_found") from None
        if response.status_code == 429:
            raise AdapterError("rate_limited") from None
        raise AdapterError(f"upstream_error:{response.status_code}") from None
