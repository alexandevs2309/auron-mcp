import json
import os
import subprocess
import sys
from pathlib import Path

from auron_mcp.audit import AuditLogger
from auron_mcp.auth import AuronIdentity
from auron_mcp.context import RequestContext


def context():
    return RequestContext(
        identity=AuronIdentity(
            user_id="u1", email="u1@auron.test", tenant_id="t1", role="CLIENT_ADMIN",
            permissions=frozenset({"pos_api.view_sale"}),
        ),
        trace_id="trace-test",
    )


def test_record_returns_event_with_outcome_success():
    event = AuditLogger().record(context(), "sales.summary.read")
    assert event.outcome == "success"
    assert event.tenant_id == "t1"


def test_record_can_mark_denied_outcome():
    event = AuditLogger().record(context(), "sales.summary.read", outcome="denied")
    assert event.outcome == "denied"


def test_events_persist_to_jsonl(tmp_path):
    log_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(str(log_path))
    logger.record(context(), "sales.summary.read")
    logger.record(context(), "clients.search.read", outcome="denied")

    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["tool"] == "sales.summary.read"
    assert first["trace_id"] == "trace-test"
    second = json.loads(lines[1])
    assert second["outcome"] == "denied"


def test_audit_never_writes_to_stdio_protocol_channel():
    """MC-04: `[AUDIT]` must not corrupt the JSON-RPC stdout framing."""
    root = Path(__file__).resolve().parent.parent
    snippet = """
import sys
from auron_mcp.audit import AuditLogger
from auron_mcp.auth import AuronIdentity
from auron_mcp.context import RequestContext
ctx = RequestContext(
    identity=AuronIdentity(
        user_id="u1", email="u1@auron.test", tenant_id="t1", role="CLIENT_ADMIN",
        permissions=frozenset({"pos_api.view_sale"}),
    ),
    trace_id="trace-stdio",
)
AuditLogger().record(ctx, "sales.summary.read")
sys.stdout.write('{"jsonrpc":"2.0","id":1,"result":{}}\\n')
"""
    env = {**os.environ, "PYTHONPATH": str(root / "src"), "AURON_ADAPTER": "mock"}
    completed = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "[AUDIT]" not in completed.stdout
    assert completed.stdout.strip() == '{"jsonrpc":"2.0","id":1,"result":{}}'
    assert "[AUDIT]" in completed.stderr
