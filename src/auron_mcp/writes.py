"""Controlled-write state: confirmation tokens and idempotency records.

Both stores are in-memory (single-process deployment is the local-first model).
They are bounded and TTL-based so memory stays flat; distributed variants are
a later roadmap item (M4: shared store) once a Redis-backed deployment exists.

Confirmation tokens follow a two-phase flow for high-impact writes:

1. A ``*_preview_*`` tool returns the exact effect plus a short-lived,
   single-use token bound to the operation name AND a canonical hash of the
   payload (``hashlib.sha256(json.dumps(payload, sort_keys=True))``).
2. The write tool must present that token; ``consume`` atomically deletes it,
   so replay is impossible and a different payload with the same token is
   rejected.
"""

import hashlib
import json
import secrets
import threading
import time
from typing import Any

from .logging import logger

_CONFIRM_PREFIX = "auron:mcp:confirm:v1"
_IDEMPOTENCY_PREFIX = "auron:mcp:idem:v1"

# GET + DEL in one atomic step (works on older Redis than GETDEL).
_TAKE_LUA = """
local value = redis.call('GET', KEYS[1])
if value then redis.call('DEL', KEYS[1]) end
return value
"""


class SharedStateUnavailableError(RuntimeError):
    """The shared Redis state needed for a controlled write is unreachable."""


def _state_key(prefix: str, scope: str, identifier: str) -> str:
    digest = hashlib.sha256(f"{scope}\x1f{identifier}".encode()).hexdigest()
    return f"{prefix}:{digest}"


