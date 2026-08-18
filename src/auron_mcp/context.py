import time
import uuid
from dataclasses import dataclass

from .auth.identity import AuronIdentity


@dataclass(frozen=True)
class RequestContext:
    identity: AuronIdentity
    trace_id: str = ""
    started_at: float = 0.0


def new_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:16]}"


def new_context(
    identity: AuronIdentity, trace_id: str | None = None
) -> RequestContext:
    return RequestContext(
        identity=identity,
        trace_id=trace_id or new_trace_id(),
        started_at=time.monotonic(),
    )
