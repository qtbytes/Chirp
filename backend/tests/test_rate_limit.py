"""
Rate limiting.

The bug these guard against: `rate_limiter` used to take its limits as literal
arguments, so the two call sites that existed froze `10` and `30` at import time
and the `rate_limit_like_*`, `rate_limit_comment_*` and `rate_limit_timeline_*`
settings configured nothing at all. Meanwhile POST /auth/login had no limiter,
leaving password guessing unthrottled.
"""

import re

import pytest
from app.core import rate_limit
from app.core.config import settings
from app.core.rate_limit import REGISTERED_BUCKETS
from fastapi.testclient import TestClient
from main import app


class _FakeSortedSets:
    """
    The slice of Redis the limiter uses: one sorted set per bucket.

    Hand-rolled rather than pulled in as a dependency -- the limiter touches five
    commands, and a fake makes the window boundaries exact instead of racing a
    real clock.
    """

    def __init__(self) -> None:
        self.sets: dict[str, dict[str, float]] = {}

    def pipeline(self, transaction: bool = True) -> "_FakePipeline":
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, store: _FakeSortedSets) -> None:
        self._store = store
        self._queued: list = []

    def zremrangebyscore(self, key, minimum, maximum):
        self._queued.append(("zremrangebyscore", key, minimum, maximum))
        return self

    def zadd(self, key, mapping):
        self._queued.append(("zadd", key, mapping))
        return self

    def zcard(self, key):
        self._queued.append(("zcard", key))
        return self

    def zrange(self, key, start, stop, withscores=False):
        self._queued.append(("zrange", key, start, stop, withscores))
        return self

    def expire(self, key, seconds):
        self._queued.append(("expire", key, seconds))
        return self

    def execute(self) -> list:
        results = []
        for operation, *args in self._queued:
            bucket = self._store.sets.setdefault(args[0], {})
            if operation == "zremrangebyscore":
                _, low, high = args
                for member in [m for m, s in bucket.items() if low <= s <= high]:
                    del bucket[member]
                results.append(None)
            elif operation == "zadd":
                bucket.update(args[1])
                results.append(1)
            elif operation == "zcard":
                results.append(len(bucket))
            elif operation == "zrange":
                _, start, stop, _ = args
                ordered = sorted(bucket.items(), key=lambda item: item[1])
                results.append(ordered[start : stop + 1])
            elif operation == "expire":
                results.append(True)
        return results


@pytest.fixture
def redis(monkeypatch) -> _FakeSortedSets:
    """Turn the limiter on (conftest disables it globally) against a fake Redis."""
    store = _FakeSortedSets()
    monkeypatch.setattr(rate_limit, "get_redis_client", lambda: store)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    return store


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _login(client: TestClient, password: str = "wrong-password"):
    return client.post(
        "/api/v1/auth/login",
        json={"username": "victim", "password": password},
    )


# --------------------------------------------------------------- dead settings


def test_no_rate_limit_setting_is_dead_and_no_bucket_is_unconfigured() -> None:
    """
    Every ``rate_limit_<bucket>_*`` setting drives a real call site, and every
    call site has settings behind it.

    This is the regression test for the original defect. `rate_limiter` refuses
    to build a dependency for a bucket with no settings, so the second direction
    is enforced at import; this pins the first.
    """
    configured = {
        match.group(1)
        for field in type(settings).model_fields
        if (match := re.fullmatch(r"rate_limit_(.+)_max_requests", field))
    }

    assert configured == REGISTERED_BUCKETS, (
        "rate limit settings and call sites disagree.\n"
        f"  configured but never used: {sorted(configured - REGISTERED_BUCKETS)}\n"
        f"  used but not configurable: {sorted(REGISTERED_BUCKETS - configured)}"
    )


def test_rate_limiter_rejects_a_bucket_with_no_settings() -> None:
    with pytest.raises(RuntimeError, match="rate_limit_nonexistent_max_requests"):
        rate_limit.rate_limiter("nonexistent")


# ------------------------------------------------------------------ the limits


