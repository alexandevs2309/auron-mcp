"""Structured JSON logging for the AURON MCP server.

Every operational event is emitted as a single-line JSON record so it can be
shipped to a log aggregator (or OpenTelemetry later) without parsing. The
logger NEVER writes secrets, tokens, upstream response bodies, or stack traces:
all string fields pass through :func:`redact` before being emitted.

Output stream: over stdio the transport owns stdout, so records go to stderr;
over streamable-http they go to stdout where the process manager (uvicorn /
Docker) captures them.
"""

import json
import sys
from datetime import datetime, timezone
from typing import Any

from .config import settings
from .secrets import redact


class StructuredLogger:
    """Minimal dependency-free JSON line logger with field redaction."""

    def __init__(self, stream: Any | None = None):
        self._stream = stream or (sys.stderr if settings.transport == "stdio" else sys.stdout)

    def emit(self, level: str, event: str, **fields: Any) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event": event,
        }
        for key, value in fields.items():
            record[key] = redact(value) if isinstance(value, str) else value
        self._stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._stream.flush()

    def info(self, event: str, **fields: Any) -> None:
        self.emit("info", event, **fields)

    def warning(self, event: str, **fields: Any) -> None:
        self.emit("warning", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self.emit("error", event, **fields)


logger = StructuredLogger()


def log_request_duration(tool: str, trace_id: str, outcome: str, duration_ms: int) -> None:
    """Emit the operational counterpart of an audit event."""
    logger.info(
        "tool.request",
        tool=tool,
        trace_id=trace_id,
        outcome=outcome,
        duration_ms=duration_ms,
    )
