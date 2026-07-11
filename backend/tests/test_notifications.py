from app.services import events
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
    items = listed.json()["items"]
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
    assert alice.get("/api/v1/notifications").json()["items"] == []


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

    items = alice.get("/api/v1/notifications").json()["items"]
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
    items = alice.get("/api/v1/notifications").json()["items"]
    assert len(items) == 1
    assert items[0]["is_read"] is True


def test_notifications_paginate_newest_first_without_gaps() -> None:
    alice, alice_id = _register("alice")

    # Five distinct actors follow alice, so she gets five notifications.
    for name in ("bob1", "bob2", "bob3", "bob4", "bob5"):
        follower, _ = _register(name)
        follower.post(f"/api/v1/follows/{alice_id}")

    seen: list[int] = []
    cursor: str | None = None
    for _ in range(10):  # guard against a non-terminating cursor
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = alice.get("/api/v1/notifications", params=params).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert len(seen) == 5
    assert len(set(seen)) == 5, "no notification appears on two pages"
    assert seen == sorted(seen, reverse=True), "newest (highest id) first"


def test_invalid_notifications_cursor_is_rejected() -> None:
    alice, _ = _register("alice")
    assert (
        alice.get("/api/v1/notifications", params={"cursor": "nonsense"}).status_code
        == 400
    )


def test_mark_single_notification_read() -> None:
    alice, alice_id = _register("alice")
    bee1, _ = _register("bee1")
    bee2, _ = _register("bee2")
    bee1.post(f"/api/v1/follows/{alice_id}")
    bee2.post(f"/api/v1/follows/{alice_id}")

    assert alice.get("/api/v1/notifications/unread-count").json()["count"] == 2

    newest_id = alice.get("/api/v1/notifications").json()["items"][0]["id"]
    assert alice.post(f"/api/v1/notifications/{newest_id}/read").status_code == 204

    # Exactly one is now read; the count drops by one, not to zero.
    assert alice.get("/api/v1/notifications/unread-count").json()["count"] == 1
    items = alice.get("/api/v1/notifications").json()["items"]
    read = {item["id"]: item["is_read"] for item in items}
    assert read[newest_id] is True
    assert sum(1 for v in read.values() if v) == 1


def test_cannot_mark_someone_elses_notification_read() -> None:
    alice, alice_id = _register("alice")
    bob, bob_id = _register("bob")
    carol, _ = _register("carol")

    # carol follows both, so each of alice and bob has a notification.
    carol.post(f"/api/v1/follows/{alice_id}")
    carol.post(f"/api/v1/follows/{bob_id}")

    bob_notification_id = bob.get("/api/v1/notifications").json()["items"][0]["id"]

    # alice cannot read bob's notification: it is not hers, so it is a 404.
    assert alice.post(f"/api/v1/notifications/{bob_notification_id}/read").status_code == 404
    # bob's notification is still unread.
    assert bob.get("/api/v1/notifications/unread-count").json()["count"] == 1
    # a missing id is also a 404.
    assert alice.post("/api/v1/notifications/999999/read").status_code == 404


class _RecordingRedis:
    """Captures publishes so a test can assert a nudge was sent post-commit."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


def test_a_notification_publishes_a_nudge_after_commit(monkeypatch) -> None:
    fake = _RecordingRedis()
    monkeypatch.setattr(events, "get_redis_client", lambda: fake)

    alice, alice_id = _register("alice")
    bob, _ = _register("bob")

    # The follow commits, so the after-commit hook should publish exactly one
    # nudge to alice's channel -- and nothing to bob (self is never notified).
    bob.post(f"/api/v1/follows/{alice_id}")

    assert fake.published, "a committed notification must publish a nudge"
    channels = {channel for channel, _ in fake.published}
    assert channels == {f"events:user:{alice_id}"}


def test_a_rolled_back_transaction_publishes_nothing(monkeypatch) -> None:
    # Queue a pending nudge on a session, then roll back: the after-rollback hook
    # must drop it so a later commit on the same session does not fire it. The
    # fake would record a publish if the drop failed.
    from conftest import TestingSessionLocal

    from app.models.user import User

    fake = _RecordingRedis()
    monkeypatch.setattr(events, "get_redis_client", lambda: fake)

    with TestingSessionLocal() as db:
        # A real staged change gives the session a transaction to roll back, so
        # the after-rollback hook actually fires and clears the pending nudge.
        db.add(User(username="rolled_back", password_hash="x"))
        events.queue_user_event(db, 42)
        db.rollback()
        db.commit()  # nothing pending -> nothing published

    assert fake.published == []


def test_stream_requires_redis(monkeypatch) -> None:
    alice, _ = _register("alice")
    from app.api.routes import notifications as notifications_route

    monkeypatch.setattr(notifications_route, "get_redis_client", lambda: None)
    assert alice.get("/api/v1/notifications/stream").status_code == 503


def test_stream_emits_a_frame_when_an_event_is_published(monkeypatch) -> None:
    """The async SSE generator forwards a published nudge as a `notification` frame."""
    import asyncio

    class _FakePubSub:
        def __init__(self) -> None:
            # One empty poll, then a nudge -- exercises both branches.
            self._queue = [None, {"data": b'{"type":"notification"}'}]

        def subscribe(self, channel: str) -> None:
            pass

        def get_message(self, ignore_subscribe_messages=False, timeout=0):
            return self._queue.pop(0) if self._queue else None

        def close(self) -> None:
            pass

    class _FakeRedis:
        def pubsub(self) -> _FakePubSub:
            return _FakePubSub()

    monkeypatch.setattr(events, "get_redis_client", lambda: _FakeRedis())

    async def collect() -> list[str]:
        frames: list[str] = []
        async for frame in events.stream_user_events(7):
            frames.append(frame)
            if any("event: notification" in f for f in frames):
                break
        return frames

    frames = asyncio.run(collect())
    assert frames[0].startswith(": connected")
    assert any(
        "event: notification" in f and '"type":"notification"' in f for f in frames
    )
