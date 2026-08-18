"""End-to-end wiring test for server.py.

Exercises the decorated MCP functions through the real adapter/policy/audit/
rate-limiter wiring built in server.py, using the mock adapter (set via env
before import) so it never touches a network.
"""

import os

os.environ["AURON_ADAPTER"] = "mock"

import pytest

from auron_mcp import server
from auron_mcp.auth import AuronIdentity, IdentityUnavailableError
from auron_mcp.policy.roles import permissions_for_role


@pytest.mark.asyncio
async def test_sales_summary_tool_returns_mock_data():
    result = await server.auron_sales_summary()
    assert result["status"] == "mock"


@pytest.mark.asyncio
async def test_clients_search_tool_caps_limit():
    result = await server.auron_clients_search(query="ana", limit=9999)
    assert result["limit"] == 100


@pytest.mark.asyncio
async def test_requires_an_authenticated_session(monkeypatch):
    async def no_session():
        raise IdentityUnavailableError("Not authenticated; run `auron-manage login`")

    monkeypatch.setattr(server.identity_provider, "resolve", no_session)
    result = await server.auron_sales_summary()
    assert result["error"] == "unauthorized"
    assert "auron-manage login" in result["detail"]


@pytest.mark.asyncio
async def test_unknown_role_fails_closed_with_forbidden_payload(monkeypatch):
    identity = AuronIdentity(
        user_id="u1",
        email="u1@auron.test",
        tenant_id="t1",
        role="NOT_A_REAL_ROLE",
        permissions=permissions_for_role("NOT_A_REAL_ROLE"),
    )
    monkeypatch.setattr(server.identity_provider, "resolve", lambda: _resolve(identity))
    result = await server.auron_sales_summary()
    assert result["error"] == "forbidden"
    assert result["detail"] == "Permission denied for 'pos_api.view_sale'"


async def _resolve(identity):
    return identity


class _FakeMCPContext:
    """Minimal stand-in for the SDK Context injected into tool functions."""

    request_id = "mcp-req-abc123"
async def _capture_context(ctx):
    return ctx


@pytest.mark.asyncio
async def test_trace_id_propagates_from_incoming_mcp_request(monkeypatch):
    identity = AuronIdentity(
        user_id="u1",
        email="u1@auron.test",
        tenant_id="t1",
        role="CLIENT_ADMIN",
        permissions=permissions_for_role("CLIENT_ADMIN"),
    )
    monkeypatch.setattr(server.identity_provider, "resolve", lambda: _resolve(identity))

    captured = await server._with_context(_capture_context, _FakeMCPContext())
    assert captured.trace_id == "mcp-req-abc123"


@pytest.mark.asyncio
async def test_trace_id_falls_back_when_no_incoming_request(monkeypatch):
    identity = AuronIdentity(
        user_id="u1",
        email="u1@auron.test",
        tenant_id="t1",
        role="CLIENT_ADMIN",
        permissions=permissions_for_role("CLIENT_ADMIN"),
    )
    monkeypatch.setattr(server.identity_provider, "resolve", lambda: _resolve(identity))

    captured = await server._with_context(_capture_context)
    assert captured.trace_id.startswith("trace-")


@pytest.mark.asyncio
async def test_current_context_resource_reports_scope():
    text = await server.current_context()
    assert "tenant_id=t-mock-1" in text
    assert "role=CLIENT_ADMIN" in text


def test_daily_business_review_prompt_is_nonempty():
    assert "sales" in server.daily_business_review().lower()


@pytest.mark.asyncio
async def test_tools_are_registered_on_the_mcp_server():
    names = {t.name for t in await server.mcp.list_tools()}
    assert {
        "auron_sales_summary",
        "auron_appointments_today",
        "auron_clients_search",
        "auron_inventory_alerts",
        "auron_employee_performance",
        "auron_system_health",
        "auron_services_list",
        "auron_employees_list",
        "auron_branches_list",
        "auron_products_list",
        "auron_dashboard_stats",
        "auron_daily_review",
        "auron_inventory_review",
        "auron_revenue_analysis",
        "auron_employee_performance_review",
    } <= names
