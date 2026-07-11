"""
Account deletion (soft delete + PII scrub).

The account row is tombstoned rather than removed: authored posts stay so the
threads others built on them survive, but every personal edge, the notification
history, the PII, sessions, and the avatar are destroyed. The account can never
log in again and no longer surfaces in discovery.
"""

from conftest import TestingSessionLocal
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


def _delete(client: TestClient, password: str = "password123"):
    return client.request(
        "DELETE", "/api/v1/auth/account", json={"password": password}
    )


def _user_row(user_id: int):
    from app.models.user import User

    with TestingSessionLocal() as db:
        return db.get(User, user_id)


# ------------------------------------------------------------------ the endpoint


def test_delete_requires_the_correct_password() -> None:
    alice, alice_id = register("alice")
    assert _delete(alice, "wrong-password").status_code == 403
    # Still usable after a failed attempt.
    assert alice.get("/api/v1/users/alice/profile").status_code == 200


def test_delete_tombstones_the_row_and_scrubs_pii() -> None:
    alice, alice_id = register("alice")
    alice.patch("/api/v1/users/me", json={"display_name": "Alice", "bio": "hi there"})

    assert _delete(alice).status_code == 204

    row = _user_row(alice_id)
    assert row is not None, "soft delete keeps the row"
    assert row.deleted_at is not None
    assert row.username == f"deleted_{alice_id}"
    assert row.email is None
    assert row.pending_email is None
    assert row.display_name is None
    assert row.bio is None
    assert row.avatar_url is None


def test_deleted_account_cannot_log_in_and_frees_its_username() -> None:
    alice, alice_id = register("alice")
    _delete(alice)

    # The original credentials no longer work by any spelling.
    login = TestClient(app).post(
        "/api/v1/auth/login", json={"username": "alice", "password": "password123"}
    )
    assert login.status_code == 401

    # The username is free to claim again.
    reclaim = TestClient(app).post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice2@example.com",
            "password": "password123",
        },
    )
    assert reclaim.status_code == 201


def test_delete_revokes_the_callers_session() -> None:
    alice, alice_id = register("alice")
    # Authenticated before...
    assert alice.get("/api/v1/notifications").status_code == 200
    _delete(alice)
    # ...and logged out after: the session cookie no longer resolves.
    assert alice.get("/api/v1/notifications").status_code == 401


# ------------------------------------------------------------------- what survives


def test_authored_posts_and_others_replies_survive_as_a_tombstone() -> None:
    alice, alice_id = register("alice")
    bob, _ = register("bob")

    tweet_id = _post(alice, "hot take")
    bob.post(f"/api/v1/tweets/{tweet_id}/comments", json={"content": "disagree"})

    _delete(alice)

    # The tweet is still readable, now authored by a tombstone.
    fetched = bob.get(f"/api/v1/tweets/{tweet_id}")
    assert fetched.status_code == 200
    author = fetched.json()["author"]
    assert author["is_deleted"] is True
    assert author["username"] == f"deleted_{alice_id}"

    # Bob's reply under it survives.
    contents = [
        c["content"] for c in bob.get(f"/api/v1/tweets/{tweet_id}/comments").json()
    ]
    assert "disagree" in contents


# --------------------------------------------------------------- what is cleaned up


def test_delete_removes_follow_edges_both_ways() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")
    alice.post(f"/api/v1/follows/{bob_id}")
    bob.post(f"/api/v1/follows/{alice_id}")

    _delete(alice)

    # Bob no longer follows -- or is followed by -- the deleted account.
    assert bob.get("/api/v1/users/bob/profile").json()["following_count"] == 0
    assert bob.get("/api/v1/users/bob/profile").json()["follower_count"] == 0


def test_delete_removes_likes_so_counts_drop() -> None:
    alice, alice_id = register("alice")
    bob, _ = register("bob")
    tweet_id = _post(bob, "likeable")
    alice.post(f"/api/v1/tweets/{tweet_id}/likes/toggle")
    assert bob.get(f"/api/v1/tweets/{tweet_id}").json()["like_count"] == 1

    _delete(alice)
    assert bob.get(f"/api/v1/tweets/{tweet_id}").json()["like_count"] == 0


def test_delete_clears_notifications_the_account_generated() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")
    alice.post(f"/api/v1/follows/{bob_id}")  # bob gets a follow notification
    assert len(bob.get("/api/v1/notifications").json()["items"]) == 1

    _delete(alice)
    assert bob.get("/api/v1/notifications").json()["items"] == []


def test_deleted_account_disappears_from_discovery() -> None:
    alice, alice_id = register("alice")
    bob, _ = register("bob")

    assert "alice" in {u["username"] for u in bob.get("/api/v1/users").json()}
    _delete(alice)
    usernames = {u["username"] for u in bob.get("/api/v1/users").json()}
    assert "alice" not in usernames
    assert f"deleted_{alice_id}" not in usernames


def test_profile_reports_is_deleted() -> None:
    alice, alice_id = register("alice")
    bob, _ = register("bob")
    _delete(alice)

    profile = bob.get(f"/api/v1/users/deleted_{alice_id}/profile")
    assert profile.status_code == 200
    assert profile.json()["is_deleted"] is True
