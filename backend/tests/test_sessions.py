import pytest
from app.core import session_store
from app.core.config import settings
from app.core.security import sign_payload
from app.core.session_store import (
    SessionBackendUnavailable,
    create_session,
    destroy_session,
    resolve_session,
    revoke_user_sessions,
)
from fastapi.testclient import TestClient
from main import app


@pytest.fixture(autouse=True)
def _in_memory_sessions(monkeypatch) -> None:
    """
    Exercise the store without depending on a running Redis.

    Both backends are covered: the tests that only care about semantics run
    against the in-process fallback, and test_redis_backend_roundtrip skips
    itself when Redis is absent.
    """
    monkeypatch.setattr(session_store, "get_redis_client", lambda: None)
    monkeypatch.setattr(settings, "session_cookie_secure", False)
    session_store._memory_sessions.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, username: str = "sessuser") -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": "supersecret1"},
    )
    assert response.status_code == 201, response.text


def test_cookie_does_not_contain_the_user_id() -> None:
    cookie = create_session(user_id=42)
    sid, _, signature = cookie.partition(".")

    assert signature, "cookie must be signed"
    # The old scheme was base64(user_id); the new one must be opaque.
    assert "42" not in sid
    assert resolve_session(cookie) == 42


def test_forged_signature_is_rejected_without_touching_the_store() -> None:
    cookie = create_session(user_id=7)
    sid = cookie.split(".", 1)[0]

    assert resolve_session(f"{sid}.not-the-real-signature") is None
    # A correctly signed id that was never issued must also fail.
    assert resolve_session(f"never-issued.{sign_payload('never-issued')}") is None
    # Malformed shapes must not raise.
    assert resolve_session("no-dot-here") is None
    assert resolve_session("") is None
    assert resolve_session(None) is None


def test_expired_session_is_rejected() -> None:
    cookie = create_session(user_id=5, ttl_seconds=-1)
    assert resolve_session(cookie) is None


def test_active_session_slides_its_ttl() -> None:
    cookie = create_session(user_id=9, ttl_seconds=1)
    sid = cookie.split(".", 1)[0]

    # resolve() with refresh=True should push expiry out to the full TTL.
    assert resolve_session(cookie, refresh=True) == 9
    _, expires_at = session_store._memory_sessions[sid]

    assert resolve_session(cookie, refresh=False) == 9
    _, unchanged = session_store._memory_sessions[sid]
    assert unchanged == expires_at, "refresh=False must not extend the session"


def test_destroy_session_invalidates_immediately() -> None:
    cookie = create_session(user_id=3)
    assert resolve_session(cookie) == 3

    destroy_session(cookie)
    assert resolve_session(cookie) is None

    # Destroying an unknown or malformed cookie must be a no-op, not an error.
    destroy_session(cookie)
    destroy_session("garbage")
    destroy_session(None)


def test_revoke_user_sessions_logs_out_every_device() -> None:
    first = create_session(user_id=11)
    second = create_session(user_id=11)
    other = create_session(user_id=12)

    assert revoke_user_sessions(11) == 2

    assert resolve_session(first) is None
    assert resolve_session(second) is None
    assert resolve_session(other) == 12, "other users must be unaffected"


def test_logout_invalidates_a_captured_cookie(client: TestClient) -> None:
    """The whole point of server-side sessions: logout must revoke, not just clear."""
    _register(client)
    captured = client.cookies[settings.session_cookie_name]

    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.post("/api/v1/auth/logout").status_code == 204

    # Replay the value an attacker would have captured before logout.
    replay = TestClient(app)
    replay.cookies.set(settings.session_cookie_name, captured)
    assert replay.get("/api/v1/auth/me").status_code == 401


def test_expired_cookie_is_rejected_by_the_api(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(settings, "session_ttl_seconds", -1)
    _register(client)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_production_refuses_the_in_memory_fallback(monkeypatch) -> None:
    """Without Redis, a production config must fail closed rather than degrade."""
    monkeypatch.setattr(settings, "session_cookie_secure", True)

    with pytest.raises(SessionBackendUnavailable):
        create_session(user_id=1)

    # A validly signed id reaches the backend, which is missing -> fail closed.
    signed = f"some-sid.{sign_payload('some-sid')}"
    with pytest.raises(SessionBackendUnavailable):
        resolve_session(signed)


def test_forged_cookie_never_reaches_the_backend(monkeypatch) -> None:
    """A bad signature is a 401, not a 503 -- it must not consult the store."""
    monkeypatch.setattr(settings, "session_cookie_secure", True)

    assert resolve_session("anything.at-all") is None
    assert resolve_session(None) is None


def test_session_backend_unavailable_surfaces_as_503(client: TestClient, monkeypatch) -> None:
    _register(client)
    monkeypatch.setattr(settings, "session_cookie_secure", True)

    response = client.get("/api/v1/auth/me")
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_redis_backend_roundtrip(monkeypatch) -> None:
    """Same semantics against the real backend, when one is running."""
    monkeypatch.undo()  # restore the real get_redis_client
    from app.db.redis_client import get_redis_client

    if get_redis_client() is None:
        pytest.skip("Redis is not running")

    cookie = create_session(user_id=4242, ttl_seconds=60)
    assert resolve_session(cookie) == 4242

    destroy_session(cookie)
    assert resolve_session(cookie) is None

    a = create_session(user_id=4243, ttl_seconds=60)
    b = create_session(user_id=4243, ttl_seconds=60)
    assert revoke_user_sessions(4243) == 2
    assert resolve_session(a) is None
    assert resolve_session(b) is None
