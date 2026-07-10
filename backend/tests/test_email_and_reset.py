"""
Email confirmation and password reset.

Most of these are not happy-path tests. The reset flow hands an unauthenticated
caller a way to take over an account, so what is worth pinning is everything the
flow refuses to do: enumerate addresses, honour a link twice, leave old sessions
alive, mail an unconfirmed address, or let a stolen cookie repoint where the
mail goes.
"""

import re

import pytest
from app.api.routes import auth as auth_routes
from app.core import tokens
from app.core.config import settings
from app.core.tokens import TokenPurpose
from app.services import mailer
from fastapi.testclient import TestClient
from main import app

PASSWORD = "original-password-1"
NEW_PASSWORD = "replacement-password-2"


class Outbox(list):
    """Captures what would have been mailed, and digs the token out of the link."""

    def send(self, to: str, subject: str, body: str) -> None:
        self.append({"to": to, "subject": subject, "body": body})

    @property
    def last(self) -> dict:
        assert self, "no mail was sent"
        return self[-1]

    def token(self) -> str:
        match = re.search(r"token=([\w\-]+)", self.last["body"])
        assert match, f"no token in mail body:\n{self.last['body']}"
        return match.group(1)


@pytest.fixture
def outbox(monkeypatch) -> Outbox:
    box = Outbox()
    monkeypatch.setattr(mailer, "send_email", box.send)
    return box


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _register(client: TestClient, username="alice", email="alice@example.com"):
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": email, "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    return response


def _verify(client: TestClient, token: str):
    return client.post("/api/v1/auth/verify-email", json={"token": token})


def _forgot(client: TestClient, email: str):
    return client.post("/api/v1/auth/forgot-password", json={"email": email})


def _reset(client: TestClient, token: str, new_password: str = NEW_PASSWORD):
    return client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "new_password": new_password},
    )


def _login(client: TestClient, password: str, username: str = "alice"):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )


def _confirmed_user(client: TestClient, outbox: Outbox, **kwargs) -> None:
    _register(client, **kwargs)
    assert _verify(client, outbox.token()).status_code == 204


# ------------------------------------------------------------------- the flow


def test_register_claims_an_address_without_confirming_it(client, outbox) -> None:
    _register(client)

    profile = client.get("/api/v1/users/alice/profile").json()
    assert profile["email"] is None, "an unconfirmed address must not count"
    assert profile["pending_email"] == "alice@example.com"
    assert outbox.last["to"] == "alice@example.com"


def test_confirming_promotes_the_address(client, outbox) -> None:
    _register(client)
    assert _verify(client, outbox.token()).status_code == 204

    profile = client.get("/api/v1/users/alice/profile").json()
    assert profile["email"] == "alice@example.com"
    assert profile["pending_email"] is None


def test_reset_replaces_the_password(client, outbox) -> None:
    _confirmed_user(client, outbox)

    assert _forgot(client, "alice@example.com").status_code == 202
    assert _reset(client, outbox.token()).status_code == 204

    fresh = TestClient(app)
    assert _login(fresh, PASSWORD).status_code == 401, "old password still works"
    assert _login(fresh, NEW_PASSWORD).status_code == 200


def test_reset_does_not_sign_the_caller_in(client, outbox) -> None:
    """Whoever holds the link may be whoever read the mailbox."""
    _confirmed_user(client, outbox)
    _forgot(client, "alice@example.com")

    anonymous = TestClient(app)
    assert _reset(anonymous, outbox.token()).status_code == 204
    assert anonymous.get("/api/v1/auth/me").status_code == 401


def test_reset_kills_every_existing_session(client, outbox) -> None:
    _confirmed_user(client, outbox)
    assert client.get("/api/v1/auth/me").status_code == 200

    _forgot(client, "alice@example.com")
    assert _reset(TestClient(app), outbox.token()).status_code == 204

    assert client.get("/api/v1/auth/me").status_code == 401, "stale session survived"


