import json

import httpx2 as httpx
import pytest

from auron_mcp.adapters.base import AdapterError
from auron_mcp.adapters.http import HttpAuronAdapter


def make_adapter(handler, **kwargs):
    adapter = HttpAuronAdapter("http://localhost:8000", **kwargs)
    adapter.client = httpx.AsyncClient(
        base_url="http://localhost:8000",
        headers=adapter.client.headers,
        transport=httpx.MockTransport(handler),
    )
    return adapter


@pytest.mark.asyncio
async def test_success_response_is_returned():
    def handler(request):
        return httpx.Response(200, json={"sales_count": 3, "total": "100.00"})

    adapter = make_adapter(handler)
    result = await adapter.sales_summary()
    assert result["sales_count"] == 3


@pytest.mark.asyncio
async def test_upstream_error_body_is_never_leaked():
    def handler(request):
        return httpx.Response(
            403, json={"detail": "internal secret debug info you must not see"}
        )

    adapter = make_adapter(handler)
    with pytest.raises(AdapterError) as exc_info:
        await adapter.sales_summary()

    assert "internal secret debug info" not in str(exc_info.value)
    assert exc_info.value.category == "unauthorized"


@pytest.mark.asyncio
async def test_network_failure_is_normalized():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    adapter = make_adapter(handler)
    with pytest.raises(AdapterError) as exc_info:
        await adapter.sales_summary()

    assert exc_info.value.category == "upstream_unavailable"


@pytest.mark.asyncio
async def test_tenant_is_never_sent_as_param_or_header():
    seen = {}

    def handler(request):
        seen["param"] = request.url.params.get("tenant_id")
        seen["header"] = request.headers.get("x-auron-tenant")
        seen["bearer"] = request.headers.get("authorization")
        return httpx.Response(200, json={"alerts": []})

    adapter = make_adapter(handler)
    await adapter.inventory_alerts()
    assert seen["param"] is None
    assert seen["header"] is None
    assert seen["bearer"] is None


@pytest.mark.asyncio
async def test_bearer_token_is_sent_when_configured():
    seen = {}

    def handler(request):
        seen["bearer"] = request.headers.get("authorization")
        return httpx.Response(200, json={"status": "ok"})

    adapter = make_adapter(handler, bearer_token="secret-token")
    await adapter.system_health()
    assert seen["bearer"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_login_stores_auth_cookies():
    def handler(request):
        return httpx.Response(
            200,
            json={"user": {"email": "a@b.test", "role": "CAJERA"}},
            headers=[
                ("set-cookie", "access_token=abc123; Path=/; HttpOnly"),
                ("set-cookie", "refresh_token=rt456; Path=/; HttpOnly"),
            ],
        )

    adapter = make_adapter(handler)
    await adapter.auth_login("a@b.test", "pw", subdomain=None)

    names = {c["name"] for c in adapter.export_cookies()}
    assert {"access_token", "refresh_token"} <= names
    assert adapter.can_refresh()


@pytest.mark.asyncio
async def test_login_failure_is_normalized():
    def handler(request):
        return httpx.Response(400, json={"email": ["Invalid credentials"]})

    adapter = make_adapter(handler)
    with pytest.raises(AdapterError) as exc_info:
        await adapter.auth_login("a@b.test", "wrong", None)
    assert exc_info.value.category == "login_failed"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("services_list", "/api/services/services/"),
        ("employees_list", "/api/employees/employees/"),
        ("branches_list", "/api/settings/branches/"),
        ("products_list", "/api/inventory/products/"),
        ("dashboard_stats", "/api/pos/dashboard/stats/"),
    ],
)
@pytest.mark.asyncio
async def test_catalog_read_methods_hit_expected_paths(method, path):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"count": 0, "results": []})

    adapter = make_adapter(handler)
    await getattr(adapter, method)()
    assert seen["path"] == path


@pytest.mark.asyncio
async def test_reschedule_hits_canonical_backend_path():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 42, "status": "scheduled"})

    adapter = make_adapter(handler)
    await adapter.reschedule_appointment("42", "2026-11-20T10:00:00")

    assert seen["path"] == "/api/appointments/42/reschedule/"
    assert seen["body"] == {"new_datetime": "2026-11-20T10:00:00"}


@pytest.mark.asyncio
async def test_login_sends_tenant_subdomain_not_subdomain():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"user": {"email": "a@b.test"}})

    adapter = make_adapter(handler)
    await adapter.auth_login("a@b.test", "pw", "mi-negocio")

    assert seen["body"]["tenant_subdomain"] == "mi-negocio"
    assert "subdomain" not in seen["body"]


@pytest.mark.asyncio
async def test_fiscal_summary_uses_reports_endpoint_and_normalizes_payload():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "summary": {
                    "taxable_sales_count": 2,
                    "taxable_sales_total": 100.50,
                    "exempt_sales_count": 1,
                    "exempt_sales_total": 10.25,
                    "itbis_amount": 13.50,
                    "tax_rate": 0.18,
                    "voided_count": 3,
                    "refunded_count": 4,
                },
                "ncf_by_type": [
                    {"ncf_type": "01", "count": 2, "total": 100.50},
                ],
            },
        )

    adapter = make_adapter(handler)
    result = await adapter.fiscal_summary()

    assert seen["path"] == "/api/reports/fiscal/"
    assert result["total_sales"] == 3
    assert result["total_revenue"] == "110.75"
    assert result["total_itbis"] == "13.50"
    assert result["sales_by_ncf_type"] == {"01": 2}
    assert result["voided_sales"] == 3
    assert result["refunded_sales"] == 4
    # The backend exposes no propina: the adapter must not fabricate one.
    assert "total_propina" not in result
