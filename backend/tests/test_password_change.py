"""
POST /auth/change-password.

The interesting property is not that the hash changes -- it is that every
session minted with the old password dies, while the device doing the change
stays signed in. Server-side sessions are what make that possible; a
self-contained token could not be revoked.
"""

import pytest
from app.api.routes import auth as auth_routes
from app.core.config import settings
from app.core.session_store import SessionBackendUnavailable
from app.repositories import user_repository
from fastapi.testclient import TestClient
from main import app

OLD_PASSWORD = "old-password-1"
NEW_PASSWORD = "new-password-2"


def _client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, username: str = "changer") -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": f"{username}@example.com", "password": OLD_PASSWORD},
    )
    assert response.status_code == 201, response.text


def _change(client: TestClient, current: str = OLD_PASSWORD, new: str = NEW_PASSWORD):
    return client.post(
        "/api/v1/auth/change-password",
        json={"current_password": current, "new_password": new},
    )


def _login(client: TestClient, password: str, username: str = "changer"):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


def test_change_password_swaps_the_credential() -> None:
    client = _client()
    _register(client)

    assert _change(client).status_code == 204

    fresh = _client()
    assert _login(fresh, OLD_PASSWORD).status_code == 401, "old password still works"
    assert _login(fresh, NEW_PASSWORD).status_code == 200


def test_change_password_keeps_the_caller_signed_in() -> None:
    client = _client()
    _register(client)

    assert _change(client).status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 200


def test_change_password_logs_every_other_device_out() -> None:
    """The reason to change a password is that the old one leaked."""
    laptop = _client()
    _register(laptop)

    phone = _client()
    assert _login(phone, OLD_PASSWORD).status_code == 200
    assert phone.get("/api/v1/auth/me").status_code == 200

    assert _change(laptop).status_code == 204

    assert phone.get("/api/v1/auth/me").status_code == 401, "stale session survived"
    assert laptop.get("/api/v1/auth/me").status_code == 200


def test_wrong_current_password_is_rejected_and_changes_nothing() -> None:
    """A stolen cookie alone must not be enough to take the account over."""
    client = _client()
    _register(client)

    assert _change(client, current="not-the-password").status_code == 403

    fresh = _client()
    assert _login(fresh, OLD_PASSWORD).status_code == 200, "password was changed anyway"
    assert client.get("/api/v1/auth/me").status_code == 200, "caller was logged out"


def test_reusing_the_current_password_is_rejected() -> None:
    client = _client()
    _register(client)

    response = _change(client, new=OLD_PASSWORD)

    assert response.status_code == 400
    assert "differ" in response.json()["detail"]


@pytest.mark.parametrize("new_password", ["short", "x" * 129])
def test_the_new_password_must_satisfy_the_registration_rules(new_password: str) -> None:
    client = _client()
    _register(client)

    assert _change(client, new=new_password).status_code == 422


def test_a_short_wrong_current_password_is_403_not_422() -> None:
    """
    Bounding current_password's length would answer a short wrong guess with 422
    and a long one with 403, leaking the password policy to a stolen session.
    """
    client = _client()
    _register(client)

    assert _change(client, current="x").status_code == 403


def test_change_password_requires_a_session() -> None:
    assert _change(_client()).status_code == 401


def test_a_failed_write_leaves_the_old_password_working(monkeypatch) -> None:
    """
    Sessions are revoked before the new hash is written.

    Should the write fail, the account must still be reachable with the old
    password. The reverse order would leave sessions opened with a leaked
    password alive after a change made precisely to kill them.
    """
    client = _client()
    _register(client)

    def boom(*args, **kwargs):
        raise RuntimeError("database went away")

    monkeypatch.setattr(user_repository, "update_user_password", boom)

    crashing = TestClient(app, raise_server_exceptions=False)
    crashing.cookies.update(client.cookies)
    assert _change(crashing).status_code == 500

    monkeypatch.undo()

    fresh = _client()
    assert _login(fresh, OLD_PASSWORD).status_code == 200, "password changed despite failure"
    assert _login(fresh, NEW_PASSWORD).status_code == 401


def test_a_failed_revoke_aborts_before_the_password_changes(monkeypatch) -> None:
    """
    The case that pins the ordering.

    If the write happened first, a revoke that then failed would leave the new
    password in place *and* every session opened with the old one alive -- the
    exact outcome the change was meant to prevent. Revoking first means a broken
    session store can only fail the request, never half-apply it.
    """
    client = _client()
    _register(client)

    def unavailable(*args, **kwargs):
        raise SessionBackendUnavailable("redis is gone")

    monkeypatch.setattr(auth_routes, "revoke_user_sessions", unavailable)

    assert _change(client).status_code == 503

    monkeypatch.undo()

    fresh = _client()
    assert _login(fresh, OLD_PASSWORD).status_code == 200, "password changed anyway"
    assert _login(fresh, NEW_PASSWORD).status_code == 401


def test_change_password_is_rate_limited() -> None:
    """Guessing the current password from a stolen session has a budget."""
    from app.core.rate_limit import REGISTERED_BUCKETS

    assert "change_password" in REGISTERED_BUCKETS
    assert settings.rate_limit_change_password_max_requests > 0
