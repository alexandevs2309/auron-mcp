import sys
import traceback
from collections.abc import Awaitable
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.types import ToolAnnotations

from .adapters.base import AdapterError
from .adapters.http import HttpAuronAdapter
from .adapters.mock import MockAuronAdapter
from .audit import AuditLogger
from .auth import IdentityProvider, IdentityUnavailableError
from .config import settings
from .context import new_context, new_trace_id
from .errors import ConfirmationError, WriteDisabledError, normalize_error
from .logging import logger
from .policy import AuthorizationError, PolicyEngine
from .ratelimit import RateLimiter, RateLimitError
from .tools import AuronTools

_KNOWN_EXCEPTIONS = (
    AuthorizationError,
    RateLimitError,
    IdentityUnavailableError,
    AdapterError,
    WriteDisabledError,
    ConfirmationError,
)


def build_adapter():
    if settings.adapter == "http":
        from .manage import load_session_cookies

        return HttpAuronAdapter(
            settings.api_base_url,
            timeout=settings.http_timeout_seconds,
            bearer_token=settings.api_token,
            cookies=load_session_cookies(),
        )
    return MockAuronAdapter()


adapter = build_adapter()
identity_provider = IdentityProvider(
    adapter,
    cache_ttl=settings.identity_cache_ttl,
    allow_superuser_without_tenant=settings.allow_superuser_without_tenant,
)
policy = PolicyEngine(allow_superuser_without_tenant=settings.allow_superuser_without_tenant)
audit = AuditLogger(settings.audit_log_path or None)
rate_limiter = RateLimiter(
    max_calls=settings.rate_limit_max_calls,
    window_seconds=settings.rate_limit_window_seconds,
)
tools = AuronTools(adapter, policy, audit, rate_limiter)

mcp = MCPServer(settings.name)


async def _context(trace_id: str | None = None):
    """Build the per-call RequestContext from the authenticated AURON identity.

    The trace_id is the real MCP request id (see `_with_context`); a fresh id
    is only generated as a fallback when no incoming request is available
    (e.g. direct internal invocations).
    """
    identity = await identity_provider.resolve()
    return new_context(identity, trace_id=trace_id or new_trace_id())


