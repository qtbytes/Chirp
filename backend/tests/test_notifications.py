from fastapi.testclient import TestClient
from main import app


def _register(username: str) -> tuple[TestClient, int]:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": "password123"},
    )
    assert response.status_code == 201
    return client, response.json()["id"]


def test_engagement_generates_notifications() -> None:
    alice, alice_id = _register("alice")
    bob, _ = _register("bob")

    # bob follows alice
    assert bob.post(f"/api/v1/follows/{alice_id}").status_code in (200, 201, 204)

    # alice posts a tweet
    tweet = alice.post("/api/v1/tweets", json={"content": "hello world"})
    tweet_id = tweet.json()["id"]

    # bob likes, comments on, and quotes (retweets) alice's tweet
    assert bob.post(f"/api/v1/tweets/{tweet_id}/likes/toggle").status_code == 200
    assert (
        bob.post(f"/api/v1/tweets/{tweet_id}/comments", json={"content": "nice"}).status_code
        == 201
    )
    assert (
        bob.post(
            "/api/v1/tweets",
            json={"content": "worth a look", "quoted_post_id": tweet_id},
        ).status_code
        == 201
    )

    # alice sees notifications for all four actions, from bob
    unread = alice.get("/api/v1/notifications/unread-count")
    assert unread.status_code == 200
    assert unread.json()["count"] == 4

    listed = alice.get("/api/v1/notifications")
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 4
    assert {item["type"] for item in items} == {"follow", "like", "comment", "retweet"}
    assert all(item["actor"]["username"] == "bob" for item in items)
    # tweet-related notifications carry a content preview
    like_item = next(item for item in items if item["type"] == "like")
    assert like_item["preview"] == "hello world"
    assert like_item["tweet_id"] == tweet_id


def test_self_actions_do_not_notify() -> None:
    alice, _ = _register("alice")
    tweet = alice.post("/api/v1/tweets", json={"content": "mine"})
    tweet_id = tweet.json()["id"]

    # alice likes and comments on her own tweet
    alice.post(f"/api/v1/tweets/{tweet_id}/likes/toggle")
    alice.post(f"/api/v1/tweets/{tweet_id}/comments", json={"content": "self"})

    assert alice.get("/api/v1/notifications/unread-count").json()["count"] == 0
    assert alice.get("/api/v1/notifications").json() == []


def test_reply_notifies_parent_comment_author() -> None:
    alice, alice_id = _register("alice")
    bob, _ = _register("bob")

    tweet = alice.post("/api/v1/tweets", json={"content": "root"})
    tweet_id = tweet.json()["id"]
    # alice comments on her own tweet (no self-notification)
    comment = alice.post(f"/api/v1/tweets/{tweet_id}/comments", json={"content": "c1"})
    comment_id = comment.json()["id"]

    # bob replies to alice's comment
    reply = bob.post(f"/api/v1/comments/{comment_id}/comments", json={"content": "r1"})
    assert reply.status_code == 201
    reply_id = reply.json()["id"]

    items = alice.get("/api/v1/notifications").json()
    assert len(items) == 1
    assert items[0]["type"] == "reply"
    # the notification references the reply itself, so its preview is what bob said
    assert items[0]["comment_id"] == reply_id
    assert items[0]["preview"] == "r1"


def test_mark_read_clears_unread_count() -> None:
    alice, alice_id = _register("alice")
    bob, _ = _register("bob")
    bob.post(f"/api/v1/follows/{alice_id}")

    assert alice.get("/api/v1/notifications/unread-count").json()["count"] == 1

    marked = alice.post("/api/v1/notifications/mark-read")
    assert marked.status_code == 200
    assert marked.json()["updated"] == 1

    assert alice.get("/api/v1/notifications/unread-count").json()["count"] == 0
    # the notification is still listed, just read
    items = alice.get("/api/v1/notifications").json()
    assert len(items) == 1
    assert items[0]["is_read"] is True
