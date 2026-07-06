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
