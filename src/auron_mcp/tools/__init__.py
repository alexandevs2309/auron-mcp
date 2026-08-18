import time

from ..adapters.base import AdapterError
from ..audit import AuditLogger
from ..config import settings
from ..context import RequestContext
from ..errors import ConfirmationError, WriteDisabledError
from ..logging import logger
from ..otel import span_tool
from ..policy import AuthorizationError, PolicyEngine
from ..policy.roles import (
    APPOINTMENTS_CANCEL,
    APPOINTMENTS_CHANGE,
    APPOINTMENTS_CREATE,
    APPOINTMENTS_READ,
    BRANCHES_READ,
    CLIENTS_READ,
    EMPLOYEES_LIST_READ,
    EMPLOYEES_READ,
    INVENTORY_READ,
    PRODUCTS_READ,
    SALES_READ,
    SERVICES_READ,
    SYSTEM_READ,
)
from ..ratelimit import RateLimiter, RateLimitError
from ..writes import ConfirmationTokenStore, IdempotencyStore, payload_hash

CLIENT_SEARCH_MAX_LIMIT = 100


def _outcome_from_exc(exc: Exception) -> str:
    if isinstance(exc, AdapterError):
        return exc.category
    if isinstance(exc, AuthorizationError):
        return "denied"
    if isinstance(exc, RateLimitError):
        return "rate_limited"
    if isinstance(exc, WriteDisabledError):
        return "read_only"
    if isinstance(exc, ConfirmationError):
        return f"confirmation_{exc.reason}"
    return "error"