# ------------------------------------------------------------- no enumeration


@pytest.mark.parametrize(
    "email", ["alice@example.com", "nobody@example.com", "unconfirmed@example.com"]
)
def test_forgot_password_answers_the_same_whoever_you_ask_about(
    client, outbox, email: str
) -> None:
    """
    202 for a real account, an unknown address, and a merely-claimed one alike.

    Anything else turns this endpoint into an oracle for "does this person have
    an account here", which on a social network is a disclosure in itself.
    """
    _confirmed_user(client, outbox)
    _register(TestClient(app), username="bob", email="unconfirmed@example.com")

    response = _forgot(TestClient(app), email)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}


def test_an_unconfirmed_address_is_never_mailed_a_reset_link(client, outbox) -> None:
    """Otherwise a typo'd or squatted address receives somebody's reset link."""
    _register(client, username="bob", email="claimed@example.com")
    outbox.clear()

    assert _forgot(TestClient(app), "claimed@example.com").status_code == 202

    assert outbox == [], "a reset link was mailed to an unconfirmed address"


def test_a_broken_mailer_still_answers_202(client, outbox, monkeypatch) -> None:
    """
    A 503 would only ever be raised for an address that exists -- which answers
    the exact question the uniform 202 exists to hide.
    """
    _confirmed_user(client, outbox)

    def down(*args, **kwargs):
        raise mailer.MailerUnavailable("smtp is down")

    monkeypatch.setattr(mailer, "send_email", down)

    assert _forgot(TestClient(app), "alice@example.com").status_code == 202


def test_email_is_never_exposed_on_someone_elses_profile(client, outbox) -> None:
    _confirmed_user(client, outbox)

    bob = TestClient(app)
    _register(bob, username="bob", email="bob@example.com")

    seen_by_bob = bob.get("/api/v1/users/alice/profile").json()
    assert seen_by_bob["email"] is None
    assert seen_by_bob["pending_email"] is None

    seen_by_alice = client.get("/api/v1/users/alice/profile").json()
    assert seen_by_alice["email"] == "alice@example.com"


def test_a_tweet_author_never_carries_an_email(client, outbox) -> None:
    """UserSummary rides inside every tweet; email must not be on it."""
    _confirmed_user(client, outbox)
    tweet = client.post("/api/v1/tweets", json={"content": "hello"})
    assert tweet.status_code == 201

    author = tweet.json()["author"]
    assert "email" not in author and "pending_email" not in author


# ----------------------------------------------------------------- the tokens


def test_a_reset_link_works_exactly_once(client, outbox) -> None:
    _confirmed_user(client, outbox)
    _forgot(client, "alice@example.com")
    token = outbox.token()

    assert _reset(TestClient(app), token).status_code == 204
    assert _reset(TestClient(app), token, "third-password-3").status_code == 400

    fresh = TestClient(app)
    assert _login(fresh, NEW_PASSWORD).status_code == 200, "the replay won"


def test_asking_again_invalidates_the_previous_link(client, outbox) -> None:
    """A link phished from an inbox goes stale when the owner asks for their own."""
    _confirmed_user(client, outbox)

    _forgot(client, "alice@example.com")
    stolen = outbox.token()
    _forgot(client, "alice@example.com")
    current = outbox.token()
    assert stolen != current

    assert _reset(TestClient(app), stolen).status_code == 400
    assert _reset(TestClient(app), current).status_code == 204


def test_changing_the_password_invalidates_outstanding_reset_links(
    client, outbox
) -> None:
    """An attacker who requested a link must not redeem it after being locked out."""
    _confirmed_user(client, outbox)
    _forgot(client, "alice@example.com")
    attacker_token = outbox.token()

    assert (
        client.post(
            "/api/v1/auth/change-password",
            json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        ).status_code
        == 204
    )

    assert _reset(TestClient(app), attacker_token, "attacker-password-3").status_code == 400