async def _safe(call: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    """Error boundary: convert internal failures into clean model-safe payloads.

    Nothing internal — exception messages, tokens, upstream bodies, stack
    traces — ever reaches the model. Unknown errors are logged server-side
    (stderr only, never stdout, which is the stdio transport) and reported
    as a generic "internal" error.
    """
    try:
        return await call
    except _KNOWN_EXCEPTIONS:
        return normalize_error(sys.exc_info()[1])
    except Exception:  # noqa: BLE001 - the boundary catches everything
        exc = sys.exc_info()[1]
        traceback.print_exc(file=sys.stderr)
        logger.error(
            "unhandled.error",
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        return normalize_error(exc)


async def _with_context(fn, mcp_ctx: Context | None = None):
    """Resolve the authenticated context and run a tool with it.

    The trace_id propagates from the incoming MCP request id (`mcp_ctx`),
    not a per-call UUID. Kept inside the error boundary so a missing/expired
    session surfaces as a clean `{"error": "unauthorized", ...}` payload
    instead of a raised IdentityUnavailableError escaping to the MCP transport.
    """
    trace_id = None
    if mcp_ctx is not None:
        try:
            trace_id = mcp_ctx.request_id
        except ValueError:
            # Context without a live wire request (e.g. in-process
            # `mcp.call_tool`): fall back to a fresh trace id.
            trace_id = None
    ctx = await _context(trace_id)
    return await fn(ctx)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_sales_summary(ctx: Context | None = None) -> dict:
    """Read today's sales summary for the authenticated AURON tenant."""
    return await _safe(_with_context(tools.sales_summary, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_appointments_today(ctx: Context | None = None) -> dict:
    """Read today's appointments for the authenticated AURON tenant."""
    return await _safe(_with_context(tools.appointments_today, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_clients_search(query: str, limit: int = 20, ctx: Context | None = None) -> dict:
    """Search clients inside the authenticated AURON tenant (max 100 results)."""
    return await _safe(_with_context(tools.clients_search_factory(query, limit), ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_inventory_alerts(ctx: Context | None = None) -> dict:
    """Read low-stock inventory alerts for the authenticated AURON tenant."""
    return await _safe(_with_context(tools.inventory_alerts, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_employee_performance(
    employee_id: str | None = None, ctx: Context | None = None
) -> dict:
    """Read employee performance for the authenticated AURON tenant."""
    return await _safe(_with_context(tools.employee_performance_factory(employee_id), ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_system_health(ctx: Context | None = None) -> dict:
    """Read AURON API and MCP health."""
    return await _safe(_with_context(tools.system_health, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_services_list(ctx: Context | None = None) -> dict:
    """List the services offered by the authenticated AURON tenant."""
    return await _safe(_with_context(tools.services_list, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_employees_list(ctx: Context | None = None) -> dict:
    """List employees of the authenticated AURON tenant."""
    return await _safe(_with_context(tools.employees_list, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_branches_list(ctx: Context | None = None) -> dict:
    """List branches of the authenticated AURON tenant."""
    return await _safe(_with_context(tools.branches_list, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_products_list(ctx: Context | None = None) -> dict:
    """List products in the authenticated AURON tenant's inventory."""
    return await _safe(_with_context(tools.products_list, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_dashboard_stats(ctx: Context | None = None) -> dict:
    """Read dashboard statistics for the authenticated AURON tenant."""
    return await _safe(_with_context(tools.dashboard_stats, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_daily_review(ctx: Context | None = None) -> dict:
    """Compound daily business review for the authenticated AURON tenant.

    One call covering sales, today's appointments, low-stock inventory and
    employee performance. Requires every underlying read permission.
    """
    return await _safe(_with_context(tools.daily_review, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_inventory_review(ctx: Context | None = None) -> dict:
    """Inventory review for the authenticated AURON tenant.

    Full product list plus low-stock alerts in one call.
    """
    return await _safe(_with_context(tools.inventory_review, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_revenue_analysis(ctx: Context | None = None) -> dict:
    """Revenue analysis for the authenticated AURON tenant.

    Dashboard stats plus today's sales summary in one call.
    """
    return await _safe(_with_context(tools.revenue_analysis, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_employee_performance_review(ctx: Context | None = None) -> dict:
    """Employee review for the authenticated AURON tenant.

    Performance report plus the employee roster in one call.
    """
    return await _safe(_with_context(tools.employee_performance_review, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_preview_create_appointment(payload: dict, ctx: Context | None = None) -> dict:
    """Phase 1 of appointment creation: describe the intended effect and obtain
    a single-use confirmation_token to execute `auron_create_appointment`.
    No data is written by this call. Requires an idempotency_key on phase 2.
    """
    return await _safe(_with_context(lambda c: tools.preview_create_appointment(c, payload), ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_create_appointment(
    payload: dict,
    idempotency_key: str,
    confirmation_token: str,
    ctx: Context | None = None,
) -> dict:
    """Create an appointment (phase 2). Requires the confirmation_token from
    `auron_preview_create_appointment` and a client-generated idempotency_key.
    Replaying the same key returns the original result without side effects.
    """
    return await _safe(
        _with_context(
            lambda c: tools.create_appointment(
                c, payload, idempotency_key=idempotency_key, confirmation_token=confirmation_token
            ),
            ctx,
        )
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_preview_cancel_appointment(appointment_id: str, ctx: Context | None = None) -> dict:
    """Phase 1 of cancellation: preview the effect and obtain a single-use
    confirmation_token for `auron_cancel_appointment`. No data is changed.
    """
    return await _safe(
        _with_context(lambda c: tools.preview_cancel_appointment(c, appointment_id), ctx)
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_cancel_appointment(
    appointment_id: str,
    idempotency_key: str,
    confirmation_token: str,
    ctx: Context | None = None,
) -> dict:
    """Cancel an appointment (phase 2). Requires the confirmation_token from
    `auron_preview_cancel_appointment` plus an idempotency_key. The record is
    kept and its status changes to cancelled.
    """
    return await _safe(
        _with_context(
            lambda c: tools.cancel_appointment(
                c,
                appointment_id,
                idempotency_key=idempotency_key,
                confirmation_token=confirmation_token,
            ),
            ctx,
        )
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_update_appointment(
    appointment_id: str,
    changes: dict,
    idempotency_key: str,
    ctx: Context | None = None,
) -> dict:
    """Update low-impact appointment fields (service, branch, description).
    No confirmation required. Provide an idempotency_key for safe retries.
    """
    return await _safe(
        _with_context(
            lambda c: tools.update_appointment(
                c, appointment_id, changes, idempotency_key=idempotency_key
            ),
            ctx,
        )
    )


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_reschedule_appointment(
    appointment_id: str,
    new_datetime: str,
    idempotency_key: str,
    ctx: Context | None = None,
) -> dict:
    """Move an appointment to a new datetime via the backend's reschedule
    endpoint. No confirmation required. Use an idempotency_key for retries.
    """
    return await _safe(
        _with_context(
            lambda c: tools.reschedule_appointment(
                c, appointment_id, new_datetime, idempotency_key=idempotency_key
            ),
            ctx,
        )
    )


# --- fiscal tools (M3: ECF / NCF) ----------------------------------------


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_ncf_sequences(ctx: Context | None = None) -> dict:
    """List NCF sequences for the authenticated AURON tenant: type, range,
    remaining count, exhaustion and expiry status.
    """
    return await _safe(_with_context(tools.ncf_sequences, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_fiscal_summary(ctx: Context | None = None) -> dict:
    """Today's fiscal summary for the authenticated AURON tenant: sales count
    by NCF type, ITBIS and Propina totals, voided/refunded counts.
    """
    return await _safe(_with_context(tools.fiscal_summary, ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_sale_fiscal_detail(sale_id: str, ctx: Context | None = None) -> dict:
    """Fiscal detail of a single sale: NCF number, RNC, tax breakdown (ITBIS,
    Propina), line items and payment method.
    """
    return await _safe(_with_context(lambda c: tools.sale_fiscal_detail(c, sale_id), ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True))
async def auron_preview_void_sale(sale_id: str, ctx: Context | None = None) -> dict:
    """Phase 1 of sale voiding: describe the effect and obtain a single-use
    confirmation_token for `auron_void_sale`. No data is changed.
    """
    return await _safe(_with_context(lambda c: tools.preview_void_sale(c, sale_id), ctx))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True))
async def auron_void_sale(
    sale_id: str,
    idempotency_key: str,
    confirmation_token: str,
    ctx: Context | None = None,
) -> dict:
    """Void a sale (phase 2). Requires the confirmation_token from
    `auron_preview_void_sale` and an idempotency_key. The NCF remains
    consumed per DGII rules — it is not recycled.
    """
    return await _safe(
        _with_context(
            lambda c: tools.void_sale(
                c,
                sale_id,
                idempotency_key=idempotency_key,
                confirmation_token=confirmation_token,
            ),
            ctx,
        )
    )


@mcp.resource("auron://context/current")
async def current_context() -> str:
    """Describe the current authenticated AURON scope."""
    ctx = await _context()
    identity = ctx.identity
    return (
        f"tenant_id={identity.tenant_id}\n"
        f"tenant_subdomain={identity.tenant_subdomain}\n"
        f"user_id={identity.user_id}\n"
        f"role={identity.role}\n"
        f"read_only={settings.read_only}"
    )


@mcp.prompt()
def daily_business_review() -> str:
    """Reusable prompt for a daily AURON business review."""
    return (
        "Perform a concise daily business review using the available AURON tools. "
        "Prefer the `auron_daily_review` compound tool for one consolidated digest "
        "of sales, appointments, inventory alerts and employee performance. "
        "Separate observed facts from interpretation and flag anything requiring action."
    )


def main() -> None:
    from .secrets import ConfigurationError, check_config

    try:
        check_config(settings)
    except ConfigurationError as exc:
        logger.error("config.invalid", error_message=str(exc))
        raise
    logger.info(
        "server.start",
        transport=settings.transport,
        adapter=settings.adapter,
        read_only=settings.read_only,
    )
    if settings.transport == "streamable-http":
        from .http_app import main as http_main

        http_main()
        return
    from .otel import setup_otel

    setup_otel()
    mcp.run(transport=settings.transport)


if __name__ == "__main__":
    main()