def payload_hash(payload: dict[str, Any], scope: str = "") -> str:
    """Canonical hash of a payload: stable across key ordering and spacing.

    ``scope`` is normally the tenant id (ADR-001). Two tenants with an
    identical payload must never be able to reuse each other's confirmation
    token, even if the gateway process keeps both stores in memory.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if scope:
        canonical = f"{scope}|{canonical}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ConfirmationTokenStore:
    """Single-use, short-TTL confirmation tokens bound to (operation, payload)."""

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 1_000):
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._tokens: dict[str, tuple[float, str, str]] = {}
        self._lock = threading.Lock()

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    def issue(self, operation: str, payload: dict[str, Any], scope: str = "") -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sweep_locked()
            self._tokens[token] = (
                time.monotonic() + self._ttl_seconds,
                operation,
                payload_hash(payload, scope),
            )
        return token

    def consume(
        self, token: str, operation: str, payload: dict[str, Any], scope: str = ""
    ) -> str:
        """Atomically consume a token; returns ``ok`` or one of:
        ``unknown``, ``expired``, ``used``, ``mismatch``."""
        with self._lock:
            entry = self._tokens.pop(token, None)
        if entry is None:
            return "unknown"
        expires_at, bound_operation, bound_hash = entry
        if time.monotonic() > expires_at:
            return "expired"
        if bound_operation != operation or bound_hash != payload_hash(payload, scope):
            return "mismatch"
        return "ok"

    def _sweep_locked(self) -> None:
        now = time.monotonic()
        for token in [t for t, (exp, _op, _h) in self._tokens.items() if exp <= now]:
            self._tokens.pop(token, None)
        if len(self._tokens) >= self._max_entries:
            for token in list(self._tokens)[: max(0, len(self._tokens) - self._max_entries)]:
                self._tokens.pop(token, None)


class IdempotencyStore:
    """Remembers successful write responses by client-supplied key.

    A retry with the same key returns the original response instead of
    repeating the side effect. Failures are NOT recorded, so a retry after a
    transient failure re-attempts the write.
    """

    def __init__(self, ttl_seconds: int = 86_400, max_entries: int = 10_000):
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._records: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _scoped(key: str, scope: str) -> str:
        return f"{scope}|{key}" if scope else key

    def get(self, key: str, scope: str = "") -> dict[str, Any] | None:
        key = self._scoped(key, scope)
        with self._lock:
            record = self._records.get(key)
        if record is None:
            return None
        expires_at, response = record
        if time.monotonic() > expires_at:
            with self._lock:
                self._records.pop(key, None)
            return None
        return response

    def put(self, key: str, response: dict[str, Any], scope: str = "") -> None:
        key = self._scoped(key, scope)
        with self._lock:
            self._sweep_locked()
            self._records[key] = (time.monotonic() + self._ttl_seconds, response)

    def _sweep_locked(self) -> None:
        now = time.monotonic()
        for key in [k for k, (exp, _r) in self._records.items() if exp <= now]:
            self._records.pop(key, None)
        if len(self._records) >= self._max_entries:
            for key in list(self._records)[: max(0, len(self._records) - self._max_entries)]:
                self._records.pop(key, None)


def _redis_client(redis_url: str, timeout_seconds: float):
    if not redis_url:
        raise ValueError("AURON_REDIS_URL is required for Redis-backed write state")
    try:
        from redis import asyncio as aioredis
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise RuntimeError(
            "Redis write state requires the redis extra: pip install 'auron-mcp[redis]'"
        ) from exc
    return aioredis.from_url(
        redis_url,
        socket_timeout=timeout_seconds,
        socket_connect_timeout=timeout_seconds,
        decode_responses=True,
    )


class RedisConfirmationTokenStore:
    """Single-use confirmation tokens shared by all gateway replicas."""

    def __init__(
        self,
        redis_url: str = "",
        *,
        ttl_seconds: int = 300,
        client: Any | None = None,
        key_prefix: str = _CONFIRM_PREFIX,
        timeout_seconds: float = 1.0,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix
        self._client = client or _redis_client(redis_url, timeout_seconds)
        self._take = self._client.register_script(_TAKE_LUA)

    @property
    def ttl_seconds(self) -> int:
        return self._ttl_seconds

    async def issue(
        self, operation: str, payload: dict[str, Any], scope: str = ""
    ) -> str:
        token = secrets.token_urlsafe(32)
        record = {
            "expires_at": time.time() + self._ttl_seconds,
            "operation": operation,
            "hash": payload_hash(payload, scope),
        }
        try:
            await self._client.set(
                _state_key(self._key_prefix, scope, token),
                json.dumps(record),
                ex=self._ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - any Redis error must fail closed
            self._on_redis_error(exc)
        return token

    async def consume(
        self, token: str, operation: str, payload: dict[str, Any], scope: str = ""
    ) -> str:
        try:
            raw = await self._take(
                keys=[_state_key(self._key_prefix, scope, token)], args=[]
            )
        except Exception as exc:  # noqa: BLE001 - any Redis error must fail closed
            self._on_redis_error(exc)
            return "unknown"
        if not raw:
            return "unknown"
        record = json.loads(raw)
        if time.time() > record["expires_at"]:
            return "expired"
        if record["operation"] != operation or record["hash"] != payload_hash(payload, scope):
            return "mismatch"
        return "ok"

    async def aclose(self) -> None:
        await self._client.aclose()

    def _on_redis_error(self, exc: Exception) -> None:
        # Writes always fail closed: skipping confirmation or idempotency would
        # allow duplicate side effects. str(exc) may carry credentials.
        logger.error("writes.redis_unavailable", error_type=type(exc).__name__)
        raise SharedStateUnavailableError("Shared state is unavailable") from exc


class RedisIdempotencyStore:
    """Successful write responses shared by all gateway replicas (TTL-bounded)."""

    def __init__(
        self,
        redis_url: str = "",
        *,
        ttl_seconds: int = 86_400,
        client: Any | None = None,
        key_prefix: str = _IDEMPOTENCY_PREFIX,
        timeout_seconds: float = 1.0,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix
        self._client = client or _redis_client(redis_url, timeout_seconds)

    async def get(self, key: str, scope: str = "") -> dict[str, Any] | None:
        try:
            raw = await self._client.get(_state_key(self._key_prefix, scope, key))
        except Exception as exc:  # noqa: BLE001 - any Redis error must fail closed
            self._on_redis_error(exc)
            return None
        return json.loads(raw) if raw else None

    async def put(self, key: str, response: dict[str, Any], scope: str = "") -> None:
        try:
            await self._client.set(
                _state_key(self._key_prefix, scope, key),
                json.dumps(response),
                ex=self._ttl_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - any Redis error must fail closed
            self._on_redis_error(exc)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _on_redis_error(self, exc: Exception) -> None:
        # Writes always fail closed: skipping confirmation or idempotency would
        # allow duplicate side effects. str(exc) may carry credentials.
        logger.error("writes.redis_unavailable", error_type=type(exc).__name__)
        raise SharedStateUnavailableError("Shared state is unavailable") from exc
