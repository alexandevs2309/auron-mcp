"""V-06 — Verificación contra Redis REAL (no fakes).

Dos instancias independientes de cada store simulan dos réplicas del gateway
que comparten el mismo Redis. Se omite si no Hay un Redis accesible en
``AURON_TEST_REDIS_URL`` (por defecto redis://127.0.0.1:56379/0).
"""

import os
import uuid

import pytest

REDIS_URL = os.environ.get('AURON_TEST_REDIS_URL', 'redis://127.0.0.1:56379/0')

pytestmark = pytest.mark.asyncio


def _live_client():
    from redis import asyncio as aioredis

    # Redis portable 5.0 no habla RESP3 (HELLO), que redis-py 8 usa por defecto.
    client = aioredis.from_url(
        REDIS_URL, socket_timeout=2, socket_connect_timeout=2, protocol=2
    )
    return client


@pytest.fixture
async def live_redis():
    client = _live_client()
    try:
        await client.ping()
    except Exception as exc:  # noqa: BLE001 - cualquier error de conexión ⇒ skip
        await client.aclose()
        pytest.skip(f'Redis real no disponible en {REDIS_URL}: {type(exc).__name__}')
    prefix = f'auron:test:{uuid.uuid4().hex}'
    try:
        yield client, prefix
    finally:
        keys = [k.decode() if isinstance(k, bytes) else k async for k in client.scan_iter(f'{prefix}*')]
        if keys:
            await client.delete(*keys)
        await client.aclose()


async def test_confirmation_token_single_use_across_replicas(live_redis):
    """Un token emitido por la réplica A no puede ser reutilizado en la B."""
    from auron_mcp.writes import RedisConfirmationTokenStore

    client, prefix = live_redis
    issuer = RedisConfirmationTokenStore(client=client, key_prefix=f'{prefix}:confirm')
    consumer = RedisConfirmationTokenStore(client=client, key_prefix=f'{prefix}:confirm')
    payload = {'appointment_id': 'a-live-1'}

    token = await issuer.issue('cancel_appointment', payload, scope='tenant-a|user-1')
    assert await consumer.consume(token, 'cancel_appointment', payload, scope='tenant-a|user-1') == 'ok'
    assert await consumer.consume(token, 'cancel_appointment', payload, scope='tenant-a|user-1') == 'unknown'


async def test_confirmation_scope_isolated_between_tenants(live_redis):
    from auron_mcp.writes import RedisConfirmationTokenStore

    client, prefix = live_redis
    store = RedisConfirmationTokenStore(client=client, key_prefix=f'{prefix}:confirm')
    payload = {'appointment_id': 'a-live-2'}
    token = await store.issue('cancel_appointment', payload, scope='tenant-a|user-1')

    # MC-01: el scope incluye tenant|usuario; otro tenant no puede usarlo.
    assert await store.consume(token, 'cancel_appointment', payload, scope='tenant-b|user-1') == 'unknown'
    assert await store.consume(token, 'cancel_appointment', payload, scope='tenant-a|user-1') == 'ok'


async def test_idempotency_shared_across_replicas(live_redis):
    from auron_mcp.writes import RedisIdempotencyStore

    client, prefix = live_redis
    writer = RedisIdempotencyStore(client=client, key_prefix=f'{prefix}:idem')
    reader = RedisIdempotencyStore(client=client, key_prefix=f'{prefix}:idem')
    scope = 'tenant-a|user-1'

    assert await reader.get('idem-live-1', scope=scope) is None
    await writer.put('idem-live-1', {'status': 'created', 'id': 42}, scope=scope)
    assert await reader.get('idem-live-1', scope=scope) == {'status': 'created', 'id': 42}
    assert await reader.get('idem-live-1', scope='tenant-b|user-1') is None


async def test_distributed_rate_limit_real_lua(live_redis):
    from auron_mcp.ratelimit import RateLimiter, RateLimitError, RedisBackend

    client, prefix = live_redis
    backend = RedisBackend(client=client, key_prefix=f'{prefix}:ratelimit', on_error='closed')
    limiter = RateLimiter(max_calls=3, window_seconds=60, backend=backend)

    for _ in range(3):
        await limiter.acheck('user-1', 'auron_sales_summary', tenant_id='tenant-a')
    with pytest.raises(RateLimitError):
        await limiter.acheck('user-1', 'auron_sales_summary', tenant_id='tenant-a')

    # Otro tenant tiene su propia ventana.
    await limiter.acheck('user-1', 'auron_sales_summary', tenant_id='tenant-b')
    assert await client.dbsize() >= 1


async def test_fail_closed_when_redis_unreachable():
    """Sin Redis compartido, los writes se rechazan (nunca se ejecutan sin guard)."""
    from auron_mcp.writes import (
        RedisConfirmationTokenStore,
        RedisIdempotencyStore,
        SharedStateUnavailableError,
    )

    unreachable = 'redis://127.0.0.1:56380/0'  # puerto deliberadamente cerrado
    confirmation = RedisConfirmationTokenStore(redis_url=unreachable, timeout_seconds=0.2)
    idempotency = RedisIdempotencyStore(redis_url=unreachable, timeout_seconds=0.2)
    try:
        with pytest.raises(SharedStateUnavailableError):
            await confirmation.issue('cancel_appointment', {}, scope='t1|u1')
        with pytest.raises(SharedStateUnavailableError):
            await idempotency.put('k', {'status': 'created'}, scope='t1|u1')
    finally:
        await confirmation.aclose()
        await idempotency.aclose()
