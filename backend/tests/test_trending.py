"""
Trending hashtags: GET /hashtags/trending.

A global aggregate -- the top tags by post volume in a recent window. Redis is
disabled in tests (see conftest), so each call computes fresh.
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


def _trending(client: TestClient) -> list[dict]:
    response = client.get("/api/v1/hashtags/trending")
    assert response.status_code == 200
    return response.json()


def test_trending_ranks_tags_by_recent_post_volume() -> None:
    alice, _ = _register("alice")
    for _ in range(3):
        _post(alice, "a #python post")
    for _ in range(2):
        _post(alice, "a #django post")
    _post(alice, "a #ruby post")

    assert _trending(alice) == [
        {"tag": "python", "post_count": 3},
        {"tag": "django", "post_count": 2},
        {"tag": "ruby", "post_count": 1},
    ]


def test_trending_respects_the_time_window() -> None:
    alice, _ = _register("alice")
    fresh = _post(alice, "fresh #news today")
    old = _post(alice, "old #news")

    # Age the old post's tag rows to just outside the window.
    db = _db()
    try:
        stale = datetime.now(timezone.utc) - timedelta(
            hours=settings.trending_window_hours + 1
        )
        db.query(PostHashtag).filter_by(post_id=old).update({"created_at": stale})
        db.commit()
    finally:
        db.close()

    trending = _trending(alice)
    news = next(row for row in trending if row["tag"] == "news")
    assert news["post_count"] == 1, "only the in-window post counts"
    assert fresh != old


def test_trending_excludes_deleted_authors_tags() -> None:
    alice, _ = _register("alice")
    bob, bob_id = _register("bob")

    _post(alice, "alice on #topic")
    _post(bob, "bob on #topic")

    # Before deletion: both count.
    assert _trending(alice)[0] == {"tag": "topic", "post_count": 2}

    db = _db()
    try:
        user_repository.soft_delete_user(db, bob_id, scrubbed_password_hash="x")
    finally:
        db.close()

    # After: only alice's tagged post drives the trend.
    assert _trending(alice)[0] == {"tag": "topic", "post_count": 1}


def test_trending_is_empty_with_no_hashtags() -> None:
    alice, _ = _register("alice")
    _post(alice, "just a plain post, no tags")
    assert _trending(alice) == []
