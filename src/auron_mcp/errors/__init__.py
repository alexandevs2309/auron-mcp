from ..adapters.base import AdapterError
from ..auth.provider import IdentityUnavailableError
from ..policy import AuthorizationError
from ..ratelimit import RateLimitError
from ..secrets import redact


class WriteDisabledError(RuntimeError):
    """A write was attempted while the server runs in read-only mode."""


class ConfirmationError(RuntimeError):
    """A confirmation token was missing, expired, replayed or mismatched.

    ``reason`` is one of the safe categories surfaced to the model.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _safe_detail(value: str) -> str:
    """Defense in depth: strip configured secrets from any surfaced detail."""
    return redact(value)


def normalize_error(exc: Exception) -> dict:
    """Convert an internal exception into a safe, model-visible payload.

    The error boundary (server._safe) guarantees no exception message, token,
    upstream body, or stack trace leaks to the model. Every failure collapses
    to a fixed-shape `{"error": category, "detail": ...}` dict; unknown
    exceptions become a generic "internal" error. Details are redacted even
    for known exception types as defense in depth.
    """
    if isinstance(exc, AuthorizationError):
        return {"error": "forbidden", "detail": _safe_detail(str(exc))}
    if isinstance(exc, RateLimitError):
        return {"error": "rate_limited", "detail": _safe_detail(str(exc))}
    if isinstance(exc, IdentityUnavailableError):
        return {"error": "unauthorized", "detail": _safe_detail(str(exc))}
    if isinstance(exc, WriteDisabledError):
        return {
            "error": "read_only",
            "detail": "Server is in read-only mode (AURON_MCP_READ_ONLY=true)",
        }
    if isinstance(exc, ConfirmationError):
        reason = exc.reason
        message = {
            "unknown": "Confirmation token not found; request a fresh preview",
            "expired": "Confirmation token expired; request a fresh preview",
            "used": "Confirmation token already used",
            "mismatch": "Confirmation token does not match the operation payload",
        }.get(reason, "Confirmation failed")
        return {"error": f"confirmation_{reason}", "detail": _safe_detail(message)}
    if isinstance(exc, AdapterError):
        return {
            "error": exc.category,
            "detail": _safe_detail(exc.detail or "Upstream request failed"),
        }
    return {"error": "internal", "detail": "Unexpected internal error"}
