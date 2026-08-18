import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    trace_id: str
    user_id: str
    tenant_id: str
    role: str
    tool: str
    outcome: str
    duration_ms: int


class AuditLogger:
    """Append-only JSONL audit sink for MCP tool calls, plus stdout.

    Records trace_id, user, tenant, tool, outcome and duration. Never logs
    secrets, tokens, response bodies or internal stack traces.
    """

    def __init__(self, log_path: str | None = None, sink=None):
        self.log_path = Path(log_path) if log_path else None
        self._sink = sink or print
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        context,
        tool: str,
        outcome: str = "success",
        duration_ms: int = 0,
    ) -> AuditEvent:
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            trace_id=context.trace_id,
            user_id=context.identity.user_id,
            tenant_id=context.identity.tenant_id or "",
            role=context.identity.role,
            tool=tool,
            outcome=outcome,
            duration_ms=duration_ms,
        )
        line = json.dumps(asdict(event), ensure_ascii=False)
        self._sink(f"[AUDIT] {line}")
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return event
