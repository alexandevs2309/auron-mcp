"""Optional OpenTelemetry tracing for the AURON MCP server (extra `[otel]`).

Enabled with ``AURON_MCP_OTEL_ENABLED=true`` and an OTLP/HTTP collector
(``AURON_MCP_OTEL_ENDPOINT``, default ``http://127.0.0.1:4318/v1/traces``).
The SDK is imported lazily so the core install (without the extra) keeps
working; without the extra a warning is logged and tracing silently stays off.

Structured logs in :mod:`auron_mcp.logging` remain the extraction point;
spans add parent/child relationships, latency and error signals for a backend
or APM. Every tool call records one span keyed by the same ``trace_id`` the
audit log uses.
"""

from typing import Any

from .config import settings
from .logging import logger

_configured = False
_tracer: Any = None


def setup_otel() -> bool:
    """Configure the global tracer provider once; True when tracing is active."""
    global _configured, _tracer
    if _configured:
        return _tracer is not None
    _configured = True
    if not settings.otel_enabled:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "otel.unavailable",
            reason="opentelemetry packages not installed (pip install '.[otel]')",
        )
        return False
    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.otel_service_name})
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
    )
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(settings.otel_service_name)
    logger.info("otel.enabled", endpoint=settings.otel_endpoint)
    return True


def span_tool(
    tool: str,
    trace_id: str,
    *,
    user_id: str,
    tenant_id: str,
    role: str,
    outcome: str,
    duration_ms: int,
    error: BaseException | None = None,
) -> None:
    """Record one tool-call span; a no-op unless tracing is configured."""
    if _tracer is None:
        return
    from opentelemetry.trace import Status, StatusCode

    span = _tracer.start_span(
        f"auron.{tool}",
        attributes={
            "mcp.tool": tool,
            "mcp.trace_id": trace_id,
            "mcp.user_id": user_id,
            "mcp.tenant_id": tenant_id or "",
            "mcp.role": role,
            "mcp.outcome": outcome,
            "mcp.duration_ms": duration_ms,
        },
    )
    if error is not None:
        span.record_exception(error)
        span.set_status(Status(StatusCode.ERROR))
    else:
        span.set_status(Status(StatusCode.OK))
    span.end()
