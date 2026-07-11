"""
Blocking. A block hides two users from each other everywhere -- timeline, for
you, profile, thread, notifications, discovery -- and forbids following or
interacting in either direction.
"""

from fastapi.testclient import TestClient
from main import app


def register(username: str) -> tuple[TestClient, int]:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201, response.text
    return client, response.json()["id"]


def _post(client: TestClient, content: str) -> int:
    return client.post("/api/v1/tweets", json={"content": content}).json()["id"]


def _for_you_ids(client: TestClient) -> list[int]:
    return [item["id"] for item in client.get("/api/v1/timeline/for-you").json()["items"]]


# --------------------------------------------------------------- the endpoint


def test_block_is_idempotent_and_rejects_self() -> None:
    alice, alice_id = register("alice")
    _, bob_id = register("bob")

    first = alice.post(f"/api/v1/blocks/{bob_id}")
    assert first.status_code == 200
    assert first.json() == {"blocked_id": bob_id, "is_blocked": True}
    assert alice.post(f"/api/v1/blocks/{bob_id}").status_code == 200  # idempotent

    assert alice.post(f"/api/v1/blocks/{alice_id}").status_code == 400
    assert alice.post("/api/v1/blocks/999999").status_code == 404


def test_blocking_severs_follows_both_ways_and_forbids_new_ones() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")

    alice.post(f"/api/v1/follows/{bob_id}")
    bob.post(f"/api/v1/follows/{alice_id}")

    alice.post(f"/api/v1/blocks/{bob_id}")

    # Both follow edges are gone.
    assert alice.get("/api/v1/users/alice/profile").json()["following_count"] == 0
    assert bob.get("/api/v1/users/bob/profile").json()["following_count"] == 0

    # And neither can re-follow while the block stands.
    assert alice.post(f"/api/v1/follows/{bob_id}").status_code == 400
    assert bob.post(f"/api/v1/follows/{alice_id}").status_code == 400

    # Unblocking lets a follow happen again (but does not restore the old one).
    assert alice.delete(f"/api/v1/blocks/{bob_id}").status_code == 200
    assert alice.post(f"/api/v1/follows/{bob_id}").status_code in (200, 201)


def test_profile_reports_is_blocked_for_the_blocker_only() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    alice.post(f"/api/v1/blocks/{bob_id}")

    assert alice.get("/api/v1/users/bob/profile").json()["is_blocked"] is True
    # Bob is not told he was blocked.
    assert bob.get("/api/v1/users/alice/profile").json()["is_blocked"] is False


# --------------------------------------------------------------- read hiding


def test_for_you_hides_blocked_users_in_both_directions() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    alice_tweet = _post(alice, "from alice")
    bob_tweet = _post(bob, "from bob")

    assert bob_tweet in _for_you_ids(alice), "precondition: alice sees bob"

    alice.post(f"/api/v1/blocks/{bob_id}")

    assert bob_tweet not in _for_you_ids(alice), "blocker no longer sees the blocked"
    assert alice_tweet not in _for_you_ids(bob), "blocked no longer sees the blocker"


def test_profile_tweets_and_replies_are_empty_across_a_block() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")

    tweet_id = _post(bob, "bob's tweet")
    # bob replies to his own tweet so he has a reply on his profile too.
    bob.post(f"/api/v1/tweets/{tweet_id}/comments", json={"content": "bob's reply"})

    alice.post(f"/api/v1/blocks/{bob_id}")

    # Blocker sees nothing of the blocked user's profile...
    assert alice.get("/api/v1/users/bob/tweets").json()["items"] == []
    assert alice.get("/api/v1/users/bob/replies").json()["items"] == []
    # ...and the blocked user sees nothing of the blocker's either.
    _post(alice, "alice's tweet")
    assert bob.get("/api/v1/users/alice/tweets").json()["items"] == []


def test_a_blocked_authors_tweet_is_not_viewable() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    tweet_id = _post(bob, "hi")

    assert alice.get(f"/api/v1/tweets/{tweet_id}").status_code == 200
    alice.post(f"/api/v1/blocks/{bob_id}")
    assert alice.get(f"/api/v1/tweets/{tweet_id}").status_code == 404


