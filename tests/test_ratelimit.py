import pytest

from auron_mcp.ratelimit import RateLimiter, RateLimitError


def test_allows_calls_under_the_limit():
    limiter = RateLimiter(max_calls=3, window_seconds=60)
    for _ in range(3):
        limiter.check("u1", "auron_sales_summary")


def test_rejects_calls_over_the_limit():
    limiter = RateLimiter(max_calls=2, window_seconds=60)
    limiter.check("u1", "auron_sales_summary")
    limiter.check("u1", "auron_sales_summary")
    with pytest.raises(RateLimitError):
        limiter.check("u1", "auron_sales_summary")


def test_limits_are_isolated_per_user():
    limiter = RateLimiter(max_calls=1, window_seconds=60)
    limiter.check("u1", "auron_sales_summary")
    limiter.check("u2", "auron_sales_summary")  # different user, own bucket


def test_limits_are_isolated_per_tool():
    limiter = RateLimiter(max_calls=1, window_seconds=60)
    limiter.check("u1", "auron_sales_summary")
    limiter.check("u1", "auron_clients_search")  # different tool, own bucket
