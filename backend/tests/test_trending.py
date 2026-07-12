"""
Trending hashtags: GET /hashtags/trending.

Trending ranks by *velocity* -- recent activity relative to a tag's own baseline
-- so a spiking tag outranks a steadily popular one. The displayed ``post_count``
is the recent-window volume. Redis is disabled in tests (see conftest), so each
call computes fresh; the windows/threshold are pinned per test for determinism.
"""

from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db.database import get_db
from app.models.post_hashtag import PostHashtag
from app.repositories import user_repository
from fastapi.testclient import TestClient
from main import app


def _register(username: str) -> tuple[TestClient, int]:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201
    return client, response.json()["id"]


def _db():
    return next(app.dependency_overrides[get_db]())


def _post(client: TestClient, content: str) -> int:
    response = client.post("/api/v1/tweets", json={"content": content})
    assert response.status_code == 201
    return response.json()["id"]


def _backdate(post_ids: list[int], hours: float) -> None:
    """Move the given posts' hashtag rows into the past (to seed a baseline)."""
    db = _db()
    try:
        when = datetime.now(timezone.utc) - timedelta(hours=hours)
        db.query(PostHashtag).filter(PostHashtag.post_id.in_(post_ids)).update(
            {"created_at": when}, synchronize_session=False
        )
        db.commit()
    finally:
        db.close()


def _trending(client: TestClient) -> list[dict]:
    response = client.get("/api/v1/hashtags/trending")
    assert response.status_code == 200
    return response.json()


def test_velocity_ranks_a_spike_above_a_higher_volume_steady_tag(monkeypatch) -> None:
    # Recent = last 24h; baseline = the 24h before that.
    monkeypatch.setattr(settings, "trending_window_hours", 24)
    monkeypatch.setattr(settings, "trending_baseline_hours", 24)
    monkeypatch.setattr(settings, "trending_min_posts", 2)
    alice, _ = _register("alice")

    # #spike: brand new, 3 posts now, no history.
    for _ in range(3):
        _post(alice, "breaking #spike")

    # #steady: MORE recent volume (4 now) but a heavy baseline (6 a day ago),
    # so its velocity is low.
    for _ in range(4):
        _post(alice, "daily #steady")
    steady_old = [_post(alice, "older #steady") for _ in range(6)]
    _backdate(steady_old, hours=36)

    trending = _trending(alice)
    tags = [row["tag"] for row in trending]
    assert tags.index("spike") < tags.index("steady"), (
        "the spiking tag ranks above the higher-volume but steady one"
    )
    # post_count is the recent-window volume, not the velocity score.
    by_tag = {row["tag"]: row["post_count"] for row in trending}
    assert by_tag["spike"] == 3
    assert by_tag["steady"] == 4


def test_a_tag_below_the_recent_floor_does_not_trend(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trending_window_hours", 24)
    monkeypatch.setattr(settings, "trending_min_posts", 3)
    alice, _ = _register("alice")

    for _ in range(3):
        _post(alice, "#hot")
    _post(alice, "#cold")  # only one recent post -> below the floor

    tags = [row["tag"] for row in _trending(alice)]
    assert "hot" in tags
    assert "cold" not in tags


def test_recent_window_bounds_the_displayed_count(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trending_window_hours", 24)
    monkeypatch.setattr(settings, "trending_baseline_hours", 24)
    monkeypatch.setattr(settings, "trending_min_posts", 1)
    alice, _ = _register("alice")

    for _ in range(2):
        _post(alice, "#news")
    old = [_post(alice, "#news")]
    _backdate(old, hours=100)  # outside both the recent and baseline windows

    news = next(row for row in _trending(alice) if row["tag"] == "news")
    assert news["post_count"] == 2, "only in-window posts count"


def test_trending_excludes_deleted_authors_tags(monkeypatch) -> None:
    monkeypatch.setattr(settings, "trending_min_posts", 1)
    alice, _ = _register("alice")
    bob, bob_id = _register("bob")

    _post(alice, "alice on #topic")
    _post(alice, "alice again #topic")
    _post(bob, "bob on #topic")

    assert next(r for r in _trending(alice) if r["tag"] == "topic")["post_count"] == 3

    db = _db()
    try:
        user_repository.soft_delete_user(db, bob_id, scrubbed_password_hash="x")
    finally:
        db.close()

    assert next(r for r in _trending(alice) if r["tag"] == "topic")["post_count"] == 2


def test_trending_is_empty_with_no_hashtags() -> None:
    alice, _ = _register("alice")
    _post(alice, "just a plain post, no tags")
    assert _trending(alice) == []
