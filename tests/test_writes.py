"""Controlled-write tests (M4): two-phase confirmation, idempotency, the
read-only fail-closed guard, permission enforcement and compensation.

Runs against the real `server` wiring with the mock adapter (set via env
before import) so nothing touches a network.
"""

import os

os.environ["AURON_ADAPTER"] = "mock"

import pytest

from auron_mcp import server
from auron_mcp.adapters.mock import MockAuronAdapter
from auron_mcp.audit import AuditLogger
from auron_mcp.auth import AuronIdentity
from auron_mcp.config import settings
from auron_mcp.context import new_context, new_trace_id
from auron_mcp.policy.roles import permissions_for_role
from auron_mcp.tools import AuronTools
from auron_mcp.writes import ConfirmationTokenStore

PAYLOAD = {
    "client": 1,
    "stylist": 2,
    "date_time": "2026-08-20T10:00:00Z",
    "service": 3,
    "branch": 4,
}


def _identity(role: str = "CLIENT_ADMIN") -> AuronIdentity:
    return AuronIdentity(
        user_id="u-writer",
        email="writer@auron.test",
        tenant_id="t1",
        role=role,
        permissions=permissions_for_role(role),
    )


def _ctx(role: str = "CLIENT_ADMIN"):
    return new_context(_identity(role), trace_id=new_trace_id())


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


async def _create(idempotency_key: str) -> dict:
    preview = await server.auron_preview_create_appointment(payload=PAYLOAD)
    return await server.auron_create_appointment(
        payload=PAYLOAD,
        idempotency_key=idempotency_key,
        confirmation_token=preview["confirmation_token"],
    )


@pytest.mark.asyncio
async def test_create_is_two_phase(auth_admin):
    preview = await server.auron_preview_create_appointment(payload=PAYLOAD)
    assert preview["operation"] == "create_appointment"
    assert preview["payload"] == PAYLOAD
    assert preview["effects"]
    token = preview["confirmation_token"]
    assert token and preview["ttl_seconds"] == settings.confirmation_ttl_seconds

    created = await server.auron_create_appointment(
        payload=PAYLOAD, idempotency_key="key-two-phase", confirmation_token=token
    )
    assert created["status"] == "scheduled"
    assert created["client"] == PAYLOAD["client"]


@pytest.mark.asyncio
async def test_confirmation_token_is_single_use(auth_admin):
    preview = await server.auron_preview_create_appointment(payload=PAYLOAD)
    token = preview["confirmation_token"]
    await server.auron_create_appointment(
        payload=PAYLOAD, idempotency_key="key-single-use", confirmation_token=token
    )
    replay = await server.auron_create_appointment(
        payload=PAYLOAD,
        idempotency_key="key-single-use-2",
        confirmation_token=token,
    )
    assert replay["error"] == "confirmation_unknown"


@pytest.mark.asyncio
async def test_confirmation_mismatch_on_changed_payload(auth_admin):
    preview = await server.auron_preview_create_appointment(payload=PAYLOAD)
    token = preview["confirmation_token"]
    tampered = {**PAYLOAD, "client": 99}
    result = await server.auron_create_appointment(
        payload=tampered,
        idempotency_key="key-mismatch",
        confirmation_token=token,
    )
    assert result["error"] == "confirmation_mismatch"


@pytest.mark.asyncio
async def test_expired_confirmation_token_is_rejected():
    store = ConfirmationTokenStore(ttl_seconds=0)
    token = store.issue("create_appointment", {"a": 1})
    assert store.consume(token, "create_appointment", {"a": 1}) == "expired"


@pytest.mark.asyncio
async def test_write_without_token_is_rejected(auth_admin):
    result = await server.auron_create_appointment(
        payload=PAYLOAD,
        idempotency_key="key-no-token",
        confirmation_token="bogus",
    )
    assert result["error"] == "confirmation_unknown"


@pytest.mark.asyncio
async def test_idempotency_replay_returns_original_result(auth_admin):
    first = await _create("key-replay")
    preview = await server.auron_preview_create_appointment(payload=PAYLOAD)
    second = await server.auron_create_appointment(
        payload=PAYLOAD,
        idempotency_key="key-replay",
        confirmation_token=preview["confirmation_token"],
    )
    assert second == first
    assert server.tools.adapter._appointments["1"]["date_time"] == PAYLOAD["date_time"]


