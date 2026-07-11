"""
Muting. A mute is one-directional: it removes the muted user's content from the
muter's own read paths -- timeline, for you, thread, notifications, discovery --
and nothing more. It never severs a follow, never forbids interaction, and is
invisible to the muted user, who keeps seeing the muter normally.
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


def test_mute_is_idempotent_and_rejects_self() -> None:
    alice, alice_id = register("alice")
    _, bob_id = register("bob")

    first = alice.post(f"/api/v1/mutes/{bob_id}")
    assert first.status_code == 200
    assert first.json() == {"muted_id": bob_id, "is_muted": True}
    assert alice.post(f"/api/v1/mutes/{bob_id}").status_code == 200  # idempotent

    assert alice.post(f"/api/v1/mutes/{alice_id}").status_code == 400
    assert alice.post("/api/v1/mutes/999999").status_code == 404


def test_muting_keeps_follows_and_allows_interaction() -> None:
    """The whole point of mute over block: relationships stay intact."""
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")

    alice.post(f"/api/v1/follows/{bob_id}")
    bob.post(f"/api/v1/follows/{alice_id}")
    bob_tweet = _post(bob, "from bob")

    alice.post(f"/api/v1/mutes/{bob_id}")

    # Both follow edges survive the mute.
    assert alice.get("/api/v1/users/alice/profile").json()["following_count"] == 1
    assert bob.get("/api/v1/users/bob/profile").json()["following_count"] == 1

    # And the muter can still interact with the muted user's posts.
    assert alice.post(f"/api/v1/tweets/{bob_tweet}/likes/toggle").status_code == 200
    assert (
        alice.post(
            f"/api/v1/tweets/{bob_tweet}/comments", json={"content": "still here"}
        ).status_code
        == 201
    )


def test_profile_reports_is_muted_for_the_muter_only() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    alice.post(f"/api/v1/mutes/{bob_id}")

    assert alice.get("/api/v1/users/bob/profile").json()["is_muted"] is True
    # Bob is not told he was muted.
    assert bob.get("/api/v1/users/alice/profile").json()["is_muted"] is False


# --------------------------------------------------------------- read hiding


def test_for_you_hides_a_muted_user_one_directionally() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    alice_tweet = _post(alice, "from alice")
    bob_tweet = _post(bob, "from bob")

    assert bob_tweet in _for_you_ids(alice), "precondition: alice sees bob"

    alice.post(f"/api/v1/mutes/{bob_id}")

    assert bob_tweet not in _for_you_ids(alice), "muter no longer sees the muted"
    assert alice_tweet in _for_you_ids(bob), "muted user still sees the muter"


def test_thread_hides_a_muted_authors_comment() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")

    tweet_id = _post(alice, "root")
    bob.post(f"/api/v1/tweets/{tweet_id}/comments", json={"content": "bob"})

    alice.post(f"/api/v1/mutes/{bob_id}")
    contents = [
        c["content"] for c in alice.get(f"/api/v1/tweets/{tweet_id}/comments").json()
    ]
    assert "bob" not in contents


def test_discovery_hides_a_muted_user_one_directionally() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    alice.post(f"/api/v1/mutes/{bob_id}")

    alice_sees = {u["username"] for u in alice.get("/api/v1/users").json()}
    bob_sees = {u["username"] for u in bob.get("/api/v1/users").json()}
    assert "bob" not in alice_sees, "muter no longer discovers the muted"
    assert "alice" in bob_sees, "muted user still discovers the muter"


def test_notifications_from_a_now_muted_actor_are_hidden() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")

    bob.post(f"/api/v1/follows/{alice_id}")  # alice gets a follow notification
    assert len(alice.get("/api/v1/notifications").json()["items"]) == 1

    alice.post(f"/api/v1/mutes/{bob_id}")
    assert alice.get("/api/v1/notifications").json()["items"] == []


def test_muted_users_own_profile_and_tweets_stay_visible() -> None:
    """Unlike a block, a mute never hides the muted user's profile itself."""
    alice, _ = register("alice")
    bob, bob_id = register("bob")

    tweet_id = _post(bob, "bob's tweet")
    bob.post(f"/api/v1/tweets/{tweet_id}/comments", json={"content": "bob's reply"})

    alice.post(f"/api/v1/mutes/{bob_id}")

    # The muter can still open the muted user's profile timeline...
    assert len(alice.get("/api/v1/users/bob/tweets").json()["items"]) == 1
    assert len(alice.get("/api/v1/users/bob/replies").json()["items"]) == 1
    # ...and click through to an individual tweet of theirs.
    assert alice.get(f"/api/v1/tweets/{tweet_id}").status_code == 200


def test_unmute_restores_content() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    bob_tweet = _post(bob, "from bob")

    alice.post(f"/api/v1/mutes/{bob_id}")
    assert bob_tweet not in _for_you_ids(alice)

    assert alice.delete(f"/api/v1/mutes/{bob_id}").status_code == 200
    assert bob_tweet in _for_you_ids(alice)


# --------------------------------------------------------------- muted list


def test_muted_list_is_paginated_newest_first() -> None:
    alice, _ = register("alice")
    _, bob_id = register("bob")
    _, carol_id = register("carol")

    alice.post(f"/api/v1/mutes/{bob_id}")
    alice.post(f"/api/v1/mutes/{carol_id}")

    page = alice.get("/api/v1/mutes").json()
    usernames = [item["username"] for item in page["items"]]
    assert usernames == ["carol", "bob"], "most recently muted first"
    assert all("muted_at" in item for item in page["items"])
