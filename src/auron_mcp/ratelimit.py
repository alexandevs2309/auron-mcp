import time
from collections import defaultdict, deque


class RateLimitError(PermissionError):
    pass


class RateLimiter:
    """In-memory sliding-window limiter keyed by (user_id, tool).

    Not distributed-safe: replace with a shared store (Redis) before running
    more than one MCP instance. See docs/ROADMAP.md.
    """

    def __init__(self, max_calls: int = 30, window_seconds: float = 60.0):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)

    def check(self, user_id: str, tool: str) -> None:
        key = (user_id, tool)
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= self.max_calls:
            raise RateLimitError(
                f"Rate limit exceeded for '{tool}' "
                f"({self.max_calls} calls / {self.window_seconds:.0f}s)"
            )
        hits.append(now)