@pytest.mark.asyncio
async def test_failed_write_does_not_poison_idempotency(auth_admin):
    missing = await server.auron_update_appointment(
        appointment_id="99999",
        changes={"service": 1},
        idempotency_key="key-poison",
    )
    assert missing["error"] == "not_found"
    created = await _create("key-poison")
    assert created["status"] == "scheduled"


@pytest.mark.asyncio
async def test_read_only_blocks_writes_and_previews(auth_admin):
    original = settings.read_only
    count_before = len(server.tools.adapter._appointments)
    try:
        object.__setattr__(settings, "read_only", True)
        write = await server.auron_create_appointment(
            payload=PAYLOAD,
            idempotency_key="key-ro",
            confirmation_token="unused",
        )
        assert write["error"] == "read_only"
        preview = await server.auron_preview_create_appointment(payload=PAYLOAD)
        assert preview["error"] == "read_only"
    finally:
        object.__setattr__(settings, "read_only", original)
    assert len(server.tools.adapter._appointments) == count_before


@pytest.mark.asyncio
async def test_write_denied_for_read_only_role(monkeypatch):
    identity = _identity(role="ESTILISTA")
    monkeypatch.setattr(server.identity_provider, "resolve", lambda: _resolve(identity))
    preview = await server.auron_preview_create_appointment(payload=PAYLOAD)
    assert preview["error"] == "forbidden"
    cancel = await server.auron_cancel_appointment(
        appointment_id="999", idempotency_key="k", confirmation_token="t"
    )
    assert cancel["error"] == "forbidden"


@pytest.mark.asyncio
async def test_cancel_flow_requires_confirmation(auth_admin):
    created = await _create("key-cancel-1")
    cancelled = await server.auron_cancel_appointment(
        appointment_id=created["id"],
        idempotency_key="key-cancel-2",
        confirmation_token="bogus",
    )
    assert cancelled["error"] == "confirmation_unknown"

    preview = await server.auron_preview_cancel_appointment(appointment_id=created["id"])
    assert preview["operation"] == "cancel_appointment"
    cancelled = await server.auron_cancel_appointment(
        appointment_id=created["id"],
        idempotency_key="key-cancel-3",
        confirmation_token=preview["confirmation_token"],
    )
    assert cancelled["status"] == "cancelled"


@pytest.mark.asyncio
async def test_update_and_reschedule_need_no_confirmation(auth_admin):
    created = await _create("key-ur-1")
    appointment_id = created["id"]
    updated = await server.auron_update_appointment(
        appointment_id=appointment_id,
        changes={"service": 7, "branch": 9},
        idempotency_key="key-ur-2",
    )
    assert updated["service"] == 7
    assert updated["branch"] == 9
    rescheduled = await server.auron_reschedule_appointment(
        appointment_id=appointment_id,
        new_datetime="2026-08-21T12:00:00Z",
        idempotency_key="key-ur-3",
    )
    assert rescheduled["date_time"] == "2026-08-21T12:00:00Z"
    assert rescheduled["status"] == "scheduled"


@pytest.mark.asyncio
async def test_compensation_runs_when_write_fails():
    adapter = MockAuronAdapter()
    tools = AuronTools(adapter, server.policy, AuditLogger(None))
    compensated = []

    async def failing():
        raise RuntimeError("boom")

    async def compensate():
        compensated.append(True)

    with pytest.raises(RuntimeError):
        await tools._write(
            _ctx(),
            "appointments.create",
            "appointments_api.add_appointment",
            failing,
            operation="create_appointment",
            payload={"x": 1},
            idempotency_key="key-comp",
            compensation=compensate,
        )
    assert compensated == [True]


@pytest.mark.asyncio
async def test_cashier_role_can_write(auth_admin, monkeypatch):
    identity = _identity(role="CAJERA")
    monkeypatch.setattr(server.identity_provider, "resolve", lambda: _resolve(identity))
    created = await _create("key-cashier-1")
    assert created["status"] == "scheduled"
    rescheduled = await server.auron_reschedule_appointment(
        appointment_id=created["id"],
        new_datetime="2026-08-22T09:00:00Z",
        idempotency_key="key-cashier-2",
    )
    assert rescheduled["date_time"] == "2026-08-22T09:00:00Z"
    cancelled = await server.auron_cancel_appointment(
        appointment_id=created["id"],
        idempotency_key="key-cashier-3",
        confirmation_token=(
            await server.auron_preview_cancel_appointment(appointment_id=created["id"])
        )["confirmation_token"],
    )
    assert cancelled["status"] == "cancelled"
