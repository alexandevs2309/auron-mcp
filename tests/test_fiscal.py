"""Fiscal / ECF tests (M3): NCF sequences, fiscal summary, sale detail, void.

Runs against the real server wiring with the mock adapter.
"""

import os

os.environ["AURON_ADAPTER"] = "mock"

import pytest

from auron_mcp import server
from auron_mcp.auth import AuronIdentity
from auron_mcp.config import settings
from auron_mcp.policy.roles import permissions_for_role


def _identity(role: str = "CLIENT_ADMIN") -> AuronIdentity:
    return AuronIdentity(
        user_id="u-fiscal",
        email="fiscal@auron.test",
        tenant_id="t1",
        role=role,
        permissions=permissions_for_role(role),
    )


async def _resolve(identity):
    return identity


@pytest.fixture
def auth_admin(monkeypatch):
    monkeypatch.setattr(server.identity_provider, "resolve", lambda: _resolve(_identity()))


@pytest.fixture(autouse=True)
def writable():
    original = settings.read_only
    object.__setattr__(settings, "read_only", False)
    yield
    object.__setattr__(settings, "read_only", original)


# --- NCF sequences ---


@pytest.mark.asyncio
async def test_ncf_sequences_returns_mock_data(auth_admin):
    result = await server.auron_ncf_sequences()
    assert result["status"] == "mock"
    seqs = result["sequences"]
    assert len(seqs) == 1
    assert seqs[0]["type"] == "02"
    assert seqs[0]["remaining"] == 50000
    assert seqs[0]["exhausted"] is False
    assert seqs[0]["expired"] is False


# --- Fiscal summary ---


@pytest.mark.asyncio
async def test_fiscal_summary_returns_mock_data(auth_admin):
    result = await server.auron_fiscal_summary()
    assert result["status"] == "mock"
    assert result["date"] == "2026-08-17"
    assert result["total_sales"] == 0
    assert result["total_itbis"] == "0.00"
    assert result["total_propina"] == "0.00"


# --- Sale fiscal detail ---


@pytest.mark.asyncio
async def test_sale_fiscal_detail_returns_mock_data(auth_admin):
    result = await server.auron_sale_fiscal_detail(sale_id="sale-42")
    assert result["status"] == "mock"
    assert result["sale_id"] == "sale-42"
    assert result["ncf"] == "B0200000001"
    assert result["itbis"] == "0.00"


# --- Void sale (two-phase) ---


@pytest.mark.asyncio
async def test_void_sale_two_phase_flow(auth_admin):
    preview = await server.auron_preview_void_sale(sale_id="sale-99")
    assert preview["operation"] == "void_sale"
    assert preview["sale_id"] == "sale-99"
    token = preview["confirmation_token"]
    result = await server.auron_void_sale(
        sale_id="sale-99",
        idempotency_key="key-void-1",
        confirmation_token=token,
    )
    assert result["status"] == "void"
    assert result["sale_id"] == "sale-99"


@pytest.mark.asyncio
async def test_void_sale_confirmation_single_use(auth_admin):
    preview = await server.auron_preview_void_sale(sale_id="sale-99")
    token = preview["confirmation_token"]
    await server.auron_void_sale(
        sale_id="sale-99",
        idempotency_key="key-void-su",
        confirmation_token=token,
    )
    replay = await server.auron_void_sale(
        sale_id="sale-99",
        idempotency_key="key-void-su-2",
        confirmation_token=token,
    )
    assert replay["error"] == "confirmation_unknown"


@pytest.mark.asyncio
async def test_void_sale_read_only_blocks(auth_admin):
    original = settings.read_only
    try:
        object.__setattr__(settings, "read_only", True)
        result = await server.auron_void_sale(
            sale_id="sale-99",
            idempotency_key="key-void-ro",
            confirmation_token="unused",
        )
        assert result["error"] == "read_only"
    finally:
        object.__setattr__(settings, "read_only", original)


@pytest.mark.asyncio
async def test_void_sale_denied_for_read_only_role(monkeypatch):
    identity = _identity(role="ESTILISTA")
    monkeypatch.setattr(server.identity_provider, "resolve", lambda: _resolve(identity))
    preview = await server.auron_preview_void_sale(sale_id="sale-99")
    assert preview["error"] == "forbidden"


@pytest.mark.asyncio
async def test_void_sale_idempotency_replay(auth_admin):
    preview = await server.auron_preview_void_sale(sale_id="sale-99")
    token = preview["confirmation_token"]
    first = await server.auron_void_sale(
        sale_id="sale-99",
        idempotency_key="key-void-idem",
        confirmation_token=token,
    )
    preview2 = await server.auron_preview_void_sale(sale_id="sale-99")
    second = await server.auron_void_sale(
        sale_id="sale-99",
        idempotency_key="key-void-idem",
        confirmation_token=preview2["confirmation_token"],
    )
    assert second == first