def test_a_confirmation_token_cannot_be_redeemed_as_a_reset_token(
    client, outbox
) -> None:
    _register(client)
    confirmation = outbox.token()

    assert _reset(TestClient(app), confirmation).status_code == 400


def test_a_reset_token_cannot_confirm_an_address(client, outbox) -> None:
    _confirmed_user(client, outbox)
    _forgot(client, "alice@example.com")

    assert _verify(TestClient(app), outbox.token()).status_code == 400


def test_an_expired_link_is_refused(client, outbox, monkeypatch) -> None:
    monkeypatch.setattr(settings, "password_reset_token_ttl_seconds", 0)
    _confirmed_user(client, outbox)
    _forgot(client, "alice@example.com")

    assert _reset(TestClient(app), outbox.token()).status_code == 400


def test_a_garbage_token_is_refused(client) -> None:
    assert _reset(client, "not-a-real-token").status_code == 400
    assert _verify(client, "not-a-real-token").status_code == 400


def test_tokens_are_stored_hashed_never_in_the_clear(client, outbox) -> None:
    """A dump of the token store must not yield anything redeemable."""
    _confirmed_user(client, outbox)
    _forgot(client, "alice@example.com")
    token = outbox.token()

    stored = tokens._memory_tokens
    assert stored, "no token was stored"
    assert token not in stored
    assert all(token not in str(key) for key in stored)
    assert tokens._digest(token) in stored


# ----------------------------------------------- the account-takeover boundary


def test_changing_email_requires_the_current_password(client, outbox) -> None:
    """
    The hole this closes: a stolen cookie alone must not repoint reset mail.

    Without this check a thief would set the address to their own, click
    "forgot password", and own the account -- walking straight around
    change-password's current-password check.
    """
    _confirmed_user(client, outbox)

    response = client.post(
        "/api/v1/auth/change-email",
        json={"current_password": "not-the-password", "email": "thief@example.com"},
    )

    assert response.status_code == 403
    profile = client.get("/api/v1/users/alice/profile").json()
    assert profile["email"] == "alice@example.com"
    assert profile["pending_email"] is None


def test_a_new_address_does_not_receive_reset_mail_until_confirmed(
    client, outbox
) -> None:
    """
    Even knowing the password, a thief cannot silently divert reset mail: the
    confirmed address keeps working until the new one is proven.
    """
    _confirmed_user(client, outbox)

    assert (
        client.post(
            "/api/v1/auth/change-email",
            json={"current_password": PASSWORD, "email": "thief@example.com"},
        ).status_code
        == 202
    )

    outbox.clear()
    assert _forgot(TestClient(app), "thief@example.com").status_code == 202
    assert outbox == [], "reset mail went to the unconfirmed new address"

    assert _forgot(TestClient(app), "alice@example.com").status_code == 202
    assert outbox.last["to"] == "alice@example.com"


def test_an_address_confirmed_by_someone_else_cannot_be_stolen(client, outbox) -> None:
    _confirmed_user(client, outbox)

    bob = TestClient(app)
    _register(bob, username="bob", email="bob@example.com")

    response = bob.post(
        "/api/v1/auth/change-email",
        json={"current_password": PASSWORD, "email": "alice@example.com"},
    )
    assert response.status_code == 409


def test_two_accounts_may_claim_one_address_but_only_one_confirms_it(
    client, outbox
) -> None:
    """
    pending_email is deliberately not unique. Whoever proves control wins; the
    loser's claim simply never promotes.
    """
    _register(client, username="alice", email="shared@example.com")
    alice_token = outbox.token()

    bob = TestClient(app)
    _register(bob, username="bob", email="shared@example.com")
    bob_token = outbox.token()

    assert _verify(TestClient(app), alice_token).status_code == 204
    assert _verify(TestClient(app), bob_token).status_code == 409

    assert client.get("/api/v1/users/alice/profile").json()["email"] == "shared@example.com"
    assert bob.get("/api/v1/users/bob/profile").json()["email"] is None


