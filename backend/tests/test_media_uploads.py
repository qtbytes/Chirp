import io
from pathlib import Path

from app.core.config import settings
from fastapi.testclient import TestClient
from main import app

# Minimal valid 1x1 PNG.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000100057b8e9b0000000049454e44ae426082"
)


def _register(client: TestClient, username: str) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 201


def _upload_png(client: TestClient) -> str:
    response = client.post(
        "/api/v1/media",
        files={"file": ("pic.png", io.BytesIO(PNG_BYTES), "image/png")},
    )
    assert response.status_code == 201
    return response.json()["url"]


def test_upload_media_and_attach_to_tweet(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    client = TestClient(app)
    _register(client, "alice")

    url = _upload_png(client)
    assert url.startswith("/uploads/media/")
    assert (tmp_path / "media" / Path(url).name).exists()

    created = client.post("/api/v1/tweets", json={"content": "look", "media_urls": [url]})
    assert created.status_code == 201
    assert created.json()["media_urls"] == [url]

    fetched = client.get(f"/api/v1/tweets/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["media_urls"] == [url]


def test_tweet_with_multiple_media(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    client = TestClient(app)
    _register(client, "alice")

    urls = [_upload_png(client) for _ in range(3)]
    created = client.post("/api/v1/tweets", json={"content": "", "media_urls": urls})
    assert created.status_code == 201
    assert created.json()["media_urls"] == urls


def test_tweet_rejects_more_than_four_media(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    client = TestClient(app)
    _register(client, "alice")

    urls = [_upload_png(client) for _ in range(5)]
    response = client.post("/api/v1/tweets", json={"content": "hi", "media_urls": urls})
    assert response.status_code == 422


def test_media_only_tweet_is_allowed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    client = TestClient(app)
    _register(client, "alice")
    url = _upload_png(client)

    created = client.post("/api/v1/tweets", json={"content": "", "media_urls": [url]})
    assert created.status_code == 201
    assert created.json()["media_urls"] == [url]


def test_empty_tweet_without_media_is_rejected() -> None:
    client = TestClient(app)
    _register(client, "alice")
    response = client.post("/api/v1/tweets", json={"content": "   "})
    assert response.status_code == 422


def test_tweet_rejects_arbitrary_media_url() -> None:
    client = TestClient(app)
    _register(client, "alice")
    response = client.post(
        "/api/v1/tweets",
        json={"content": "hi", "media_urls": ["http://evil.example/x.png"]},
    )
    assert response.status_code == 422


def test_media_upload_rejects_non_image() -> None:
    client = TestClient(app)
    _register(client, "alice")
    response = client.post(
        "/api/v1/media",
        files={"file": ("note.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert response.status_code == 415


def test_upload_video_and_attach_to_tweet(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    client = TestClient(app)
    _register(client, "alice")

    response = client.post(
        "/api/v1/media",
        files={"file": ("clip.mp4", io.BytesIO(b"\x00\x00\x00\x18ftypmp42"), "video/mp4")},
    )
    assert response.status_code == 201
    url = response.json()["url"]
    assert url.endswith(".mp4")
    assert (tmp_path / "media" / Path(url).name).exists()

    created = client.post("/api/v1/tweets", json={"content": "watch", "media_urls": [url]})
    assert created.status_code == 201
    assert created.json()["media_urls"] == [url]


def test_comment_with_media_round_trips(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    client = TestClient(app)
    _register(client, "alice")
    url = _upload_png(client)

    tweet = client.post("/api/v1/tweets", json={"content": "hi"})
    tweet_id = tweet.json()["id"]

    comment = client.post(
        f"/api/v1/tweets/{tweet_id}/comments",
        json={"content": "", "media_urls": [url]},
    )
    assert comment.status_code == 201
    assert comment.json()["media_urls"] == [url]

    listed = client.get(f"/api/v1/tweets/{tweet_id}/comments")
    assert listed.status_code == 200
    assert listed.json()[0]["media_urls"] == [url]
