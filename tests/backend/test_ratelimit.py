import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.core.errors import ApiError
from app.core.ratelimit import InMemoryRateLimiter, hash_ip, raise_for_limit


def test_fixed_window_boundary_and_typed_headers():
    now = [100.0]
    limiter = InMemoryRateLimiter(app_env="test", clock=lambda: now[0])

    async def run():
        first = await limiter.consume("identity:one", limit=2, window_seconds=60)
        assert first.allowed
        assert first.headers() == {
            "RateLimit-Limit": "2",
            "RateLimit-Remaining": "1",
            "RateLimit-Reset": "20",
        }
        assert (await limiter.consume("identity:one", limit=2, window_seconds=60)).allowed
        blocked = await limiter.consume("identity:one", limit=2, window_seconds=60)
        assert not blocked.allowed
        assert blocked.headers()["Retry-After"] == "20"
        with pytest.raises(ApiError) as error:
            raise_for_limit(blocked)
        assert error.value.code == "rate_limited"
        assert error.value.status == 429
        assert error.value.headers["Retry-After"] == "20"
        now[0] = 120.0
        assert (await limiter.consume("identity:one", limit=2, window_seconds=60)).allowed

    asyncio.run(run())


def test_concurrent_consumption_never_overruns_limit():
    limiter = InMemoryRateLimiter(app_env="test", clock=lambda: 100.0)

    def consume(_):
        return asyncio.run(limiter.consume("identity:one", limit=7, window_seconds=60)).allowed

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(consume, range(100)))
    assert sum(results) == 7


def test_bounded_memory_fails_closed_without_evicting_active_windows():
    now = [100.0]
    limiter = InMemoryRateLimiter(app_env="test", max_keys=1, clock=lambda: now[0])

    async def run():
        assert (await limiter.consume("one", limit=1, window_seconds=60)).allowed
        assert not (await limiter.consume("two", limit=1, window_seconds=60)).allowed
        assert not (await limiter.consume("one", limit=1, window_seconds=60)).allowed
        now[0] = 120.0
        assert (await limiter.consume("two", limit=1, window_seconds=60)).allowed

    asyncio.run(run())


@pytest.mark.parametrize("environment", ["preview", "production"])
def test_local_limiter_cannot_be_used_for_hosted_abuse_control(environment):
    with pytest.raises(ValueError):
        InMemoryRateLimiter(app_env=environment)


def test_ip_hmac_normalization_and_rotation():
    secret = b"a" * 32
    first = hash_ip("2001:0db8:0:0:0:0:0:1", secret)
    assert first == hash_ip("2001:db8::1", secret)
    assert len(first) == 32
    assert first != hash_ip("2001:db8::1", b"b" * 32)
    assert first != hash_ip("2001:db8::1", secret, key_version="v2")
    with pytest.raises(ValueError):
        hash_ip("127.0.0.1", b"too-short")
