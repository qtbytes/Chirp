import pytest
from app.core import session_store
from app.core.config import settings
from app.core.security import sign_payload
from app.core.session_store import (
    SessionBackendUnavailable,
    create_session,
    destroy_session,
    list_user_sessions,
    resolve_session,
    revoke_session_by_handle,
    revoke_user_sessions,
    session_handle,
    session_id_from_cookie,
)
from app.db.redis_client import get_redis_client
from fastapi.testclient import TestClient
from main import app
from redis.exceptions import RedisError


def _redis_down(monkeypatch) -> None:
    """Make the session store see Redis as unreachable."""

    def down(*args, **kwargs):
        raise RedisError("redis is gone")

    monkeypatch.setattr(session_store, "get_redis_client", down)


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
    cookie = create_session(user_id=9)
    sid = cookie.split(".", 1)[0]
    key = session_store._session_key(sid)
    redis = get_redis_client()

    # Shrink the TTL so a refresh is observable.
    redis.expire(key, 5)

    # A read that does not refresh must leave the (short) TTL alone.
    assert resolve_session(cookie, refresh=False) == 9
    assert redis.ttl(key) <= 5, "refresh=False must not extend the session"

    # A refreshing read pushes it back out to the full session TTL.
    assert resolve_session(cookie, refresh=True) == 9
    assert redis.ttl(key) > 5, "refresh=True slides the TTL forward"


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


# --------------------------------------------------- listing & selective revoke


def test_list_user_sessions_returns_only_the_owners_with_metadata() -> None:
    a = create_session(user_id=20, ip="1.1.1.1", user_agent="Firefox")
    b = create_session(user_id=20, ip="2.2.2.2", user_agent="Safari")
    create_session(user_id=99, ip="9.9.9.9", user_agent="Edge")

    sessions = list_user_sessions(20)

    assert len(sessions) == 2, "another user's session must not appear"
    assert {s.ip for s in sessions} == {"1.1.1.1", "2.2.2.2"}
    assert {s.user_agent for s in sessions} == {"Firefox", "Safari"}
    # The handle is sha256(sid), not the sid itself.
    ids = {s.id for s in sessions}
    assert session_handle(session_id_from_cookie(a)) in ids
    assert session_handle(session_id_from_cookie(b)) in ids


def test_revoke_user_sessions_can_keep_the_current_one() -> None:
    keep = create_session(user_id=30)
    other = create_session(user_id=30)

    assert revoke_user_sessions(30, keep_sid=session_id_from_cookie(keep)) == 1
    assert resolve_session(keep) == 30, "the kept session must survive"
    assert resolve_session(other) is None


def test_revoke_session_by_handle_targets_one_and_only_the_owners() -> None:
    mine = create_session(user_id=40)
    also_mine = create_session(user_id=40)
    someone_else = create_session(user_id=41)

    handle = session_handle(session_id_from_cookie(also_mine))
    assert revoke_session_by_handle(40, handle) is True
    assert resolve_session(also_mine) is None
    assert resolve_session(mine) == 40, "sibling session untouched"

    # A handle for another user's session is invisible from this account.
    foreign = session_handle(session_id_from_cookie(someone_else))
    assert revoke_session_by_handle(40, foreign) is False
    assert resolve_session(someone_else) == 41

    # An unknown handle is a miss, not an error.
    assert revoke_session_by_handle(40, "deadbeef") is False


def test_sessions_endpoint_lists_and_flags_current(client: TestClient) -> None:
    _register(client)
    me_id = client.get("/api/v1/auth/me").json()["id"]
    # A second device signed in as the same user.
    create_session(user_id=me_id, ip="5.6.7.8", user_agent="OtherDevice/1.0")

    response = client.get("/api/v1/auth/sessions")
    assert response.status_code == 200, response.text
    sessions = response.json()

    assert len(sessions) == 2
    assert [s for s in sessions if s["current"]], "one entry must be the caller"
    assert len([s for s in sessions if s["current"]]) == 1

    # The raw session id must never appear in the listing.
    captured_sid = client.cookies[settings.session_cookie_name].split(".")[0]
    assert all(captured_sid not in session["id"] for session in sessions)


def test_logout_others_keeps_this_device_and_drops_the_rest(client: TestClient) -> None:
    _register(client)
    me_id = client.get("/api/v1/auth/me").json()["id"]
    ghost = create_session(user_id=me_id)

    response = client.post("/api/v1/auth/logout-others")
    assert response.status_code == 200, response.text
    assert response.json()["revoked"] == 1

    assert client.get("/api/v1/auth/me").status_code == 200, "this device stays in"
    assert resolve_session(ghost) is None, "the other device is gone"


