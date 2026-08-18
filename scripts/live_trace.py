"""Live stdio round-trip proving real trace propagation.

Spawns the real server (real backend on localhost:8002, caja session) and
drives a real MCP client through a tools/call request. The audit event for
that call must carry the incoming MCP request id (a JSON-RPC integer issued
by the session layer), not the `trace-` fallback generated per-call.

    AURON_API_BASE_URL=http://localhost:8002 .venv/bin/python scripts/live_trace.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from mcp import client

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".venv", "bin", "auron-mcp"))
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOG = Path(tempfile.gettempdir()) / "auron_trace_audit.jsonl"
if LOG.exists():
    LOG.unlink()


async def main() -> None:
    env = {
        **os.environ,
        "AURON_ADAPTER": "http",
        "AURON_API_BASE_URL": "http://localhost:8002",
        "AURON_MCP_SESSION_PATH": os.path.expanduser("~/.auron-mcp/session.json"),
        "AURON_AUDIT_LOG_PATH": str(LOG),
        "PYTHONPATH": os.path.join(PROJECT_ROOT, "src"),
    }
    server_params = client.stdio.StdioServerParameters(
        command=BIN, args=[], env=env, cwd=PROJECT_ROOT
    )
    async with (
        client.stdio.stdio_client(server_params) as (read, write),
        client.session.ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("auron_sales_summary", {})
        assert result.content, "empty tool result"
        print("tool output:", result.content[0].text[:120])

    lines = [json.loads(l) for l in LOG.read_text(encoding="utf-8").strip().splitlines()]
    assert lines, "no audit events written"
    trace_id = lines[0]["trace_id"]
    print("audit trace_id:", repr(trace_id))
    assert trace_id.isdigit(), f"trace is not the JSON-RPC request id: {trace_id!r}"
    assert not trace_id.startswith("trace-"), "used per-call fallback trace, not incoming request id"
    print("TRACE PROPAGATION OK")


if __name__ == "__main__":
    asyncio.run(main())
