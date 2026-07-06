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
