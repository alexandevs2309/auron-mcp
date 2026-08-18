import pytest

from auron_mcp.adapters.base import AdapterError
from auron_mcp.adapters.mock import MockAuronAdapter
from auron_mcp.audit import AuditLogger
from auron_mcp.auth import AuronIdentity
from auron_mcp.context import RequestContext
from auron_mcp.errors import normalize_error
from auron_mcp.policy import AuthorizationError, PolicyEngine
from auron_mcp.ratelimit import RateLimiter, RateLimitError
from auron_mcp.tools import AuronTools


def make_context(permissions=frozenset(), tenant_id="tenant-a", user_id="u1", role="CLIENT_ADMIN"):
    return RequestContext(
        identity=AuronIdentity(
            user_id=user_id,
            email=f"{user_id}@auron.test",
            tenant_id=tenant_id,
            role=role,
            permissions=permissions,
        )
    )


FULL_PERMS = frozenset(
    {
        "pos_api.view_sale",
        "appointments_api.view_appointment",
        "clients_api.view_client",
        "inventory_api.view_product",
        "reports_api.view_employee_reports",
        "reports_api.view_kpi_dashboard",
        "services_api.view_service",
        "employees_api.view_employee",
        "settings_api.view_branch",
        "system:read",
    }
)


@pytest.fixture
def context():
    return make_context(FULL_PERMS)


def make_tools(rate_limiter=None, log_path=None):
    return AuronTools(
        MockAuronAdapter(),
        PolicyEngine(),
        AuditLogger(log_path),
        rate_limiter,
    )


@pytest.mark.asyncio
async def test_sales_summary_runs_with_full_permissions(context):
    result = await make_tools().sales_summary(context)
    assert result["status"] == "mock"


@pytest.mark.asyncio
async def test_client_limit_is_capped(context):
    result = await make_tools().clients_search(context, "ana", 9999)
    assert result["limit"] == 100


@pytest.mark.asyncio
async def test_missing_permission_raises_and_is_audited(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    ctx = make_context(frozenset())  # no permissions at all
    tools = make_tools(log_path=str(log_path))

    with pytest.raises(AuthorizationError):
        await tools.sales_summary(ctx)

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"outcome": "denied"' in lines[0]


@pytest.mark.asyncio
async def test_rate_limit_blocks_excessive_calls(context):
    tools = make_tools(rate_limiter=RateLimiter(max_calls=1, window_seconds=60))

    await tools.sales_summary(context)
    with pytest.raises(RateLimitError):
        await tools.sales_summary(context)


@pytest.mark.asyncio
async def test_rate_limit_is_per_tool_not_global(context):
    tools = make_tools(rate_limiter=RateLimiter(max_calls=1, window_seconds=60))

    await tools.sales_summary(context)
    # Different tool, own bucket — should not be blocked by sales_summary's call.
    await tools.appointments_today(context)


@pytest.mark.asyncio
async def test_daily_review_requires_every_read_permission(context):
    result = await make_tools().daily_review(context)
    assert result["sales"]["status"] == "mock"
    assert result["appointments_today"]["status"] == "mock"
    assert result["inventory_alerts"]["status"] == "mock"
    assert result["employee_performance"]["status"] == "mock"


@pytest.mark.asyncio
async def test_daily_review_denied_if_any_permission_missing(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    perms = frozenset(
        {"pos_api.view_sale", "appointments_api.view_appointment", "inventory_api.view_product"}
    )  # missing reports_api.view_employee_reports
    ctx = make_context(perms)
    tools = make_tools(log_path=str(log_path))

    with pytest.raises(AuthorizationError):
        await tools.daily_review(ctx)

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"outcome": "denied"' in lines[0]
    assert '"tool": "daily.review.read"' in lines[0]


@pytest.mark.asyncio
async def test_inventory_review_aggregates_products_and_alerts(context):
    result = await make_tools().inventory_review(context)
    assert result["products"]["status"] == "mock"
    assert result["low_stock_alerts"]["status"] == "mock"


@pytest.mark.asyncio
async def test_revenue_analysis_aggregates_dashboard_and_sales(context):
    result = await make_tools().revenue_analysis(context)
    assert result["dashboard"]["status"] == "mock"
    assert result["sales_today"]["status"] == "mock"


@pytest.mark.asyncio
async def test_employee_review_requires_performance_and_roster_perms(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    ctx = make_context(frozenset({"reports_api.view_employee_reports"}))  # missing employees_api.view_employee
    tools = make_tools(log_path=str(log_path))

    with pytest.raises(AuthorizationError):
        await tools.employee_performance_review(ctx)

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"outcome": "denied"' in lines[0]
    assert '"tool": "employees.review.read"' in lines[0]


def test_error_boundary_never_leaks_internal_messages():
    assert normalize_error(AuthorizationError("denied")) == {
        "error": "forbidden",
        "detail": "denied",
    }
    assert normalize_error(RateLimitError("slow down"))["error"] == "rate_limited"
    assert normalize_error(AdapterError("upstream_unavailable"))["error"] == "upstream_unavailable"
    internal = normalize_error(RuntimeError("database password=supersecret"))
    assert internal == {"error": "internal", "detail": "Unexpected internal error"}
