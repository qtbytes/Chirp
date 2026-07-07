from pathlib import Path

from app.core.config import settings
from fastapi.testclient import TestClient
from main import app


def register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 201
    return response.json()


def test_user_summary_includes_avatar_url() -> None:
    client = TestClient(app)
    body = register(client, "alice")
    assert body["avatar_url"] is None


def test_profile_counts_and_follow_state() -> None:
    alice_client = TestClient(app)
    bob_client = TestClient(app)
    register(alice_client, "alice")
    bob = register(bob_client, "bob")

    bob_client.post("/api/v1/tweets", json={"content": "hello"})
    alice_client.post(f"/api/v1/follows/{bob['id']}")

    response = alice_client.get("/api/v1/users/bob/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == bob["id"]
    assert body["username"] == "bob"
    assert body["bio"] is None
    assert body["follower_count"] == 1
    assert body["following_count"] == 0
    assert body["tweet_count"] == 1
    assert body["is_following"] is True
    assert body["is_current_user"] is False

    own = bob_client.get("/api/v1/users/bob/profile").json()
    assert own["is_current_user"] is True
    assert own["is_following"] is False


def test_profile_unknown_username_returns_404() -> None:
    client = TestClient(app)
    register(client, "alice")
    assert client.get("/api/v1/users/ghost/profile").status_code == 404


def test_get_single_tweet_with_stats() -> None:
    client = TestClient(app)
    register(client, "alice")
    tweet = client.post("/api/v1/tweets", json={"content": "hello"}).json()
    client.post(f"/api/v1/tweets/{tweet['id']}/likes/toggle")

    response = client.get(f"/api/v1/tweets/{tweet['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "hello"
    assert body["author"]["username"] == "alice"
    assert body["like_count"] == 1
    assert body["liked_by_me"] is True

    assert client.get("/api/v1/tweets/999999").status_code == 404


def test_user_tweets_newest_first_with_cursor() -> None:
    client = TestClient(app)
    register(client, "alice")
    for index in range(3):
        client.post("/api/v1/tweets", json={"content": f"tweet {index}"})

    page = client.get("/api/v1/users/alice/tweets", params={"limit": 2}).json()
    assert [item["content"] for item in page["items"]] == ["tweet 2", "tweet 1"]
    assert page["next_cursor"]

    page2 = client.get(
        "/api/v1/users/alice/tweets",
        params={"limit": 2, "cursor": page["next_cursor"]},
    ).json()
    assert [item["content"] for item in page2["items"]] == ["tweet 0"]
    assert page2["next_cursor"] is None


def test_profile_feed_includes_retweets() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    # bob posts a tweet, alice posts her own, then alice retweets bob's
    bob_tweet = bob.post("/api/v1/tweets", json={"content": "bob original"}).json()
    alice.post("/api/v1/tweets", json={"content": "alice own"})
    assert alice.post(f"/api/v1/tweets/{bob_tweet['id']}/retweets").status_code == 201

    items = alice.get("/api/v1/users/alice/tweets").json()["items"]
    # the retweet is newest, so it surfaces first, marked as retweeted by alice
    assert len(items) == 2
    retweet_item = items[0]
    assert retweet_item["content"] == "bob original"
    assert retweet_item["author"]["username"] == "bob"
    assert retweet_item["retweeted_by"]["username"] == "alice"
    # alice's own tweet has no retweet marker
    own_item = items[1]
    assert own_item["content"] == "alice own"
    assert own_item["retweeted_by"] is None


def test_home_timeline_shows_followee_retweets_once() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    bob_id = register(bob, "bob")["id"]
    carol = TestClient(app)
    carol_id = register(carol, "carol")["id"]

    # alice follows both bob and carol
    alice.post(f"/api/v1/follows/{bob_id}")
    alice.post(f"/api/v1/follows/{carol_id}")

    # carol posts, then bob retweets it
    carol_tweet = carol.post("/api/v1/tweets", json={"content": "carol says hi"}).json()
    assert bob.post(f"/api/v1/tweets/{carol_tweet['id']}/retweets").status_code == 201

    items = alice.get("/api/v1/timeline/home").json()["items"]
    # even though alice follows both the author and the retweeter, the tweet
    # appears once — surfaced as bob's retweet (the most recent activity)
    matching = [item for item in items if item["id"] == carol_tweet["id"]]
    assert len(matching) == 1
    assert matching[0]["retweeted_by"]["username"] == "bob"
    assert matching[0]["author"]["username"] == "carol"


def test_user_tweets_error_paths() -> None:
    client = TestClient(app)
    register(client, "alice")

    assert client.get("/api/v1/users/ghost/tweets").status_code == 404

    invalid = client.get(
        "/api/v1/users/alice/tweets",
        params={"cursor": "not-a-cursor"},
    )
    assert invalid.status_code == 400


def test_user_replies_include_parent_tweet() -> None:
    alice_client = TestClient(app)
    bob_client = TestClient(app)
    register(alice_client, "alice")
    register(bob_client, "bob")

    tweet = bob_client.post("/api/v1/tweets", json={"content": "original"}).json()
    alice_client.post(
        f"/api/v1/tweets/{tweet['id']}/comments",
        json={"content": "my reply"},
    )

    response = alice_client.get("/api/v1/users/alice/replies")
    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["comment"]["content"] == "my reply"
    assert item["comment"]["author"]["username"] == "alice"
    assert item["parent_tweet"]["content"] == "original"
    assert item["parent_tweet"]["author"]["username"] == "bob"
    assert item["parent_tweet"]["comment_count"] == 1


def test_replies_feed_includes_retweeted_comments() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    # bob posts a tweet and comments on it
    tweet = bob.post("/api/v1/tweets", json={"content": "root"}).json()
    comment = bob.post(
        f"/api/v1/tweets/{tweet['id']}/comments", json={"content": "bob's reply"}
    ).json()

    # alice retweets bob's comment
    assert alice.post(f"/api/v1/comments/{comment['id']}/retweets").status_code in (
        200,
        201,
        204,
    )

    items = alice.get("/api/v1/users/alice/replies").json()["items"]
    # the retweeted comment shows on alice's replies, authored by bob,
    # marked as retweeted by alice
    assert len(items) == 1
    entry = items[0]["comment"]
    assert entry["content"] == "bob's reply"
    assert entry["author"]["username"] == "bob"
    assert entry["retweeted_by"]["username"] == "alice"


def test_user_replies_error_paths() -> None:
    client = TestClient(app)
    register(client, "alice")

    assert client.get("/api/v1/users/ghost/replies").status_code == 404

    invalid = client.get(
        "/api/v1/users/alice/replies",
        params={"cursor": "not-a-cursor"},
    )
    assert invalid.status_code == 400


def test_user_replies_pagination() -> None:
    alice_client = TestClient(app)
    bob_client = TestClient(app)
    register(alice_client, "alice")
    register(bob_client, "bob")

    tweet = bob_client.post("/api/v1/tweets", json={"content": "original"}).json()
    for index in range(3):
        alice_client.post(
            f"/api/v1/tweets/{tweet['id']}/comments",
            json={"content": f"reply {index}"},
        )

    page = alice_client.get(
        "/api/v1/users/alice/replies", params={"limit": 2}
    ).json()
    assert [item["comment"]["content"] for item in page["items"]] == [
        "reply 2",
        "reply 1",
    ]
    assert page["next_cursor"]

    page2 = alice_client.get(
        "/api/v1/users/alice/replies",
        params={"limit": 2, "cursor": page["next_cursor"]},
    ).json()
    assert [item["comment"]["content"] for item in page2["items"]] == ["reply 0"]
    assert page2["next_cursor"] is None


def test_user_replies_skips_comments_on_deleted_tweet() -> None:
    from app.models.tweet import Tweet
    from conftest import TestingSessionLocal

    alice_client = TestClient(app)
    bob_client = TestClient(app)
    register(alice_client, "alice")
    register(bob_client, "bob")

    tweet = bob_client.post("/api/v1/tweets", json={"content": "will be deleted"}).json()
    alice_client.post(
        f"/api/v1/tweets/{tweet['id']}/comments",
        json={"content": "orphaned reply"},
    )

    db = TestingSessionLocal()
    try:
        db.query(Tweet).filter(Tweet.id == tweet["id"]).delete()
        db.commit()
    finally:
        db.close()

    response = alice_client.get("/api/v1/users/alice/replies")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_update_bio() -> None:
    client = TestClient(app)
    register(client, "alice")

    response = client.patch("/api/v1/users/me", json={"bio": "Building things."})
    assert response.status_code == 200
    assert response.json()["bio"] == "Building things."

    profile = client.get("/api/v1/users/alice/profile").json()
    assert profile["bio"] == "Building things."


def test_update_bio_rejects_too_long() -> None:
    client = TestClient(app)
    register(client, "alice")
    response = client.patch("/api/v1/users/me", json={"bio": "x" * 161})
    assert response.status_code == 422


def test_avatar_upload_updates_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    client = TestClient(app)
    register(client, "alice")

    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("me.png", b"fake-png-bytes", "image/png")},
    )
    assert response.status_code == 200
    avatar_url = response.json()["avatar_url"]
    assert avatar_url.startswith("/uploads/avatars/")
    assert (tmp_path / "avatars").exists()

    me = client.get("/api/v1/auth/me").json()
    assert me["avatar_url"] == avatar_url


def test_avatar_upload_rejects_bad_type_and_size(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    client = TestClient(app)
    register(client, "alice")

    bad_type = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert bad_type.status_code == 415

    too_big = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("big.png", b"x" * (2 * 1024 * 1024 + 1), "image/png")},
    )
    assert too_big.status_code == 413


def test_avatar_is_served_from_uploads_mount() -> None:
    client = TestClient(app)
    register(client, "alice")

    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("me.png", b"fake-png-bytes", "image/png")},
    )
    assert response.status_code == 200
    avatar_url = response.json()["avatar_url"]

    served = client.get(avatar_url)
    assert served.status_code == 200
    assert served.content == b"fake-png-bytes"

    avatar_path = avatar_url.split("?", maxsplit=1)[0]
    saved_file = Path(settings.uploads_dir) / avatar_path.removeprefix("/uploads/")
    saved_file.unlink(missing_ok=True)