def test_revoke_named_session_endpoint_guards(client: TestClient) -> None:
    _register(client)
    me_id = client.get("/api/v1/auth/me").json()["id"]
    ghost = create_session(user_id=me_id, user_agent="Ghost")
    ghost_handle = session_handle(session_id_from_cookie(ghost))

    current = next(s for s in client.get("/api/v1/auth/sessions").json() if s["current"])
    # The current session cannot be ended here; that is what logout is for.
    assert client.delete(f"/api/v1/auth/sessions/{current['id']}").status_code == 400
    # An unknown handle is a 404.
    assert client.delete("/api/v1/auth/sessions/unknown-handle").status_code == 404

    # The other device is revoked and really gone.
    assert client.delete(f"/api/v1/auth/sessions/{ghost_handle}").status_code == 204
    assert resolve_session(ghost) is None


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


def test_redis_outage_fails_closed(monkeypatch) -> None:
    """An unreachable Redis raises SessionBackendUnavailable, never degrades."""
    _redis_down(monkeypatch)

    with pytest.raises(SessionBackendUnavailable):
        create_session(user_id=1)

    # A validly signed id reaches the backend, which is down -> fail closed.
    signed = f"some-sid.{sign_payload('some-sid')}"
    with pytest.raises(SessionBackendUnavailable):
        resolve_session(signed)


def test_forged_cookie_never_reaches_the_backend(monkeypatch) -> None:
    """A bad signature is a miss, not a 503 -- it must not consult the store."""
    # Even with Redis down, a forged/absent cookie is rejected on the signature
    # check before the store is ever touched.
    _redis_down(monkeypatch)

    assert resolve_session("anything.at-all") is None
    assert resolve_session(None) is None


def test_session_backend_unavailable_surfaces_as_503(client: TestClient, monkeypatch) -> None:
    _register(client)
    _redis_down(monkeypatch)

    response = client.get("/api/v1/auth/me")
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"].lower()


def test_full_session_lifecycle_and_legacy_pruning() -> None:
    """End-to-end against the real Redis backend: lifecycle, revoke, metadata, legacy."""
    cookie = create_session(user_id=4242, ttl_seconds=60)
    assert resolve_session(cookie) == 4242

    destroy_session(cookie)
    assert resolve_session(cookie) is None

    a = create_session(user_id=4243, ttl_seconds=60)
    b = create_session(user_id=4243, ttl_seconds=60)
    assert revoke_user_sessions(4243) == 2
    assert resolve_session(a) is None
    assert resolve_session(b) is None

    # Metadata round-trips through a real Redis hash, and the handle-based
    # revoke keeps the sibling alive.
    keep = create_session(user_id=4244, ttl_seconds=60, ip="8.8.8.8", user_agent="Real")
    drop = create_session(user_id=4244, ttl_seconds=60, ip="9.9.9.9")
    listed = list_user_sessions(4244)
    assert {s.ip for s in listed} == {"8.8.8.8", "9.9.9.9"}
    assert revoke_session_by_handle(4244, session_handle(session_id_from_cookie(drop)))
    assert resolve_session(drop) is None
    assert resolve_session(keep) == 4244
    revoke_user_sessions(4244)

    # A pre-hash session (the old `session:<sid> -> user_id` string) must not
    # 503 the new code: it resolves to nothing, and the listing skips and prunes
    # it instead of choking on WRONGTYPE.
    client = get_redis_client()
    legacy_sid = "legacy-sid-for-test"
    client.set(f"session:{legacy_sid}", "4245")
    client.sadd("session_user:4245", legacy_sid)
    legacy_cookie = f"{legacy_sid}.{sign_payload(legacy_sid)}"
    try:
        assert resolve_session(legacy_cookie) is None, "legacy session must not 503"

        modern = create_session(user_id=4245, ttl_seconds=60, ip="4.4.4.4")
        listed = list_user_sessions(4245)
        assert [s.ip for s in listed] == ["4.4.4.4"], "legacy row skipped, modern kept"
        assert not client.sismember("session_user:4245", legacy_sid), "legacy pruned"
        assert resolve_session(modern) == 4245
    finally:
        client.delete(f"session:{legacy_sid}")
        revoke_user_sessions(4245)
