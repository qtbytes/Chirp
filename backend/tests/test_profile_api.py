from pathlib import Path

from app.core.config import settings
from fastapi.testclient import TestClient
from main import app


def register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": "password123"},
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
    assert body["post_count"] == 1
    assert body["is_following"] is True
    assert body["is_current_user"] is False

    own = bob_client.get("/api/v1/users/bob/profile").json()
    assert own["is_current_user"] is True
    assert own["is_following"] is False


def test_post_count_includes_replies_and_retweets() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    bob_tweet = bob.post("/api/v1/tweets", json={"content": "bob original"}).json()

    alice.post("/api/v1/tweets", json={"content": "own tweet"})
    alice.post(f"/api/v1/tweets/{bob_tweet['id']}/comments", json={"content": "a reply"})
    # A bare retweet is a quote post, so it counts as a post of alice's.
    alice.post("/api/v1/tweets", json={"quoted_post_id": bob_tweet["id"]})

    profile = alice.get("/api/v1/users/alice/profile").json()
    assert profile["post_count"] == 3


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


def test_profile_feed_includes_quotes() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    # bob posts a tweet, alice posts her own, then alice quotes bob's
    bob_tweet = bob.post("/api/v1/tweets", json={"content": "bob original"}).json()
    alice.post("/api/v1/tweets", json={"content": "alice own"})
    assert (
        alice.post(
            "/api/v1/tweets",
            json={"content": "quoting bob", "quoted_post_id": bob_tweet["id"]},
        ).status_code
        == 201
    )

    items = alice.get("/api/v1/users/alice/tweets").json()["items"]
    # the quote is a post by alice; newest first, embedding bob's tweet
    assert len(items) == 2
    quote_item = items[0]
    assert quote_item["content"] == "quoting bob"
    assert quote_item["author"]["username"] == "alice"
    assert quote_item["quoted_post"]["content"] == "bob original"
    assert quote_item["quoted_post"]["author"]["username"] == "bob"
    # alice's own plain tweet embeds nothing
    own_item = items[1]
    assert own_item["content"] == "alice own"
    assert own_item["quoted_post"] is None


def test_home_timeline_shows_followee_quotes() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    bob_id = register(bob, "bob")["id"]
    carol = TestClient(app)
    carol_id = register(carol, "carol")["id"]

    # alice follows both bob and carol
    alice.post(f"/api/v1/follows/{bob_id}")
    alice.post(f"/api/v1/follows/{carol_id}")

    # carol posts, then bob quotes it
    carol_tweet = carol.post("/api/v1/tweets", json={"content": "carol says hi"}).json()
    quote = bob.post(
        "/api/v1/tweets",
        json={"content": "bob quotes carol", "quoted_post_id": carol_tweet["id"]},
    ).json()

    items = alice.get("/api/v1/timeline/home").json()["items"]
    ids = [item["id"] for item in items]
    # both carol's original and bob's quote of it are their own posts
    assert carol_tweet["id"] in ids
    assert quote["id"] in ids
    quote_item = next(item for item in items if item["id"] == quote["id"])
    assert quote_item["author"]["username"] == "bob"
    assert quote_item["quoted_post"]["id"] == carol_tweet["id"]
    assert quote_item["quoted_post"]["author"]["username"] == "carol"


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


def test_quote_of_comment_embeds_comment_as_tweet() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    # bob posts a tweet and comments on it
    tweet = bob.post("/api/v1/tweets", json={"content": "root"}).json()
    comment = bob.post(
        f"/api/v1/tweets/{tweet['id']}/comments", json={"content": "bob's reply"}
    ).json()

    # alice quotes bob's comment, producing a top-level tweet that embeds it
    quote = alice.post(
        "/api/v1/tweets",
        json={"content": "quoting a comment", "quoted_post_id": comment["id"]},
    )
    assert quote.status_code == 201

    items = alice.get("/api/v1/users/alice/tweets").json()["items"]
    assert len(items) == 1
    entry = items[0]
    assert entry["content"] == "quoting a comment"
    assert entry["quoted_post"]["content"] == "bob's reply"
    assert entry["quoted_post"]["author"]["username"] == "bob"

    # a quote is not a reply, so alice's replies feed stays empty
    assert alice.get("/api/v1/users/alice/replies").json()["items"] == []