@pytest.mark.parametrize("max_requests", [1, 3, 5])
def test_the_configured_limit_is_the_limit(
    client: TestClient, redis, monkeypatch, max_requests: int
) -> None:
    """
    Exactly ``rate_limit_login_max_requests`` requests get through.

    Parametrised because the old code could have passed a fixed-number test
    while ignoring the setting entirely.
    """
    monkeypatch.setattr(settings, "rate_limit_login_max_requests", max_requests)
    monkeypatch.setattr(settings, "rate_limit_login_window_seconds", 300)

    for attempt in range(max_requests):
        assert _login(client).status_code == 401, f"attempt {attempt} was limited early"

    assert _login(client).status_code == 429


def test_limits_are_read_per_request_not_captured_at_import(
    client: TestClient, redis, monkeypatch
) -> None:
    """Raising the limit mid-flight lets a throttled caller straight through."""
    monkeypatch.setattr(settings, "rate_limit_login_max_requests", 1)
    monkeypatch.setattr(settings, "rate_limit_login_window_seconds", 300)

    assert _login(client).status_code == 401
    assert _login(client).status_code == 429

    monkeypatch.setattr(settings, "rate_limit_login_max_requests", 50)
    assert _login(client).status_code == 401


def test_a_throttled_response_says_when_to_come_back(
    client: TestClient, redis, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "rate_limit_login_max_requests", 1)
    monkeypatch.setattr(settings, "rate_limit_login_window_seconds", 300)

    _login(client)
    response = _login(client)

    assert response.status_code == 429
    retry_after = int(response.headers["Retry-After"])
    assert 1 <= retry_after <= 300


# -------------------------------------------------------------------- identity


def test_login_buckets_by_ip_even_when_the_caller_holds_a_session(
    client: TestClient, redis, monkeypatch
) -> None:
    """
    An attacker guessing passwords may hold a perfectly good session of their
    own. If login bucketed on it they could mint a fresh allowance per guess by
    rotating cookies, so the bucket must key on the peer address regardless.
    """
    monkeypatch.setattr(settings, "rate_limit_login_max_requests", 2)

    client.post(
        "/api/v1/auth/register",
        json={"username": "attacker", "password": "supersecret1"},
    )
    assert client.cookies.get(settings.session_cookie_name), "expected a session cookie"

    _login(client)

    login_keys = [key for key in redis.sets if key.startswith("rate_limit:login:")]
    assert login_keys, "login was not rate limited at all"
    assert all(key.startswith("rate_limit:login:ip:") for key in login_keys), login_keys


def test_authenticated_buckets_key_on_the_user_not_the_address(
    client: TestClient, redis
) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"username": "liker", "password": "supersecret1"},
    )
    tweet = client.post("/api/v1/tweets", json={"content": "hello"})
    assert tweet.status_code == 201, tweet.text

    client.post(f"/api/v1/tweets/{tweet.json()['id']}/likes/toggle")

    like_keys = [key for key in redis.sets if key.startswith("rate_limit:like:")]
    assert like_keys, "likes are not rate limited"
    assert all(key.startswith("rate_limit:like:user:") for key in like_keys), like_keys


# ------------------------------------------------------------------- fail-shut


def test_rate_limiting_fails_closed_when_redis_is_gone(
    client: TestClient, monkeypatch
) -> None:
    """
    A limit that cannot be enforced is refused, not waived. The timeline and
    link-preview *caches* degrade without Redis; this guarantee does not.
    """
    monkeypatch.setattr(rate_limit, "get_redis_client", lambda: None)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)

    assert _login(client).status_code == 503


def test_disabling_the_limiter_never_touches_redis(
    client: TestClient, monkeypatch
) -> None:
    """`RATE_LIMIT_ENABLED=false` is how you run the app without Redis."""

    def explode():  # pragma: no cover - must never run
        raise AssertionError("the disabled limiter reached for Redis")

    monkeypatch.setattr(rate_limit, "get_redis_client", explode)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)

    for _ in range(20):
        assert _login(client).status_code == 401
