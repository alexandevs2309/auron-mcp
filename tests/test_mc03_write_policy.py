"""MC-03: `update` y `reschedule` son escrituras de dos fases, como create/cancel/void."""

import os

os.environ["AURON_ADAPTER"] = "mock"

import pytest

from auron_mcp import server
from auron_mcp.auth import AuronIdentity
from auron_mcp.config import settings
from auron_mcp.policy.roles import permissions_for_role


def _identity(role: str = "CLIENT_ADMIN") -> AuronIdentity:
    return AuronIdentity(
        user_id="u-writer",
        email="writer@auron.test",
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


async def _appointment() -> dict:
    return await server.tools.adapter.create_appointment(
        {"client": 1, "stylist": 2, "date_time": "2026-08-20T10:00:00Z"}
    )


@pytest.mark.asyncio
async def test_update_without_valid_token_is_rejected(auth_admin):
    appointment = await _appointment()
    result = await server.auron_update_appointment(
        appointment_id=appointment["id"],
        changes={"service": 7},
        idempotency_key="mc03-update-no-token",
        confirmation_token="bogus",
    )
    assert result["error"] == "confirmation_unknown"
    assert server.tools.adapter._appointments[appointment["id"]]["service"] is None


@pytest.mark.asyncio
async def test_update_preview_token_is_single_use_and_payload_bound(auth_admin):
    appointment = await _appointment()
    changes = {"service": 7, "branch": 9}
    preview = await server.auron_preview_update_appointment(
        appointment_id=appointment["id"], changes=changes
    )
    assert preview["operation"] == "update_appointment"
    assert preview["confirmation_token"]

    tampered = await server.auron_update_appointment(
        appointment_id=appointment["id"],
        changes={"service": 99},
        idempotency_key="mc03-update-tampered",
        confirmation_token=preview["confirmation_token"],
    )
    assert tampered["error"] == "confirmation_mismatch"

    # Un intento con payload manipulado quema el token (single-use), así que
    # hace falta un preview nuevo para completar la escritura legítima.
    retry_preview = await server.auron_preview_update_appointment(
        appointment_id=appointment["id"], changes=changes
    )
    updated = await server.auron_update_appointment(
        appointment_id=appointment["id"],
        changes=changes,
        idempotency_key="mc03-update-ok",
        confirmation_token=retry_preview["confirmation_token"],
    )
    assert updated["service"] == 7

    replay = await server.auron_update_appointment(
        appointment_id=appointment["id"],
        changes=changes,
        idempotency_key="mc03-update-replay",
        confirmation_token=preview["confirmation_token"],
    )
    assert replay["error"] == "confirmation_unknown"


@pytest.mark.asyncio
async def test_reschedule_token_is_single_use_and_payload_bound(auth_admin):
    appointment = await _appointment()
    preview = await server.auron_preview_reschedule_appointment(
        appointment_id=appointment["id"], new_datetime="2026-08-21T12:00:00Z"
    )
    assert preview["operation"] == "reschedule_appointment"

    tampered = await server.auron_reschedule_appointment(
        appointment_id=appointment["id"],
        new_datetime="2026-08-22T12:00:00Z",
        idempotency_key="mc03-reschedule-tampered",
        confirmation_token=preview["confirmation_token"],
    )
    assert tampered["error"] == "confirmation_mismatch"

    retry_preview = await server.auron_preview_reschedule_appointment(
        appointment_id=appointment["id"], new_datetime="2026-08-21T12:00:00Z"
    )
    moved = await server.auron_reschedule_appointment(
        appointment_id=appointment["id"],
        new_datetime="2026-08-21T12:00:00Z",
        idempotency_key="mc03-reschedule-ok",
        confirmation_token=retry_preview["confirmation_token"],
    )
    assert moved["date_time"] == "2026-08-21T12:00:00Z"

    replay = await server.auron_reschedule_appointment(
        appointment_id=appointment["id"],
        new_datetime="2026-08-21T12:00:00Z",
        idempotency_key="mc03-reschedule-replay",
        confirmation_token=preview["confirmation_token"],
    )
    assert replay["error"] == "confirmation_unknown"


@pytest.mark.asyncio
async def test_previews_are_blocked_in_read_only_mode(auth_admin):
    appointment = await _appointment()
    original = settings.read_only
    object.__setattr__(settings, "read_only", True)
    try:
        update = await server.auron_preview_update_appointment(
            appointment_id=appointment["id"], changes={"service": 7}
        )
        reschedule = await server.auron_preview_reschedule_appointment(
            appointment_id=appointment["id"], new_datetime="2026-08-21T12:00:00Z"
        )
    finally:
        object.__setattr__(settings, "read_only", original)
    assert update["error"] == "read_only"
    assert reschedule["error"] == "read_only"
