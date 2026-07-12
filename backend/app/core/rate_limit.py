from collections.abc import Callable
from math import ceil
from time import time_ns
from typing import Literal

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.session_store import SessionBackendUnavailable, resolve_session
from app.db.redis_client import get_redis_client

# How a caller is identified for bucketing.
#
# - "session_or_ip": the signed session cookie when present, else the peer
#   address. Right for authenticated endpoints, where the limit belongs to the
#   account rather than the network it happens to be on.
# - "ip": always the peer address. Required for the auth endpoints: a login
#   request may carry a cookie, and bucketing on it would let an attacker mint a
#   fresh bucket per guess simply by rotating sessions.
IdentitySource = Literal["session_or_ip", "ip"]

# Every bucket handed to rate_limiter(), filled in at import as the routes are
# defined. tests/test_rate_limit.py asserts this matches the buckets that
# Settings configures, so a limit can never again be configured but unused --
# or used with a limit nobody can tune.
REGISTERED_BUCKETS: set[str] = set()


def _setting_names(bucket_name: str) -> tuple[str, str]:
    return (
        f"rate_limit_{bucket_name}_max_requests",
        f"rate_limit_{bucket_name}_window_seconds",
    )


def _policy(bucket_name: str) -> tuple[int, int]:
    """
    Read the bucket's limits from settings.

    Deliberately read per request, not captured when the dependency is built:
    the old signature took the numbers as arguments, so every call site froze a
    literal at import time and the ``rate_limit_*`` settings could not move it.
    """
    max_name, window_name = _setting_names(bucket_name)
    return getattr(settings, max_name), getattr(settings, window_name)


def _client_ip(request: Request) -> str:
    # Never key off a client-supplied header: a caller could rotate it per
    # request and skip the limit entirely. Behind a reverse proxy the peer
    # address is only the real client when uvicorn runs with --proxy-headers,
    # otherwise every caller shares the proxy's bucket.
    return f"ip:{request.client.host if request.client else 'unknown'}"


def _session_or_ip(request: Request) -> str:
    # refresh=False: rate limiting is a read-only observation and must not keep
    # a session alive on its own.
    try:
        user_id = resolve_session(
            request.cookies.get(settings.session_cookie_name),
            refresh=False,
        )
    except SessionBackendUnavailable:
        user_id = None

    if user_id is None:
        return _client_ip(request)
    return f"user:{user_id}"


def _enforce(bucket_name: str, identity: str) -> None:
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
    4. read the oldest survivor, which is when the caller regains a slot
    5. set expiry so inactive buckets are cleaned up automatically

    Fails closed. Rate limiting is a guarantee, not a cache: without Redis the
    limit cannot be enforced, so the request is refused rather than waved
    through. The timeline and link-preview *caches* degrade instead.
    """
    max_requests, window_seconds = _policy(bucket_name)

    now_ns = time_ns()
    now_ms = now_ns // 1_000_000
    window_start_ms = now_ms - window_seconds * 1000

    bucket_key = f"rate_limit:{bucket_name}:{identity}"
    member = f"{now_ns}:{identity}"

    try:
        redis_client = get_redis_client()
        pipeline = redis_client.pipeline(transaction=True)
        pipeline.zremrangebyscore(bucket_key, 0, window_start_ms)
        pipeline.zadd(bucket_key, {member: now_ms})
        pipeline.zcard(bucket_key)
        pipeline.zrange(bucket_key, 0, 0, withscores=True)
        pipeline.expire(bucket_key, window_seconds + 1)
        _, _, request_count, oldest, _ = pipeline.execute()
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiter storage is unavailable.",
        ) from exc

    if int(request_count) <= max_requests:
        return

    # The caller gets a slot back when the oldest entry leaves the window.
    # Falling back to the full window is only reachable if the set emptied
    # between the count and the read.
    retry_after = window_seconds
    if oldest:
        _, oldest_ms = oldest[0]
        retry_after = ceil((oldest_ms + window_seconds * 1000 - now_ms) / 1000)

    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Rate limit exceeded for {bucket_name}.",
        headers={"Retry-After": str(max(1, retry_after))},
    )


def rate_limiter(
    bucket_name: str,
    *,
    identity: IdentitySource = "session_or_ip",
) -> Callable:
    """
    Build a rate-limiting dependency for ``bucket_name``.

    The limits come from ``Settings.rate_limit_<bucket_name>_{max_requests,
    window_seconds}``. A bucket with no matching settings raises here, at import
    time, rather than on the first request that would have been limited.
    """
    max_name, window_name = _setting_names(bucket_name)
    for name in (max_name, window_name):
        if not hasattr(settings, name):
            raise RuntimeError(
                f"rate_limiter({bucket_name!r}) requires Settings.{name}; "
                "add it to app/core/config.py."
            )

    REGISTERED_BUCKETS.add(bucket_name)
    resolve_identity = _client_ip if identity == "ip" else _session_or_ip

    def dependency(request: Request) -> None:
        if not settings.rate_limit_enabled:
            return
        _enforce(bucket_name, resolve_identity(request))

    return dependency
