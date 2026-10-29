"""Portable limiter contract. The bounded process-local implementation is local/test only."""

import hashlib
import hmac
import ipaddress
import math
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import ApiError


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int

    def headers(self) -> dict[str, str]:
        headers = {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(self.remaining),
            "RateLimit-Reset": str(self.reset_after_seconds),
        }
        if not self.allowed:
            headers["Retry-After"] = str(self.reset_after_seconds)
        return headers


class RateLimiter(Protocol):
    async def consume(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult: ...


def raise_for_limit(result: RateLimitResult) -> None:
    if not result.allowed:
        raise ApiError("rate_limited", 429, headers=result.headers())


def hash_ip(ip: str, secret: bytes, *, key_version: str = "v1") -> bytes:
    """Only a versioned HMAC leaves this boundary; never retain/log raw addresses.

    Callers use the direct peer address unless ingress trust is explicitly configured.
    Rotation invalidates old windows; keep overlap outside this local implementation.
    """
    if len(secret) < 32 or not key_version or len(key_version) > 32:
        raise ValueError("A strong HMAC secret and a short rotation version are required")
    canonical = ipaddress.ip_address(ip).compressed.encode("ascii")
    return hmac.digest(
        secret, b"penforge:identity-ip:" + key_version.encode() + b":" + canonical, hashlib.sha256
    )


class PostgresRateLimiter:
    """Atomic fixed windows shared by every API instance.

    One statement inserts or increments the current window, so concurrent replicas
    cannot each spend the same allowance. Only HMAC digests/UUID-derived keys reach
    this table; no raw client address is stored. Expired windows are pruned lazily.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        clock: Callable[[], float] = time.time,
        cleanup_probability: float = 0.01,
    ):
        self._engine = engine
        self._clock = clock
        self._cleanup_probability = min(max(cleanup_probability, 0.0), 1.0)

    async def consume(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        if not key or len(key) > 256 or limit < 1 or window_seconds < 1:
            raise ValueError("A bounded key and positive limits are required")
        now = self._clock()
        if not math.isfinite(now):
            raise ValueError("The limiter clock must be finite")
        start = math.floor(now / window_seconds) * window_seconds
        reset = max(1, math.ceil(start + window_seconds - now))
        async with self._engine.begin() as connection:
            used = (
                await connection.execute(
                    text(
                        """
                        INSERT INTO rate_limit_windows
                          (key, limit_amount, window_seconds, window_start, used)
                        VALUES (:key, :limit, :window_seconds, :start, 1)
                        ON CONFLICT (key, limit_amount, window_seconds, window_start)
                        DO UPDATE SET used = rate_limit_windows.used + 1
                        RETURNING used
                        """
                    ),
                    {
                        "key": key,
                        "limit": limit,
                        "window_seconds": window_seconds,
                        "start": datetime.fromtimestamp(start, UTC),
                    },
                )
            ).scalar_one()
            if random.random() < self._cleanup_probability:
                await connection.execute(
                    text("DELETE FROM rate_limit_windows WHERE window_start < :cutoff"),
                    {"cutoff": datetime.fromtimestamp(now - 86400, UTC)},
                )
        return RateLimitResult(used <= limit, limit, max(0, limit - used), reset)


class InMemoryRateLimiter:
    """Fixed windows, thread-safe and bounded; never a distributed abuse-control claim.

    At capacity, new keys fail closed until an existing window expires. Active windows
    are not evicted, since eviction would allow quota farming with arbitrary new keys.
    """

    def __init__(
        self,
        *,
        app_env: str = "local",
        max_keys: int = 10000,
        clock: Callable[[], float] = time.time,
    ):
        if app_env not in ("local", "test"):
            raise ValueError("Process-local rate limiting is restricted to local/test")
        if max_keys < 1:
            raise ValueError("The limiter needs a positive key capacity")
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, int, int], tuple[float, int]] = {}

    async def consume(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        if not key or len(key) > 256 or limit < 1 or window_seconds < 1:
            raise ValueError("A bounded key and positive limits are required")
        now = self._clock()
        if not math.isfinite(now):
            raise ValueError("The limiter clock must be finite")
        bucket = (key, limit, window_seconds)
        with self._lock:
            expiry, used = self._windows.get(bucket, (0.0, 0))
            if now >= expiry:
                expiry = (math.floor(now / window_seconds) + 1) * window_seconds
                used = 0
                if bucket not in self._windows and len(self._windows) >= self._max_keys:
                    self._windows = {
                        k: value for k, value in self._windows.items() if value[0] > now
                    }
                    if len(self._windows) >= self._max_keys:
                        earliest = min(value[0] for value in self._windows.values())
                        return RateLimitResult(False, limit, 0, max(1, math.ceil(earliest - now)))
            reset = max(1, math.ceil(expiry - now))
            if used >= limit:
                return RateLimitResult(False, limit, 0, reset)
            used += 1
            self._windows[bucket] = (expiry, used)
            return RateLimitResult(True, limit, limit - used, reset)