def test_thread_hides_a_blocked_authors_comment_and_its_subtree() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    carol, _ = register("carol")
    dave, _ = register("dave")

    tweet_id = _post(alice, "root")
    bob_comment = bob.post(
        f"/api/v1/tweets/{tweet_id}/comments", json={"content": "bob"}
    ).json()["id"]
    # carol replies under bob's comment; her reply lives in bob's subtree.
    carol.post(f"/api/v1/comments/{bob_comment}/comments", json={"content": "carol under bob"})
    # dave comments directly on the tweet -- outside bob's subtree.
    dave.post(f"/api/v1/tweets/{tweet_id}/comments", json={"content": "dave"})

    # Dave blocks bob, then reads the thread: bob's comment and carol's reply
    # beneath it both vanish; dave's own top-level comment remains.
    dave.post(f"/api/v1/blocks/{bob_id}")
    contents = [
        c["content"] for c in dave.get(f"/api/v1/tweets/{tweet_id}/comments").json()
    ]
    assert "bob" not in contents
    assert "carol under bob" not in contents, "subtree of a hidden comment is hidden too"
    assert "dave" in contents


def test_discovery_hides_blocked_users_both_ways() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    alice.post(f"/api/v1/blocks/{bob_id}")

    alice_sees = {u["username"] for u in alice.get("/api/v1/users").json()}
    bob_sees = {u["username"] for u in bob.get("/api/v1/users").json()}
    assert "bob" not in alice_sees
    assert "alice" not in bob_sees


def test_notifications_from_a_now_blocked_actor_are_hidden() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")

    bob.post(f"/api/v1/follows/{alice_id}")  # alice gets a follow notification
    assert len(alice.get("/api/v1/notifications").json()["items"]) == 1

    alice.post(f"/api/v1/blocks/{bob_id}")
    assert alice.get("/api/v1/notifications").json()["items"] == []


# --------------------------------------------------------------- write hiding


def test_interactions_are_forbidden_across_a_block() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")

    alice_tweet = _post(alice, "alice")
    bob_tweet = _post(bob, "bob")

    alice.post(f"/api/v1/blocks/{bob_id}")

    # Blocker cannot interact with the blocked user's post...
    assert alice.post(f"/api/v1/tweets/{bob_tweet}/likes/toggle").status_code == 404
    assert (
        alice.post(f"/api/v1/tweets/{bob_tweet}/comments", json={"content": "x"}).status_code
        == 404
    )
    assert (
        alice.post("/api/v1/tweets", json={"content": "q", "quoted_post_id": bob_tweet}).status_code
        == 404
    )
    # ...and the blocked user cannot interact with the blocker's post.
    assert bob.post(f"/api/v1/tweets/{alice_tweet}/likes/toggle").status_code == 404
    assert (
        bob.post(f"/api/v1/tweets/{alice_tweet}/comments", json={"content": "x"}).status_code
        == 404
    )


def test_write_timeline_excludes_blocked_author_from_precomputed_feed() -> None:
    from conftest import TestingSessionLocal
    from app.repositories import (
        block_repository,
        feed_repository,
        tweet_repository,
        user_repository,
    )

    with TestingSessionLocal() as db:
        alice = user_repository.create_user(db, username="alice", password_hash="x")
        bob = user_repository.create_user(db, username="bob", password_hash="x")
        tweet = tweet_repository.create_tweet(db, author_id=bob.id, content="hi")
        feed_repository.bulk_insert_feed_items(
            db,
            owner_ids=[alice.id],
            post_id=tweet.id,
            actor_id=bob.id,
            created_at=tweet.created_at,
        )

        before = feed_repository.list_feed_tweets(db, owner_id=alice.id, limit=10)
        assert any(row["tweet"].id == tweet.id for row in before)

        block_repository.block_user(db, blocker_id=alice.id, blocked_id=bob.id)
        hidden = block_repository.hidden_user_ids(db, alice.id)
        after = feed_repository.list_feed_tweets(
            db, owner_id=alice.id, limit=10, exclude_author_ids=hidden
        )
        assert all(row["tweet"].id != tweet.id for row in after), "stale feed row hidden"


# --------------------------------------------------------------- blocked list


def test_blocked_list_is_paginated_newest_first() -> None:
    alice, _ = register("alice")
    _, bob_id = register("bob")
    _, carol_id = register("carol")

    alice.post(f"/api/v1/blocks/{bob_id}")
    alice.post(f"/api/v1/blocks/{carol_id}")

    page = alice.get("/api/v1/blocks").json()
    usernames = [item["username"] for item in page["items"]]
    assert usernames == ["carol", "bob"], "most recently blocked first"
    assert all("blocked_at" in item for item in page["items"])
