from conftest import TestingSessionLocal
from fastapi.testclient import TestClient
from main import app


def register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": "password123"},
    )
    assert response.status_code == 201
    return response.json()


def test_edit_tweet_updates_content_and_marks_edited() -> None:
    client = TestClient(app)
    register(client, "alice")
    tweet = client.post("/api/v1/tweets", json={"content": "original"}).json()
    assert tweet["edited_at"] is None

    response = client.patch(
        f"/api/v1/tweets/{tweet['id']}", json={"content": "updated"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "updated"
    assert body["edited_at"] is not None

    fetched = client.get(f"/api/v1/tweets/{tweet['id']}").json()
    assert fetched["content"] == "updated"
    assert fetched["edited_at"] is not None


def test_edit_tweet_rejects_non_author() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    register(alice, "alice")
    register(bob, "bob")

    tweet = alice.post("/api/v1/tweets", json={"content": "mine"}).json()
    response = bob.patch(
        f"/api/v1/tweets/{tweet['id']}", json={"content": "hijacked"}
    )
    assert response.status_code == 403
    # unchanged
    assert alice.get(f"/api/v1/tweets/{tweet['id']}").json()["content"] == "mine"


def test_edit_tweet_missing_returns_404() -> None:
    client = TestClient(app)
    register(client, "alice")
    assert (
        client.patch("/api/v1/tweets/999999", json={"content": "x"}).status_code
        == 404
    )


def test_delete_tweet_removes_thread_and_engagement() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    register(alice, "alice")
    register(bob, "bob")

    tweet = alice.post("/api/v1/tweets", json={"content": "delete me"}).json()
    # bob likes and comments; the comment gets a nested reply
    bob.post(f"/api/v1/tweets/{tweet['id']}/likes/toggle")
    comment = bob.post(
        f"/api/v1/tweets/{tweet['id']}/comments", json={"content": "a reply"}
    ).json()
    alice.post(
        f"/api/v1/comments/{comment['id']}/comments", json={"content": "nested"}
    )

    response = alice.delete(f"/api/v1/tweets/{tweet['id']}")
    assert response.status_code == 204

    # the tweet and its whole thread are gone
    assert alice.get(f"/api/v1/tweets/{tweet['id']}").status_code == 404
    assert alice.get(f"/api/v1/comments/stats?ids={comment['id']}").json() == []

    # no orphaned posts/likes/notifications remain
    from app.models.like import Like
    from app.models.notification import Notification
    from app.models.post import Post

    with TestingSessionLocal() as db:
        assert db.query(Post).count() == 0
        assert db.query(Like).count() == 0
        # notifications about the deleted posts are cleaned up
        assert db.query(Notification).filter(Notification.post_id.isnot(None)).count() == 0


def test_delete_tweet_rejects_non_author() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    register(alice, "alice")
    register(bob, "bob")

    tweet = alice.post("/api/v1/tweets", json={"content": "mine"}).json()
    assert bob.delete(f"/api/v1/tweets/{tweet['id']}").status_code == 403
    assert alice.get(f"/api/v1/tweets/{tweet['id']}").status_code == 200


def test_edit_and_delete_comment() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    tweet = alice.post("/api/v1/tweets", json={"content": "root"}).json()
    comment = alice.post(
        f"/api/v1/tweets/{tweet['id']}/comments", json={"content": "first"}
    ).json()

    edited = alice.patch(
        f"/api/v1/comments/{comment['id']}", json={"content": "edited comment"}
    )
    assert edited.status_code == 200
    assert edited.json()["content"] == "edited comment"
    assert edited.json()["edited_at"] is not None

    deleted = alice.delete(f"/api/v1/comments/{comment['id']}")
    assert deleted.status_code == 204

    # the comment is gone from the thread, the root tweet remains
    listed = alice.get(f"/api/v1/tweets/{tweet['id']}/comments").json()
    assert listed == []
    assert alice.get(f"/api/v1/tweets/{tweet['id']}").status_code == 200


def test_comment_endpoints_reject_a_tweet_id() -> None:
    client = TestClient(app)
    register(client, "alice")
    tweet = client.post("/api/v1/tweets", json={"content": "root"}).json()
    # editing/deleting a top-level tweet via /comments is a 404
    assert (
        client.patch(
            f"/api/v1/comments/{tweet['id']}", json={"content": "x"}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/comments/{tweet['id']}").status_code == 404
    # and it must still exist
    assert client.get(f"/api/v1/tweets/{tweet['id']}").status_code == 200
