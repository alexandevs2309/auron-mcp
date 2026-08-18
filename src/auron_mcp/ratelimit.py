import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque


class RateLimitError(PermissionError):
    pass


class RateLimitBackend(ABC):
    """Abstract rate-limit storage backend. The ``RateLimiter`` delegates
    hit-tracking here; swap implementations for distributed deployments."""

    @abstractmethod
    def allow(self, key: tuple[str, str], max_calls: int, window: float) -> None:
        """Record a hit. Raise ``RateLimitError`` if the window is full."""

    @abstractmethod
    def cleanup(self, max_calls: int, window: float) -> None:
        """Expire old entries. Called periodically or on each check."""


class InMemoryBackend(RateLimitBackend):
    """Per-process sliding-window counter. Not distributed-safe."""

    def __init__(self):
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def allow(self, key: tuple[str, str], max_calls: int, window: float) -> None:
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


class RedisBackend(RateLimitBackend):
    """Future: Redis-backed rate limiter using sorted sets.

    Blocked on: Redis infrastructure in the deployment. When ready, use
    ``ZRANGEBYSCORE`` + ``ZCARD`` for the sliding window and ``EXPIRE``
    for automatic key cleanup.

    Instantiation requires ``redis.asyncio`` (``pip install redis``).
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0"):
        self._redis_url = redis_url
        # TODO: self._redis = redis.asyncio.from_url(redis_url)

    def allow(self, key: tuple[str, str], max_calls: int, window: float) -> None:
        # TODO: ZRANGEBYSCORE + ZCARD + ZADD + EXPIRE
        raise NotImplementedError("Redis backend requires redis.asyncio package")

    def cleanup(self, max_calls: int, window: float) -> None:
        # Redis handles expiry automatically via EXPIRE
        pass


class RateLimiter:
    """Sliding-window rate limiter backed by a pluggable ``RateLimitBackend``.

    Default is ``InMemoryBackend`` (single-process). For multi-instance
    deployments, inject ``RedisBackend`` once Redis is available.
    """

    def __init__(
        self,
        max_calls: int = 30,
        window_seconds: float = 60.0,
        backend: RateLimitBackend | None = None,
    ):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._backend = backend or InMemoryBackend()

    def check(self, user_id: str, tool: str) -> None:
        self._backend.allow((user_id, tool), self.max_calls, self.window_seconds)