def test_user_replies_parent_is_immediate_not_root() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    # bob posts a tweet, then a top-level comment on it
    tweet = bob.post("/api/v1/tweets", json={"content": "root tweet"}).json()
    parent_comment = bob.post(
        f"/api/v1/tweets/{tweet['id']}/comments", json={"content": "parent comment"}
    ).json()

    # alice replies to bob's comment (a nested reply, not on the root tweet)
    alice.post(
        f"/api/v1/comments/{parent_comment['id']}/comments",
        json={"content": "my nested reply"},
    )

    items = alice.get("/api/v1/users/alice/replies").json()["items"]
    assert len(items) == 1
    assert items[0]["comment"]["content"] == "my nested reply"
    # the parent is the immediate parent comment, not the thread root tweet
    assert items[0]["parent_tweet"]["content"] == "parent comment"
    assert items[0]["parent_tweet"]["author"]["username"] == "bob"


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
    from app.models.post import Post
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
        db.query(Post).filter(Post.id == tweet["id"]).delete()
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


def test_display_name_defaults_to_none_and_round_trips() -> None:
    client = TestClient(app)
    register(client, "alice")

    profile = client.get("/api/v1/users/alice/profile").json()
    assert profile["display_name"] is None

    response = client.patch("/api/v1/users/me", json={"display_name": "Alice L."})
    assert response.status_code == 200
    assert response.json()["display_name"] == "Alice L."

    # surfaced on the profile and in the discovery list
    assert client.get("/api/v1/users/alice/profile").json()["display_name"] == "Alice L."
    users = {u["username"]: u for u in client.get("/api/v1/users").json()}
    assert users["alice"]["display_name"] == "Alice L."


def test_updating_display_name_leaves_bio_untouched() -> None:
    client = TestClient(app)
    register(client, "alice")

    client.patch("/api/v1/users/me", json={"bio": "Building things."})
    # a partial update that only sends display_name must not wipe the bio
    client.patch("/api/v1/users/me", json={"display_name": "Alice"})

    profile = client.get("/api/v1/users/alice/profile").json()
    assert profile["display_name"] == "Alice"
    assert profile["bio"] == "Building things."


def test_blank_display_name_clears_back_to_username_fallback() -> None:
    client = TestClient(app)
    register(client, "alice")

    client.patch("/api/v1/users/me", json={"display_name": "Alice"})
    cleared = client.patch("/api/v1/users/me", json={"display_name": "   "})
    assert cleared.status_code == 200
    assert cleared.json()["display_name"] is None


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


# A syntactically valid media URL; regex-validated only, no upload needed.
MEDIA_URL_A = "/uploads/media/" + "a" * 32 + ".png"
MEDIA_URL_B = "/uploads/media/" + "b" * 32 + ".png"


def test_user_media_lists_tweets_and_replies_with_media() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    bob_tweet = bob.post(
        "/api/v1/tweets", json={"content": "bob pic", "media_urls": [MEDIA_URL_A]}
    ).json()

    alice.post("/api/v1/tweets", json={"content": "text only"})
    media_tweet = alice.post(
        "/api/v1/tweets", json={"content": "my photo", "media_urls": [MEDIA_URL_A]}
    ).json()
    media_reply = alice.post(
        f"/api/v1/tweets/{bob_tweet['id']}/comments",
        json={"content": "reply pic", "media_urls": [MEDIA_URL_B]},
    ).json()
    # A bare retweet of bob's media tweet carries no media of its own, so the
    # media tab must not show it.
    assert (
        alice.post(
            "/api/v1/tweets", json={"quoted_post_id": bob_tweet["id"]}
        ).status_code
        == 201
    )

    items = alice.get("/api/v1/users/alice/media").json()["items"]
    assert [(item["id"], item["is_reply"]) for item in items] == [
        (media_reply["id"], True),
        (media_tweet["id"], False),
    ]
    reply_item = items[0]
    assert reply_item["thread_id"] == bob_tweet["id"]
    assert reply_item["parent_comment_id"] is None
    assert reply_item["media_urls"] == [MEDIA_URL_B]
    assert reply_item["author"]["username"] == "alice"


def test_user_media_pagination() -> None:
    client = TestClient(app)
    register(client, "alice")
    ids = [
        client.post(
            "/api/v1/tweets",
            json={"content": f"media {index}", "media_urls": [MEDIA_URL_A]},
        ).json()["id"]
        for index in range(3)
    ]

    page = client.get("/api/v1/users/alice/media", params={"limit": 2}).json()
    assert [item["id"] for item in page["items"]] == [ids[2], ids[1]]
    assert page["next_cursor"]

    page2 = client.get(
        "/api/v1/users/alice/media",
        params={"limit": 2, "cursor": page["next_cursor"]},
    ).json()
    assert [item["id"] for item in page2["items"]] == [ids[0]]
    assert page2["next_cursor"] is None


def test_user_media_error_paths_and_blocks() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    bob_id = register(bob, "bob")["id"]

    assert alice.get("/api/v1/users/ghost/media").status_code == 404
    assert (
        alice.get("/api/v1/users/bob/media", params={"cursor": "junk"}).status_code
        == 400
    )

    bob.post("/api/v1/tweets", json={"content": "pic", "media_urls": [MEDIA_URL_A]})
    assert alice.get("/api/v1/users/bob/media").json()["items"]

    alice.post(f"/api/v1/blocks/{bob_id}")
    assert alice.get("/api/v1/users/bob/media").json()["items"] == []


