from collections.abc import Callable
from time import time_ns

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.session_store import SessionBackendUnavailable, resolve_session
from app.db.redis_client import get_redis_client


def rate_limiter(bucket_name: str, max_requests: int, window_seconds: int) -> Callable:
    """
    Redis-backed sliding-window rate limiter.

    Why this version is more realistic:
    - shared across multiple app instances
    - survives per-process memory isolation
    - works better under concurrent traffic than an in-memory dict

    Data structure:
    - one Redis sorted set per bucket
    - score = request timestamp in milliseconds
    - member = unique request id derived from current time

    Flow per request:
    1. remove entries older than the window
    2. add current request timestamp
    3. count how many remain in the window
    4. set expiry so inactive buckets are cleaned up automatically
    """

    def dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return

        redis_client = get_redis_client()
        if redis_client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Redis is required for rate limiting but is unavailable.",
            )

        now_ns = time_ns()
        now_ms = now_ns // 1_000_000
        window_start_ms = now_ms - window_seconds * 1000

        # Never key the bucket off a client-supplied header: a caller could
        # rotate it per request and skip the limit entirely. Prefer the signed
        # session cookie, and fall back to the peer address. Behind a reverse
        # proxy that address is only the real client when uvicorn runs with
        # --proxy-headers, otherwise every caller shares the proxy's bucket.
        #
        # refresh=False: rate limiting is a read-only observation and must not
        # keep a session alive on its own.
        try:
            user_id = resolve_session(
                request.cookies.get(settings.session_cookie_name),
                refresh=False,
            )
        except SessionBackendUnavailable:
            user_id = None

        if user_id is not None:
            identity = f"user:{user_id}"
        else:
            identity = f"ip:{request.client.host if request.client else 'unknown'}"

        bucket_key = f"rate_limit:{bucket_name}:{identity}"
        member = f"{now_ns}:{identity}"

        try:
            pipeline = redis_client.pipeline(transaction=True)
            pipeline.zremrangebyscore(bucket_key, 0, window_start_ms)
            pipeline.zadd(bucket_key, {member: now_ms})
            pipeline.zcard(bucket_key)
            pipeline.expire(bucket_key, window_seconds + 1)
            _, _, request_count, _ = pipeline.execute()
        except RedisError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limiter storage is unavailable.",
            ) from exc

        if int(request_count) > max_requests:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded for {bucket_name}.",
            )

    return dependency