class AuronTools:
    """Tool logic: policy + rate-limit + audit wrap every data read.

    Permission checks run before any adapter call and the identity driving them
    is always the authenticated AURON one — never a model-supplied value.
    """

    def __init__(
        self,
        adapter,
        policy: PolicyEngine,
        audit: AuditLogger,
        rate_limiter: RateLimiter | None = None,
        confirmations: ConfirmationTokenStore | None = None,
        idempotency: IdempotencyStore | None = None,
    ):
        self.adapter = adapter
        self.policy = policy
        self.audit = audit
        self.rate_limiter = rate_limiter
        self.confirmations = confirmations or ConfirmationTokenStore(
            ttl_seconds=settings.confirmation_ttl_seconds
        )
        self.idempotency = idempotency or IdempotencyStore(
            ttl_seconds=settings.idempotency_ttl_seconds,
            max_entries=settings.idempotency_max_entries,
        )

    async def _guard(self, ctx: RequestContext, tool: str, permission) -> None:
        if self.rate_limiter is not None:
            self.rate_limiter.check(ctx.identity.user_id, tool)
        required = {permission} if isinstance(permission, str) else permission
        for perm in required:
            self.policy.require_permission(ctx.identity, perm)

    def _record(
        self,
        ctx: RequestContext,
        tool: str,
        outcome: str,
        duration_ms: int,
        error: BaseException | None = None,
    ) -> None:
        self.audit.record(ctx, tool, outcome, duration_ms)
        span_tool(
            tool,
            ctx.trace_id,
            user_id=ctx.identity.user_id,
            tenant_id=ctx.identity.tenant_id or "",
            role=ctx.identity.role,
            outcome=outcome,
            duration_ms=duration_ms,
            error=error,
        )

    async def _call(
        self,
        ctx: RequestContext,
        tool: str,
        permission: str | frozenset[str],
        fn,
    ) -> dict:
        started = time.monotonic()
        try:
            await self._guard(ctx, tool, permission)
            result = await fn()
        except Exception as exc:
            outcome = _outcome_from_exc(exc)
            duration_ms = int((time.monotonic() - started) * 1000)
            self._record(ctx, tool, outcome, duration_ms, error=exc)
            logger.error(
                "tool.error",
                tool=tool,
                trace_id=ctx.trace_id,
                user_id=ctx.identity.user_id,
                tenant_id=ctx.identity.tenant_id or "",
                role=ctx.identity.role,
                outcome=outcome,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
            )
            raise
        self._record(ctx, tool, "success", int((time.monotonic() - started) * 1000))
        return result

    async def _write(
        self,
        ctx: RequestContext,
        tool: str,
        permission: str,
        fn,
        *,
        operation: str,
        payload: dict,
        idempotency_key: str | None = None,
        confirmation_token: str | None = None,
        compensation=None,
    ) -> dict:
        """Controlled-write boundary: read-only guard, permission, two-phase
        confirmation, idempotent replay and best-effort compensation."""
        started = time.monotonic()
        try:
            if settings.read_only:
                raise WriteDisabledError()
            await self._guard(ctx, tool, permission)
            if confirmation_token is not None:
                result = self.confirmations.consume(confirmation_token, operation, payload)
                if result != "ok":
                    raise ConfirmationError(result)
            if idempotency_key is not None:
                existing = self.idempotency.get(idempotency_key)
                if existing is not None:
                    self._record(ctx, tool, "idempotent", int((time.monotonic() - started) * 1000))
                    return existing
            try:
                result = await fn()
            except Exception:
                if compensation is not None:
                    try:
                        await compensation()
                    except Exception as comp_exc:  # noqa: BLE001 - best-effort
                        # Compensation is best-effort; the original error wins.
                        logger.warning(
                            "compensation.failed",
                            tool=tool,
                            trace_id=ctx.trace_id,
                            error_type=type(comp_exc).__name__,
                            error_message=str(comp_exc),
                        )
                raise
            if idempotency_key is not None:
                self.idempotency.put(idempotency_key, result)
        except Exception as exc:
            outcome = _outcome_from_exc(exc)
            duration_ms = int((time.monotonic() - started) * 1000)
            self._record(ctx, tool, outcome, duration_ms, error=exc)
            logger.error(
                "tool.error",
                tool=tool,
                trace_id=ctx.trace_id,
                user_id=ctx.identity.user_id,
                tenant_id=ctx.identity.tenant_id or "",
                role=ctx.identity.role,
                outcome=outcome,
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
            )
            raise
        self._record(ctx, tool, "success", int((time.monotonic() - started) * 1000))
        return result

    async def sales_summary(self, ctx: RequestContext) -> dict:
        return await self._call(ctx, "sales.summary.read", SALES_READ, self.adapter.sales_summary)

    async def appointments_today(self, ctx: RequestContext) -> dict:
        return await self._call(
            ctx, "appointments.today.read", APPOINTMENTS_READ, self.adapter.appointments_today
        )

    async def clients_search(self, ctx: RequestContext, query: str, limit: int = 20) -> dict:
        limit = max(1, min(int(limit), CLIENT_SEARCH_MAX_LIMIT))
        return await self._call(
            ctx,
            "clients.search.read",
            CLIENTS_READ,
            lambda: self.adapter.clients_search(query, limit),
        )

    async def inventory_alerts(self, ctx: RequestContext) -> dict:
        return await self._call(
            ctx, "inventory.alerts.read", INVENTORY_READ, self.adapter.inventory_alerts
        )

    async def employee_performance(
        self, ctx: RequestContext, employee_id: str | None = None
    ) -> dict:
        return await self._call(
            ctx,
            "employees.performance.read",
            EMPLOYEES_READ,
            lambda: self.adapter.employee_performance(employee_id),
        )

    def clients_search_factory(self, query: str, limit: int = 20):
        """Bound entry point for the MCP tool (keeps _safe around ctx)."""

        async def run(ctx: RequestContext):
            return await self.clients_search(ctx, query, limit)

        return run

    def employee_performance_factory(self, employee_id: str | None = None):
        async def run(ctx: RequestContext):
            return await self.employee_performance(ctx, employee_id)

        return run

    async def system_health(self, ctx: RequestContext) -> dict:
        return await self._call(ctx, "system.health.read", SYSTEM_READ, self.adapter.system_health)

    async def services_list(self, ctx: RequestContext) -> dict:
        return await self._call(
            ctx, "services.list.read", SERVICES_READ, self.adapter.services_list
        )

    async def employees_list(self, ctx: RequestContext) -> dict:
        return await self._call(
            ctx, "employees.list.read", EMPLOYEES_LIST_READ, self.adapter.employees_list
        )

    async def branches_list(self, ctx: RequestContext) -> dict:
        return await self._call(
            ctx, "branches.list.read", BRANCHES_READ, self.adapter.branches_list
        )

    async def products_list(self, ctx: RequestContext) -> dict:
        return await self._call(
            ctx, "products.list.read", PRODUCTS_READ, self.adapter.products_list
        )

    async def dashboard_stats(self, ctx: RequestContext) -> dict:
        return await self._call(
            ctx, "dashboard.stats.read", SYSTEM_READ, self.adapter.dashboard_stats
        )

    async def daily_review(self, ctx: RequestContext) -> dict:
        """Compound daily business review: sales, appointments, inventory,
        employee performance in a single read.

        Denied unless the identity holds EVERY required permission, matching
        what each individual read would demand.
        """
        return await self._call(
            ctx,
            "daily.review.read",
            frozenset({SALES_READ, APPOINTMENTS_READ, INVENTORY_READ, EMPLOYEES_READ}),
            self._run_daily_review,
        )

    async def _run_daily_review(self) -> dict:
        sales = await self.adapter.sales_summary()
        appointments = await self.adapter.appointments_today()
        alerts = await self.adapter.inventory_alerts()
        perf = await self.adapter.employee_performance(None)
        return {
            "sales": sales,
            "appointments_today": appointments,
            "inventory_alerts": alerts,
            "employee_performance": perf,
        }

    async def inventory_review(self, ctx: RequestContext) -> dict:
        """Inventory review: full product list plus low-stock alerts."""
        return await self._call(
            ctx,
            "inventory.review.read",
            INVENTORY_READ,
            self._run_inventory_review,
        )

    async def _run_inventory_review(self) -> dict:
        products = await self.adapter.products_list()
        alerts = await self.adapter.inventory_alerts()
        return {"products": products, "low_stock_alerts": alerts}

    async def revenue_analysis(self, ctx: RequestContext) -> dict:
        """Revenue analysis: dashboard stats plus today's sales summary."""
        return await self._call(
            ctx,
            "revenue.analysis.read",
            SALES_READ,
            self._run_revenue_analysis,
        )

    async def _run_revenue_analysis(self) -> dict:
        stats = await self.adapter.dashboard_stats()
        sales = await self.adapter.sales_summary()
        return {"dashboard": stats, "sales_today": sales}

    async def employee_performance_review(self, ctx: RequestContext) -> dict:
        """Employee review: performance report plus the employee roster."""
        return await self._call(
            ctx,
            "employees.review.read",
            frozenset({EMPLOYEES_READ, EMPLOYEES_LIST_READ}),
            self._run_employee_review,
        )

    async def _run_employee_review(self) -> dict:
        roster = await self.adapter.employees_list()
        perf = await self.adapter.employee_performance(None)
        return {"employees": roster, "performance": perf}

    # --- controlled writes (M4) -------------------------------------------
    async def preview_create_appointment(self, ctx: RequestContext, payload: dict) -> dict:
        """Phase 1 of create: returns the effect + a single-use token."""

        async def _preview():
            token = self.confirmations.issue("create_appointment", payload)
            return {
                "operation": "create_appointment",
                "payload": payload,
                "effects": "Creates a new appointment in the authenticated tenant",
                "confirmation_token": token,
                "ttl_seconds": self.confirmations.ttl_seconds,
                "payload_hash": payload_hash(payload),
            }

        return await self._write(
            ctx,
            "appointments.create.preview",
            APPOINTMENTS_CREATE,
            _preview,
            operation="create_appointment",
            payload=payload,
        )

    async def create_appointment(
        self,
        ctx: RequestContext,
        payload: dict,
        *,
        idempotency_key: str,
        confirmation_token: str,
    ) -> dict:
        """Phase 2 of create: requires the token issued by the preview."""

        async def _create():
            return await self.adapter.create_appointment(payload)

        return await self._write(
            ctx,
            "appointments.create",
            APPOINTMENTS_CREATE,
            _create,
            operation="create_appointment",
            payload=payload,
            idempotency_key=idempotency_key,
            confirmation_token=confirmation_token,
        )

    async def preview_cancel_appointment(self, ctx: RequestContext, appointment_id: str) -> dict:
        """Phase 1 of cancel: preview effect + single-use token."""

        async def _preview():
            payload = {"appointment_id": appointment_id}
            token = self.confirmations.issue("cancel_appointment", payload)
            return {
                "operation": "cancel_appointment",
                "appointment_id": appointment_id,
                "effects": "Marks the appointment as cancelled (record is kept)",
                "confirmation_token": token,
                "ttl_seconds": self.confirmations.ttl_seconds,
                "payload_hash": payload_hash(payload),
            }

        return await self._write(
            ctx,
            "appointments.cancel.preview",
            APPOINTMENTS_CANCEL,
            _preview,
            operation="cancel_appointment",
            payload={"appointment_id": appointment_id},
        )

    async def cancel_appointment(
        self,
        ctx: RequestContext,
        appointment_id: str,
        *,
        idempotency_key: str,
        confirmation_token: str,
    ) -> dict:
        """Phase 2 of cancel: token issued by the preview is required."""

        async def _cancel():
            return await self.adapter.cancel_appointment(appointment_id)

        payload = {"appointment_id": appointment_id}
        return await self._write(
            ctx,
            "appointments.cancel",
            APPOINTMENTS_CANCEL,
            _cancel,
            operation="cancel_appointment",
            payload=payload,
            idempotency_key=idempotency_key,
            confirmation_token=confirmation_token,
        )

    async def update_appointment(
        self,
        ctx: RequestContext,
        appointment_id: str,
        changes: dict,
        *,
        idempotency_key: str,
    ) -> dict:
        """Low-impact field update (service/branch/description). No confirmation."""

        async def _update():
            return await self.adapter.update_appointment(appointment_id, changes)

        payload = {"appointment_id": appointment_id, **changes}
        return await self._write(
            ctx,
            "appointments.update",
            APPOINTMENTS_CHANGE,
            _update,
            operation="update_appointment",
            payload=payload,
            idempotency_key=idempotency_key,
        )

    async def reschedule_appointment(
        self,
        ctx: RequestContext,
        appointment_id: str,
        new_datetime: str,
        *,
        idempotency_key: str,
    ) -> dict:
        """Move an appointment to a new datetime via the dedicated endpoint."""

        async def _reschedule():
            return await self.adapter.reschedule_appointment(appointment_id, new_datetime)

        payload = {"appointment_id": appointment_id, "new_datetime": new_datetime}
        return await self._write(
            ctx,
            "appointments.reschedule",
            APPOINTMENTS_CHANGE,
            _reschedule,
            operation="reschedule_appointment",
            payload=payload,
            idempotency_key=idempotency_key,
        )
