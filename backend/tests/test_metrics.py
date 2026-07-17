"""
The metrics exist to make silent failure visible, so the tests check exactly
that: the scrape endpoint reports traffic and pipeline state, and the two
deliberately-swallowed Redis failures (a dropped SSE nudge, a failed fan-out
enqueue) leave a countable trace instead of vanishing.

Counters are process-global and other tests also drive them, so every
assertion here is on a delta around the action, never on an absolute value.
"""

import pytest
from app.core.metrics import REGISTRY
from app.services import events, timeline_service
from fastapi.testclient import TestClient
from main import app
from redis.exceptions import RedisError


def _sample(name: str, labels: dict[str, str] | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def test_metrics_endpoint_reports_http_traffic_and_pipeline_state() -> None:
    client = TestClient(app)
    before = _sample(
        "chirp_http_requests_total",
        {"method": "GET", "route": "/", "status": "200"},
    )

    assert client.get("/").status_code == 200

    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.text

    after = _sample(
        "chirp_http_requests_total",
        {"method": "GET", "route": "/", "status": "200"},
    )
    assert after == before + 1

    # The fan-out pipeline gauges are read from the test Redis at scrape time.
    assert "chirp_redis_up 1.0" in body
    assert 'chirp_rq_queue_depth{queue="' in body
    assert 'chirp_rq_failed_jobs{queue="' in body
    assert "chirp_rq_workers" in body

    # Route templates, not raw paths: per-id URLs must not mint one series per
    # tweet, and unroutable paths must collapse into one label.
    client.get("/api/v1/tweets/999999")
    client.get("/no/such/path")
    assert (
        _sample(
            "chirp_http_requests_total",
            {"method": "GET", "route": "/api/v1/tweets/{tweet_id}", "status": "401"},
        )
        >= 1
    )
    assert (
        _sample(
            "chirp_http_requests_total",
            {"method": "GET", "route": "unmatched", "status": "404"},
        )
        >= 1
    )


def test_dropped_notification_publish_is_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DeadRedis:
        def publish(self, *args: object, **kwargs: object) -> None:
            raise RedisError("connection refused")

    monkeypatch.setattr(events, "get_redis_client", lambda: _DeadRedis())

    labels = {"operation": "notification_publish"}
    before = _sample("chirp_redis_failures_total", labels)

    # Must swallow the error (the commit already happened) but count the drop.
    events._publish(user_id=1, payload={"type": "notification"})

    assert _sample("chirp_redis_failures_total", labels) == before + 1


def test_fanout_enqueue_falls_back_inline_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran_inline: list[tuple[int, int]] = []

    def _unreachable() -> None:
        raise RedisError("connection refused")

    monkeypatch.setattr(timeline_service, "get_redis_client", _unreachable)
    monkeypatch.setattr(
        timeline_service,
        "run_feed_fanout_job",
        lambda tweet_id, author_id: ran_inline.append((tweet_id, author_id)),
    )

    labels = {"operation": "fanout_enqueue"}
    before = _sample("chirp_redis_failures_total", labels)

    timeline_service.enqueue_feed_fanout_job(tweet_id=7, author_id=3)

    assert ran_inline == [(7, 3)]
    assert _sample("chirp_redis_failures_total", labels) == before + 1


def test_timeline_cache_lookups_are_counted() -> None:
    client, _ = _register(client_name="metrics_cache_user")

    miss_before = _sample("chirp_timeline_cache_total", {"result": "miss"})
    hit_before = _sample("chirp_timeline_cache_total", {"result": "hit"})

    # First read misses and fills the cache; second read hits it.
    assert client.get("/api/v1/timeline/home?strategy=read").status_code == 200
    assert client.get("/api/v1/timeline/home?strategy=read").status_code == 200

    assert _sample("chirp_timeline_cache_total", {"result": "miss"}) == miss_before + 1
    assert _sample("chirp_timeline_cache_total", {"result": "hit"}) == hit_before + 1


def _register(client_name: str) -> tuple[TestClient, int]:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": client_name,
            "email": f"{client_name}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201
    return client, response.json()["id"]
