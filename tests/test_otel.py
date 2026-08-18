"""Tests for the optional OpenTelemetry tracing layer (extra `[otel]`).

Tracing is opt-in and lazily imported: disabled by default it must be a no-op,
and when a tracer is configured every tool call records one span carrying the
same trace_id the audit log uses. Uses an in-memory exporter, never a network
collector.
"""

import os

os.environ["AURON_ADAPTER"] = "mock"

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from auron_mcp import otel
from auron_mcp.adapters.mock import MockAuronAdapter
from auron_mcp.audit import AuditLogger
from auron_mcp.auth import AuronIdentity
from auron_mcp.context import new_context
from auron_mcp.policy import PolicyEngine
from auron_mcp.policy.roles import permissions_for_role
from auron_mcp.tools import AuronTools


@pytest.fixture
def exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider.get_tracer("test")


def test_trace_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(otel, "_configured", False)
    monkeypatch.setattr(otel, "_tracer", None)
    assert otel.setup_otel() is False
    otel.span_tool(
        "sales.summary.read",
        "trace-1",
        user_id="u",
        tenant_id="t",
        role="CLIENT_ADMIN",
        outcome="success",
        duration_ms=1,
    )


def test_span_tool_records_success_span(monkeypatch, exporter):
    in_memory, tracer = exporter
    monkeypatch.setattr(otel, "_tracer", tracer)
    otel.span_tool(
        "sales.summary.read",
        "trace-abc",
        user_id="u1",
        tenant_id="t1",
        role="CLIENT_ADMIN",
        outcome="success",
        duration_ms=42,
    )
    spans = in_memory.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "auron.sales.summary.read"
    assert span.attributes["mcp.trace_id"] == "trace-abc"
    assert span.attributes["mcp.tenant_id"] == "t1"
    assert span.attributes["mcp.role"] == "CLIENT_ADMIN"
    assert span.attributes["mcp.outcome"] == "success"
    assert span.attributes["mcp.duration_ms"] == 42
    assert span.status.status_code == StatusCode.OK


def test_span_tool_records_error_status(monkeypatch, exporter):
    in_memory, tracer = exporter
    monkeypatch.setattr(otel, "_tracer", tracer)
    otel.span_tool(
        "sales.summary.read",
        "trace-err",
        user_id="u1",
        tenant_id="t1",
        role="CLIENT_ADMIN",
        outcome="upstream_error",
        duration_ms=7,
        error=RuntimeError("boom"),
    )
    spans = in_memory.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.status.status_code == StatusCode.ERROR
    assert any(event.name == "exception" for event in span.events)


@pytest.mark.asyncio
async def test_tool_call_wires_span_with_audit_trace_id(monkeypatch, exporter):
    in_memory, tracer = exporter
    monkeypatch.setattr(otel, "_tracer", tracer)
    tools = AuronTools(
        MockAuronAdapter(),
        PolicyEngine(),
        AuditLogger(sink=lambda _line: None),
    )
    identity = AuronIdentity(
        user_id="u2",
        email="u2@auron.test",
        tenant_id="t2",
        role="CLIENT_ADMIN",
        permissions=permissions_for_role("CLIENT_ADMIN"),
    )
    ctx = new_context(identity, trace_id="trace-wired")
    result = await tools.sales_summary(ctx)
    assert result["status"] == "mock"
    spans = in_memory.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "auron.sales.summary.read"
    assert spans[0].attributes["mcp.trace_id"] == "trace-wired"
