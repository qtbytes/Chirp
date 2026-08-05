"""Followers / following list endpoints."""

from fastapi.testclient import TestClient
from main import app


def register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_following_list_reflects_viewer_state() -> None:
    alice, bob, carol = TestClient(app), TestClient(app), TestClient(app)
    register(alice, "alice")
    b = register(bob, "bob")
    c = register(carol, "carol")

    alice.post(f"/api/v1/follows/{b['id']}")
    alice.post(f"/api/v1/follows/{c['id']}")

    page = alice.get("/api/v1/users/alice/following").json()
    assert [item["username"] for item in page["items"]] == ["carol", "bob"], "newest first"
    assert all(item["is_following"] for item in page["items"]), "alice follows both"
    assert all(not item["is_current_user"] for item in page["items"])
    assert page["next_cursor"] is None


def test_followers_list_and_current_user_flag() -> None:
    alice, bob = TestClient(app), TestClient(app)
    a = register(alice, "alice")
    register(bob, "bob")

    bob.post(f"/api/v1/follows/{a['id']}")  # bob follows alice, not the reverse

    seen_by_alice = alice.get("/api/v1/users/alice/followers").json()["items"]
    assert [item["username"] for item in seen_by_alice] == ["bob"]
    assert seen_by_alice[0]["is_current_user"] is False
    assert seen_by_alice[0]["is_following"] is False, "alice does not follow bob back"

    # Bob viewing the same list sees himself flagged, so the UI hides his button.
    seen_by_bob = bob.get("/api/v1/users/alice/followers").json()["items"]
    assert seen_by_bob[0]["is_current_user"] is True


def test_followers_list_paginates_without_skips_or_dupes() -> None:
    alice = TestClient(app)
    a = register(alice, "alice")

    for name in ("bob", "carol", "dave"):
        follower = TestClient(app)
        register(follower, name)
        follower.post(f"/api/v1/follows/{a['id']}")

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # guard against a cursor that never terminates
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = alice.get("/api/v1/users/alice/followers", params=params).json()
        seen.extend(item["username"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert seen == ["dave", "carol", "bob"], "newest-first, every follower once"
    assert len(seen) == len(set(seen))


def test_follow_lists_reject_unknown_user_and_bad_cursor() -> None:
    client = TestClient(app)
    register(client, "alice")

    assert client.get("/api/v1/users/ghost/followers").status_code == 404
    assert client.get("/api/v1/users/ghost/following").status_code == 404
    assert (
        client.get(
            "/api/v1/users/alice/followers", params={"cursor": "garbage"}
        ).status_code
        == 400
    )


def test_follow_lists_are_empty_for_a_lonely_account() -> None:
    client = TestClient(app)
    register(client, "alice")

    followers = client.get("/api/v1/users/alice/followers").json()
    following = client.get("/api/v1/users/alice/following").json()
    assert followers == {"items": [], "next_cursor": None}
    assert following == {"items": [], "next_cursor": None}


def test_following_list_reports_a_suspended_account() -> None:
    """
    A suspension keeps every follow edge, so this list is where a summary blind
    to ``is_suspended`` showed most plainly -- a frozen account listed as live.
    """
    from datetime import datetime, timezone

    from app.models.user import User
    from conftest import TestingSessionLocal

    alice, bob = TestClient(app), TestClient(app)
    register(alice, "alice")
    bob_id = register(bob, "bob")["id"]
    alice.post(f"/api/v1/follows/{bob_id}")

    with TestingSessionLocal() as db:
        db.get(User, bob_id).suspended_at = datetime.now(timezone.utc)
        db.commit()

    item = alice.get("/api/v1/users/alice/following").json()["items"][0]
    assert item["username"] == "bob"
    assert item["is_suspended"] is True
