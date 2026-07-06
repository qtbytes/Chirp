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
