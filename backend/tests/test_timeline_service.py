from datetime import datetime, timezone

from app.models.post import Post
from app.services.timeline_service import decode_cursor, encode_cursor
from conftest import TestingSessionLocal
from fastapi.testclient import TestClient
from main import app


def test_cursor_round_trip() -> None:
    created_at = datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc)
    cursor = encode_cursor(created_at, 42)

    decoded_created_at, decoded_id = decode_cursor(cursor)

    assert decoded_created_at == created_at
    assert decoded_id == 42


def test_decode_invalid_cursor() -> None:
    decoded_created_at, decoded_id = decode_cursor("bad-cursor")

    assert decoded_created_at is None
    assert decoded_id is None


def test_manual_refresh_replaces_a_stale_first_page_cache() -> None:
    client = TestClient(app)
    registered = client.post(
        "/api/v1/auth/register",
        json={"username": "refresh_test", "password": "password123"},
    )
    assert registered.status_code == 201, registered.text
    user_id = registered.json()["id"]
    url = "/api/v1/timeline/home"
    # Cache an empty first page, then model a post that has committed before
    # its asynchronous cache invalidation has run.
    assert client.get(url).json()["items"] == []
    with TestingSessionLocal() as db:
        post = Post(user_id=user_id, content="A newly committed post")
        db.add(post)
        db.commit()
        post_id = post.id

    assert client.get(url).json()["items"] == []
    refreshed = client.get(url, params={"refresh": "true"})
    assert refreshed.status_code == 200, refreshed.text
    assert [item["id"] for item in refreshed.json()["items"]] == [post_id]
    # Subsequent ordinary visits benefit from the refreshed cache too.
    assert [item["id"] for item in client.get(url).json()["items"]] == [post_id]
    invalid = client.get(url, params={"refresh": "true", "cursor": "bad-cursor"})
    assert invalid.status_code == 400
