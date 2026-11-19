"""Redis-backed rate limiting and controlled-write state (no live Redis needed).

The fakes implement the exact semantics of the Lua scripts with an injectable
clock, so concurrency and tenant isolation are exercised in the default suite.
"""

import asyncio
from collections import defaultdict

import pytest

from auron_mcp.audit import AuditLogger
from auron_mcp.auth import AuronIdentity
from auron_mcp.config import Settings
from auron_mcp.context import RequestContext
from auron_mcp.policy import PolicyEngine
from auron_mcp.ratelimit import RateLimiter, RateLimitError, RedisBackend
from auron_mcp.secrets import validate_config
from auron_mcp.tools import AuronTools
from auron_mcp.writes import (
    _TAKE_LUA,
    RedisConfirmationTokenStore,
    RedisIdempotencyStore,
    SharedStateUnavailableError,
)


class FakeSlidingWindow:
    """Async script runner equivalent to the ZSET Lua script."""

    def __init__(self):
        self.hits: dict[str, list[int]] = defaultdict(list)
        self.clock_ms = 0
        self.lock = asyncio.Lock()
        self.denied_keys: list[str] = []

    async def __call__(self, keys: list[str], args: list[int | str]) -> int:
        async with self.lock:
            await asyncio.sleep(0)  # expose races between replicas
            key = keys[0]
            max_calls, window_ms = int(args[0]), int(args[1])
            cutoff = self.clock_ms - window_ms
            self.hits[key] = [ms for ms in self.hits[key] if ms > cutoff]
            if len(self.hits[key]) >= max_calls:
                self.denied_keys.append(key)
                return 0
            self.hits[key].append(self.clock_ms)
            return 1


class FakeRedis:
    """Minimal async client shared by several store instances."""

    def __init__(self, fail_message: str = ""):
        self.data: dict[str, str] = {}
        self.fail_message = fail_message
        self.closed = False

    def _check(self) -> None:
        if self.fail_message:
            raise RuntimeError(self.fail_message)

    async def get(self, key: str) -> str | None:
        self._check()
        return self.data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._check()
        self.data[key] = value

    def register_script(self, script: str):
        async def take(keys: list[str], args: list[list]) -> str | None:
            self._check()
            assert script == _TAKE_LUA
            return self.data.pop(keys[0], None)

        return take

    async def aclose(self) -> None:
        self.closed = True


def make_limiter(script_runner, max_calls=2, on_error="closed"):
    backend = RedisBackend(script_runner=script_runner, on_error=on_error)
    return RateLimiter(max_calls=max_calls, window_seconds=60, backend=backend)


def test_async_backend_requires_acheck():
    limiter = make_limiter(FakeSlidingWindow())
    with pytest.raises(TypeError, match="acheck"):
        limiter.check("u1", "auron_sales_summary", tenant_id="t1")


async def test_sliding_window_allows_then_denies():
    runner = FakeSlidingWindow()
    limiter = make_limiter(runner)
    await limiter.acheck("u1", "auron_sales_summary", tenant_id="t1")
    await limiter.acheck("u1", "auron_sales_summary", tenant_id="t1")
    with pytest.raises(RateLimitError):
        await limiter.acheck("u1", "auron_sales_summary", tenant_id="t1")
    assert len(runner.hits[next(iter(runner.hits))]) == 2  # denied call adds no hit


async def test_window_slides_with_time():
    runner = FakeSlidingWindow()
    limiter = make_limiter(runner, max_calls=1)
    await limiter.acheck("u1", "tool", tenant_id="t1")
    with pytest.raises(RateLimitError):
        await limiter.acheck("u1", "tool", tenant_id="t1")
    runner.clock_ms = 60_001
    await limiter.acheck("u1", "tool", tenant_id="t1")


async def test_buckets_are_hashed_and_tenant_scoped():
    runner = FakeSlidingWindow()
    limiter = make_limiter(runner, max_calls=1)
    await limiter.acheck("u1", "tool", tenant_id="t1")
    await limiter.acheck("u1", "tool", tenant_id="t2")
    await limiter.acheck("u2", "tool", tenant_id="t1")
    assert len(runner.hits) == 3
    for key in runner.hits:
        assert "t1" not in key and "u1" not in key and "tool" not in key


async def test_concurrent_replicas_share_one_window():
    runner = FakeSlidingWindow()
    limiter = make_limiter(runner, max_calls=5)
    results = await asyncio.gather(
        *[
            limiter.acheck("u1", "tool", tenant_id="t1")
            for _ in range(20)
        ],
        return_exceptions=True,
    )
    allowed = [r for r in results if r is None]
    denied = [r for r in results if isinstance(r, RateLimitError)]
    assert len(allowed) == 5
    assert len(denied) == 15