def test_registering_a_confirmed_address_twice_is_refused(client, outbox) -> None:
    _confirmed_user(client, outbox)

    response = TestClient(app).post(
        "/api/v1/auth/register",
        json={
            "username": "impostor",
            "email": "alice@example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 409


# ------------------------------------------------------------------- ordering


def test_a_failed_revoke_aborts_before_the_reset_lands(client, outbox, monkeypatch) -> None:
    """
    Same rule as change-password: revoke first, write second.

    Writing first would leave the new password in place with every old session
    still alive, after a reset performed precisely because the old password was
    presumed lost.
    """
    _confirmed_user(client, outbox)
    _forgot(client, "alice@example.com")
    token = outbox.token()

    def unavailable(*args, **kwargs):
        raise auth_routes.SessionBackendUnavailable("redis is gone")

    monkeypatch.setattr(auth_routes, "revoke_user_sessions", unavailable)
    assert _reset(TestClient(app), token).status_code == 503
    monkeypatch.undo()

    fresh = TestClient(app)
    assert _login(fresh, PASSWORD).status_code == 200, "password changed anyway"
    assert _login(fresh, NEW_PASSWORD).status_code == 401


# --------------------------------------------------------------- misc surfaces


def test_resend_needs_something_to_confirm(client, outbox) -> None:
    _confirmed_user(client, outbox)
    assert client.post("/api/v1/auth/resend-verification").status_code == 400


def test_resend_replaces_the_previous_confirmation_link(client, outbox) -> None:
    _register(client)
    first = outbox.token()

    assert client.post("/api/v1/auth/resend-verification").status_code == 202
    second = outbox.token()
    assert first != second

    assert _verify(TestClient(app), first).status_code == 400
    assert _verify(TestClient(app), second).status_code == 204


def test_verify_and_resend_and_forgot_are_rate_limited() -> None:
    from app.core.rate_limit import REGISTERED_BUCKETS

    for bucket in (
        "forgot_password",
        "reset_password",
        "verify_email",
        "change_email",
        "resend_verification",
    ):
        assert bucket in REGISTERED_BUCKETS, bucket


def test_addresses_are_normalized_before_they_are_matched(client, outbox) -> None:
    """Alice@Example.COM and alice@example.com are one account, not two."""
    _register(client, username="alice", email="Alice@Example.COM")
    assert _verify(client, outbox.token()).status_code == 204

    assert client.get("/api/v1/users/alice/profile").json()["email"] == "alice@example.com"

    outbox.clear()
    assert _forgot(TestClient(app), "ALICE@example.com").status_code == 202
    assert outbox.last["to"] == "alice@example.com"


@pytest.mark.parametrize("username", ["reset-password", "verify-email", "forgot-password"])
def test_the_pages_a_mailed_link_lands_on_are_reserved_usernames(
    client, username: str
) -> None:
    """
    Profiles live at /:username. React Router prefers a static segment, so
    registering these would not hijack the reset page -- it would strand the
    user's own profile behind a URL that never reaches it.
    """
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username.replace('-', '')}@example.com",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 422


def test_email_delivery_is_not_required_for_an_account_to_exist(
    client, monkeypatch
) -> None:
    """A mail server that is down must not stop people signing up."""

    def down(*args, **kwargs):
        raise mailer.MailerUnavailable("smtp is down")

    monkeypatch.setattr(mailer, "send_email", down)

    assert _register(client).status_code == 201
    assert client.get("/api/v1/auth/me").status_code == 200


def test_change_email_reports_503_when_mail_cannot_be_sent(client, outbox, monkeypatch):
    """Authenticated and self-directed, so there is no address to protect here."""
    _confirmed_user(client, outbox)

    def down(*args, **kwargs):
        raise mailer.MailerUnavailable("smtp is down")

    monkeypatch.setattr(mailer, "send_email", down)

    response = client.post(
        "/api/v1/auth/change-email",
        json={"current_password": PASSWORD, "email": "new@example.com"},
    )
    assert response.status_code == 503
