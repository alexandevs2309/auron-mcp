"""End-to-end MCP stdio handshake against the real server process.

Spawns `auron-mcp` (mock adapter, via env) and drives a real MCP client
session: initialize, list tools, call a tool, read a resource. This validates
that the decorated server actually works over the wire, not just in-process.
"""

import os
import sys

os.environ["AURON_ADAPTER"] = "mock"

import pytest
from mcp import client



if sys.platform == "win32":
    SERVER_ARGS = [
        os.path.join(
            os.path.dirname(sys.executable),
            "auron-mcp.exe",
        )
    ]
else:
    BIN = os.path.join(
        os.path.dirname(__file__), "..", ".venv", "bin", "auron-mcp"
    )
    SERVER_ARGS = [os.path.abspath(BIN)]



PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.mark.anyio
async def test_stdio_session_calls_a_tool_and_resource():
    env = {**os.environ, "AURON_ADAPTER": "mock", "PYTHONPATH": os.path.join(PROJECT_ROOT, "src")}
    server_params = client.stdio.StdioServerParameters(
        command=SERVER_ARGS[0], args=[], env=env, cwd=PROJECT_ROOT
    )
    async with (
        client.stdio.stdio_client(server_params) as (read, write),
        client.session.ClientSession(read, write) as session,
    ):
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert "auron_sales_summary" in names
            assert "auron_clients_search" in names

            result = await session.call_tool("auron_sales_summary", {})
            assert result.content
            text = result.content[0].text
            assert '"status": "mock"' in text

            search = await session.call_tool("auron_clients_search", {"query": "ana", "limit": 9999})
            assert '"limit": 100' in search.content[0].text

            resources = await session.list_resources()
            assert any(r.uri == "auron://context/current" for r in resources.resources)
            ctx = await session.read_resource("auron://context/current")
            assert "tenant_id=t-mock-1" in ctx.contents[0].text