class FailingRunner:
    async def __call__(self, keys, args):
        raise RuntimeError("redis://user:super-secret@redis.internal:6379/0")


async def test_redis_failure_fails_closed_without_leaking_url():
    limiter = make_limiter(FailingRunner())
    with pytest.raises(RateLimitError) as exc:
        await limiter.acheck("u1", "tool", tenant_id="t1")
    assert "super-secret" not in str(exc.value)
    assert "redis.internal" not in str(exc.value)


async def test_redis_failure_can_fail_open_for_reads():
    limiter = make_limiter(FailingRunner(), on_error="open")
    await limiter.acheck("u1", "tool", tenant_id="t1")


async def test_tools_guard_awaits_distributed_limiter():
    runner = FakeSlidingWindow()
    tools = AuronTools(
        _mock_adapter(),
        PolicyEngine(),
        AuditLogger(None),
        make_limiter(runner, max_calls=1),
    )
    ctx = RequestContext(
        identity=AuronIdentity(
            user_id="u1",
            email="u1@auron.test",
            tenant_id="t1",
            role="CLIENT_ADMIN",
            permissions=frozenset({"pos_api.view_sale"}),
        )
    )
    assert (await tools.sales_summary(ctx))["status"] == "mock"
    with pytest.raises(RateLimitError):
        await tools.sales_summary(ctx)


def _mock_adapter():
    from auron_mcp.adapters.mock import MockAuronAdapter

    return MockAuronAdapter()


async def test_confirmations_are_single_use_across_replicas():
    client = FakeRedis()
    issuer = RedisConfirmationTokenStore(client=client, ttl_seconds=60)
    consumer = RedisConfirmationTokenStore(client=client, ttl_seconds=60)
    payload = {"appointment_id": "a1"}
    token = await issuer.issue("cancel_appointment", payload, scope="t1")

    assert await consumer.consume(token, "cancel_appointment", payload, scope="t1") == "ok"
    assert await consumer.consume(token, "cancel_appointment", payload, scope="t1") == "unknown"


async def test_confirmations_bound_to_tenant_and_payload():
    client = FakeRedis()
    store = RedisConfirmationTokenStore(client=client, ttl_seconds=60)
    payload = {"appointment_id": "a1"}
    token = await store.issue("cancel_appointment", payload, scope="t1")
    assert await store.consume(token, "cancel_appointment", payload, scope="t2") == "unknown"
    token2 = await store.issue("cancel_appointment", payload, scope="t1")
    assert await store.consume(token2, "cancel_appointment", {"appointment_id": "a2"}, scope="t1") == "mismatch"


async def test_idempotency_shared_across_replicas():
    client = FakeRedis()
    writer = RedisIdempotencyStore(client=client, ttl_seconds=60)
    reader = RedisIdempotencyStore(client=client, ttl_seconds=60)
    await writer.put("key-1", {"status": "created"}, scope="t1")
    assert await reader.get("key-1", scope="t1") == {"status": "created"}
    assert await reader.get("key-1", scope="t2") is None


async def test_write_state_fails_closed_on_redis_error():
    client = FakeRedis(fail_message="redis://user:super-secret@host/0")
    confirmations = RedisConfirmationTokenStore(client=client)
    idempotency = RedisIdempotencyStore(client=client)
    with pytest.raises(SharedStateUnavailableError) as exc:
        await confirmations.issue("cancel_appointment", {}, scope="t1")
    assert "super-secret" not in str(exc.value)
    with pytest.raises(SharedStateUnavailableError):
        await idempotency.put("k", {}, scope="t1")


def test_config_and_validation_for_shared_state():
    settings = Settings.from_env(
        {
            "AURON_ADAPTER": "mock",
            "AURON_REDIS_URL": "rediss://:super-secret@redis.internal:6380/1",
            "AURON_REDIS_ON_ERROR": "open",
            "AURON_REDIS_TIMEOUT_SECONDS": "2.5",
        }
    )
    assert settings.redis_url.startswith("rediss://")
    assert settings.redis_on_error == "open"
    assert settings.redis_timeout_seconds == 2.5
    assert validate_config(settings) == []

    broken = Settings.from_env({"AURON_REDIS_URL": "http://redis.internal:6379"})
    assert any("AURON_REDIS_URL" in p for p in validate_config(broken))