def test_user_media_hides_replies_into_hidden_threads() -> None:
    bob = TestClient(app)
    bob_id = register(bob, "bob")["id"]
    carol = TestClient(app)
    register(carol, "carol")
    alice = TestClient(app)
    register(alice, "alice")

    followers_tweet = bob.post(
        "/api/v1/tweets",
        json={"content": "followers only", "visibility": "followers"},
    ).json()
    carol.post(f"/api/v1/follows/{bob_id}")
    carol.post(
        f"/api/v1/tweets/{followers_tweet['id']}/comments",
        json={"content": "sneak pic", "media_urls": [MEDIA_URL_A]},
    )

    # bob follows the thread root he owns; the media reply is visible to him.
    assert bob.get("/api/v1/users/carol/media").json()["items"]
    # alice does not follow bob, so the reply's thread is hidden from her.
    assert alice.get("/api/v1/users/carol/media").json()["items"] == []


def test_user_likes_lists_liked_tweets_and_replies_by_like_time() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    tweet1 = bob.post("/api/v1/tweets", json={"content": "first"}).json()
    tweet2 = bob.post("/api/v1/tweets", json={"content": "second"}).json()
    reply = bob.post(
        f"/api/v1/tweets/{tweet1['id']}/comments", json={"content": "bob replies"}
    ).json()

    # Liked in this order: tweet2, then the reply, then tweet1 -- the feed
    # must come back most-recently-liked first, not by post age.
    alice.post(f"/api/v1/tweets/{tweet2['id']}/likes")
    alice.post(f"/api/v1/comments/{reply['id']}/likes")
    alice.post(f"/api/v1/tweets/{tweet1['id']}/likes")

    items = alice.get("/api/v1/users/alice/likes").json()["items"]
    assert [(item["id"], item["is_reply"]) for item in items] == [
        (tweet1["id"], False),
        (reply["id"], True),
        (tweet2["id"], False),
    ]
    reply_item = items[1]
    assert reply_item["thread_id"] == tweet1["id"]
    assert reply_item["author"]["username"] == "bob"
    assert all(item["liked_by_me"] for item in items)


def test_user_likes_are_private() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    tweet = bob.post("/api/v1/tweets", json={"content": "hello"}).json()
    alice.post(f"/api/v1/tweets/{tweet['id']}/likes")

    assert bob.get("/api/v1/users/alice/likes").status_code == 403
    assert alice.get("/api/v1/users/ghost/likes").status_code == 404
    assert (
        alice.get("/api/v1/users/alice/likes", params={"cursor": "junk"}).status_code
        == 400
    )


def test_user_likes_pagination() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    register(bob, "bob")

    ids = [
        bob.post("/api/v1/tweets", json={"content": f"tweet {index}"}).json()["id"]
        for index in range(3)
    ]
    for tweet_id in ids:
        alice.post(f"/api/v1/tweets/{tweet_id}/likes")

    page = alice.get("/api/v1/users/alice/likes", params={"limit": 2}).json()
    assert [item["id"] for item in page["items"]] == [ids[2], ids[1]]
    assert page["next_cursor"]

    page2 = alice.get(
        "/api/v1/users/alice/likes",
        params={"limit": 2, "cursor": page["next_cursor"]},
    ).json()
    assert [item["id"] for item in page2["items"]] == [ids[0]]
    assert page2["next_cursor"] is None


def test_user_likes_hide_blocked_and_invisible_posts() -> None:
    alice = TestClient(app)
    register(alice, "alice")
    bob = TestClient(app)
    bob_id = register(bob, "bob")["id"]
    carol = TestClient(app)
    carol_id = register(carol, "carol")["id"]

    bob_tweet = bob.post("/api/v1/tweets", json={"content": "bob post"}).json()
    alice.post(f"/api/v1/tweets/{bob_tweet['id']}/likes")

    # A followers-only tweet alice can see while following; unfollowing later
    # must drop it from her likes rather than leak it.
    alice.post(f"/api/v1/follows/{carol_id}")
    carol_tweet = carol.post(
        "/api/v1/tweets",
        json={"content": "carol followers only", "visibility": "followers"},
    ).json()
    alice.post(f"/api/v1/tweets/{carol_tweet['id']}/likes")

    items = alice.get("/api/v1/users/alice/likes").json()["items"]
    assert [item["id"] for item in items] == [carol_tweet["id"], bob_tweet["id"]]

    alice.delete(f"/api/v1/follows/{carol_id}")
    alice.post(f"/api/v1/blocks/{bob_id}")

    assert alice.get("/api/v1/users/alice/likes").json()["items"] == []
