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


def payload_hash(payload: dict[str, Any]) -> str:
    """Canonical hash of a payload: stable across key ordering and spacing."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
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

    def issue(self, operation: str, payload: dict[str, Any]) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sweep_locked()
            self._tokens[token] = (
                time.monotonic() + self._ttl_seconds,
                operation,
                payload_hash(payload),
            )
        return token

    def consume(self, token: str, operation: str, payload: dict[str, Any]) -> str:
        """Atomically consume a token; returns ``ok`` or one of:
        ``unknown``, ``expired``, ``used``, ``mismatch``."""
        with self._lock:
            entry = self._tokens.pop(token, None)
        if entry is None:
            return "unknown"
        expires_at, bound_operation, bound_hash = entry
        if time.monotonic() > expires_at:
            return "expired"
        if bound_operation != operation or bound_hash != payload_hash(payload):
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

    def get(self, key: str) -> dict[str, Any] | None:
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

    def put(self, key: str, response: dict[str, Any]) -> None:
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
