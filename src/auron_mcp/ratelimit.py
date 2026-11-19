"""Sliding-window rate limiting with an in-memory and a Redis backend.

The bucket key is always ``(tenant_id, user_id, tool)`` (ADR-001). The tenant
comes from the authenticated identity, never from model input.

``RedisBackend`` uses a single Lua script (atomic ZREMRANGEBYSCORE + ZCARD +
ZADD + PEXPIRE) so several gateway replicas share one window. Redis failures
fail closed by default, matching RBAC deny-by-default and read-only mode.
"""

import hashlib
import time
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from typing import Any

from .logging import logger

_KEY_PREFIX = "auron:mcp:rl:v1"

# TIME is evaluated by Redis, so replicas with skewed clocks share one window.
# The exclusive upper bound keeps hits exactly ``window`` seconds old, matching
# InMemoryBackend's ``now - hit > window``. A denied call does not add a hit.
_SLIDING_WINDOW_LUA = """
local key = KEYS[1]
local max_calls = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local member = ARGV[3]

local t = redis.call('TIME')
local now_ms = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)

redis.call('ZREMRANGEBYSCORE', key, '-inf', '(' .. (now_ms - window_ms))
if redis.call('ZCARD', key) >= max_calls then
    redis.call('PEXPIRE', key, window_ms + 1000)
    return 0
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms + 1000)
return 1
"""


class RateLimitError(PermissionError):
    pass


class RateLimitBackend(ABC):
    """Abstract rate-limit storage backend."""

    @abstractmethod
    def allow(self, key: tuple[str, ...], max_calls: int, window: float) -> None:
        """Record a hit. Raise ``RateLimitError`` if the window is full."""

    @abstractmethod
    def cleanup(self, max_calls: int, window: float) -> None:
        """Expire old entries. Called periodically or on each check."""


class AsyncRateLimitBackend(ABC):
    """Backend whose check must be awaited (used by the Redis implementation)."""

    @abstractmethod
    async def aallow(self, key: tuple[str, ...], max_calls: int, window: float) -> None:
        """Record a hit. Raise ``RateLimitError`` if the window is full."""

    def cleanup(self, max_calls: int, window: float) -> None:
        """Redis expires keys itself; kept for API symmetry."""


class InMemoryBackend(RateLimitBackend):
    """Per-process sliding-window counter. Not distributed-safe."""

    def __init__(self):
        self._hits: dict[tuple[str, ...], deque] = defaultdict(deque)

    def allow(self, key: tuple[str, ...], max_calls: int, window: float) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= max_calls:
            raise RateLimitError(
                f"Rate limit exceeded for '{key[1]}' ({max_calls} calls / {window:.0f}s)"
            )
        hits.append(now)

    def cleanup(self, max_calls: int, window: float) -> None:
        now = time.monotonic()
        empty = []
        for key, hits in self._hits.items():
            while hits and now - hits[0] > window:
                hits.popleft()
            if not hits:
                empty.append(key)
        for key in empty:
            del self._hits[key]


def bucket_key(key: tuple[str, ...], prefix: str = _KEY_PREFIX) -> str:
    """Stable, fixed-length Redis key; never stores identifiers in clear text."""
    digest = hashlib.sha256("\x1f".join(key).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


class RedisBackend(AsyncRateLimitBackend):
    """Distributed sliding window backed by a Redis sorted set.

    ``client`` and ``script_runner`` are injection seams for tests. The redis
    package is imported lazily so core installs keep working without it.
    """

    def __init__(
        self,
        redis_url: str = "",
        *,
        client: Any | None = None,
        script_runner: Any | None = None,
        key_prefix: str = _KEY_PREFIX,
        on_error: str = "closed",
        timeout_seconds: float = 1.0,
    ) -> None:
        if not redis_url and client is None and script_runner is None:
            raise ValueError("AURON_REDIS_URL is required for the Redis rate limit backend")
        if on_error not in {"closed", "open"}:
            raise ValueError("on_error must be 'closed' or 'open'")

        self._client = client
        self._script_runner = script_runner
        self._key_prefix = key_prefix
        self._on_error = on_error

        if script_runner is None:
            if client is None:
                try:
                    from redis import asyncio as aioredis
                except ImportError as exc:  # pragma: no cover - depends on install extras
                    raise RuntimeError(
                        "RedisBackend requires the redis extra: pip install 'auron-mcp[redis]'"
                    ) from exc
                self._client = aioredis.from_url(
                    redis_url,
                    socket_timeout=timeout_seconds,
                    socket_connect_timeout=timeout_seconds,
                    decode_responses=True,
                )
            self._script_runner = self._client.register_script(_SLIDING_WINDOW_LUA)

    async def aallow(self, key: tuple[str, ...], max_calls: int, window: float) -> None:
        window_ms = max(1, round(window * 1000))
        arguments: list[int | str] = [max_calls, window_ms, uuid.uuid4().hex]
        try:
            allowed = int(
                await self._script_runner(keys=[bucket_key(key, self._key_prefix)], args=arguments)
            )
        except Exception as exc:
            # Never log str(exc): it may contain the Redis URL and its password.
            if self._on_error == "open":
                logger.warning("ratelimit.redis_unavailable", error_type=type(exc).__name__)
                return
            raise RateLimitError("Rate limiter unavailable") from exc

        if allowed == 0:
            raise RateLimitError(
                f"Rate limit exceeded for '{key[1]}' ({max_calls} calls / {window:.0f}s)"
            )

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()


class RateLimiter:
    """Sliding-window rate limiter backed by a pluggable backend.

    Default is ``InMemoryBackend`` (single-process). With ``AURON_REDIS_URL``
    configured, ``server.build_rate_limiter()`` injects ``RedisBackend``.
    """

    def __init__(
        self,
        max_calls: int = 30,
        window_seconds: float = 60.0,
        backend: RateLimitBackend | AsyncRateLimitBackend | None = None,
    ):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._backend = backend or InMemoryBackend()

    def check(self, user_id: str, tool: str, tenant_id: str = "") -> None:
        """Synchronous check. Async backends must use :meth:`acheck`."""
        if isinstance(self._backend, AsyncRateLimitBackend):
            raise TypeError("backend is async; use await limiter.acheck()")
        self._backend.allow(
            (tenant_id, user_id, tool), self.max_calls, self.window_seconds
        )

    async def acheck(self, user_id: str, tool: str, tenant_id: str = "") -> None:
        """Awaitable check that accepts both backend flavours."""
        key = (tenant_id, user_id, tool)
        if isinstance(self._backend, AsyncRateLimitBackend):
            await self._backend.aallow(key, self.max_calls, self.window_seconds)
        else:
            self._backend.allow(key, self.max_calls, self.window_seconds)
