"""Per-session request throttling.

Without this, one stuck client — a retry loop, a held-down Enter key, a
misbehaving script — can drain a whole day's LLM quota in under a minute.
That is the failure this module exists to prevent; it is a budget guard, not
an anti-abuse system (auth belongs at the gateway).

The window is a **sliding log** in Redis: one sorted-set entry per allowed
request, scored by timestamp. Compared with the usual `INCR`+`EXPIRE` fixed
window, it costs one small key per session and cannot be gamed by the
boundary burst that lets a fixed window pass 2x the limit across two adjacent
windows. Redis (not process memory) so the limit still holds when the app runs
as more than one worker.

Rejected requests are removed from the log again, so being throttled never
extends the block — the caller is told exactly how long to wait.
"""

import time
from dataclasses import dataclass

from redis.asyncio import Redis


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    #: Seconds until the oldest request leaves the window (0 when allowed).
    retry_after: int = 0

    def message(self, what: str) -> str:
        return (
            f"rate limit reached — too many {what}. "
            f"Try again in {self.retry_after}s (or raise ASSISTANT_RATE_LIMIT_*)."
        )


class RateLimiter:
    """Sliding-window limiter keyed by session (or any caller identity)."""

    def __init__(self, redis: Redis, *, enabled: bool = True) -> None:
        self._redis = redis
        self._enabled = enabled

    @staticmethod
    def _key(bucket: str, identity: str) -> str:
        return f"ratelimit:{bucket}:{identity}"

    async def check(
        self, bucket: str, identity: str, *, limit: int, window_seconds: int
    ) -> RateLimitDecision:
        """Record one request and say whether it is allowed.

        `limit <= 0` disables the bucket, which is how a deployment turns off a
        single limit without turning off the whole feature.
        """
        if not self._enabled or limit <= 0:
            return RateLimitDecision(allowed=True)

        key = self._key(bucket, identity)
        now = time.time()
        cutoff = now - window_seconds

        # One round trip, transactional: drop expired entries, add this one,
        # count what is left, and keep the key alive only as long as the window.
        pipe = self._redis.pipeline(transaction=True)
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zadd(key, {f"{now:.6f}": now})
        pipe.zcard(key)
        pipe.expire(key, window_seconds + 1)
        used: int = (await pipe.execute())[2]

        if used <= limit:
            return RateLimitDecision(allowed=True)

        # Over the limit: take this request back out, so a client that keeps
        # hammering does not push its own reset time further away.
        await self._redis.zrem(key, f"{now:.6f}")
        oldest = await self._redis.zrange(key, 0, 0, withscores=True)
        # `zrange(withscores=True)` is typed as loosely as Redis replies are;
        # the score is the timestamp this entry was written with.
        wait = window_seconds - (now - float(oldest[0][1])) if oldest else window_seconds
        return RateLimitDecision(allowed=False, retry_after=max(1, round(wait)))

    async def reset(self, bucket: str, identity: str) -> None:
        """Forget a caller's history — used by tests and by `/api/sessions/new`."""
        await self._redis.delete(self._key(bucket, identity))
