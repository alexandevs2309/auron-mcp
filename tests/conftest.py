"""Global test setup.

Ensures a deterministic, offline-safe environment BEFORE any test module (and
therefore any auron_mcp module) is imported: the module-level `settings` in
`auron_mcp/config.py` is frozen at import time, so this must run first.
"""

import os

os.environ["AURON_ADAPTER"] = "mock"
os.environ["AURON_API_BASE_URL"] = ""
os.environ["AURON_MCP_SESSION_PATH"] = ""
os.environ["AURON_API_TOKEN"] = ""
