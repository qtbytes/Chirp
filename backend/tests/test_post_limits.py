from datetime import datetime, timedelta, timezone

import pytest
from app.models.user import User
from app.services.post_limits import (
    BASE_POST_LENGTH,
    GLOBAL_MAX_POST_LENGTH,
    MAX_BONUS,
    TENURE_BONUS_MAX_MONTHS,
    post_length_allowance,
    whole_months_between,
)
from conftest import TestingSessionLocal
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
    assert response.status_code == 201
    return response.json()


def _user(*, email: str | None, months_old: int) -> User:
    """A detached User standing in for one registered ``months_old`` months ago."""
    now = datetime.now(timezone.utc)
    # Step back whole months by day-count, then normalise onto the same day of
    # month, so the boundary maths is exercised rather than sidestepped.
    created = now - timedelta(days=31 * months_old)
    created = created.replace(day=min(now.day, 28))
    return User(
        username="x",
        password_hash="x",
        email=email,
        created_at=created,
    )


# --------------------------------------------------------------- the rule itself


def test_fresh_unverified_account_gets_the_base_limit() -> None:
    allowance = post_length_allowance(_user(email=None, months_old=0))
    assert allowance.limit == BASE_POST_LENGTH == 280
    assert allowance.email_verified is False
    assert allowance.at_global_max is False


def test_confirming_an_email_adds_a_thousand() -> None:
    allowance = post_length_allowance(_user(email="a@example.com", months_old=0))
    assert allowance.limit == 1280
    assert allowance.verified_bonus == 1000
    assert allowance.tenure_bonus == 0


def test_tenure_adds_a_hundred_per_whole_month() -> None:
    """The worked example from the spec: 1280 -> 1380 -> 1480."""
    limits = [
        post_length_allowance(_user(email="a@example.com", months_old=n)).limit
        for n in (0, 1, 2)
    ]
    assert limits == [1280, 1380, 1480]


def test_the_ceiling_holds_however_it_is_reached() -> None:
    verified = post_length_allowance(_user(email="a@example.com", months_old=9))
    unverified = post_length_allowance(_user(email=None, months_old=12))
    assert verified.limit == unverified.limit == GLOBAL_MAX_POST_LENGTH == 1480
    assert verified.at_global_max and unverified.at_global_max


def test_unverified_account_climbs_to_the_ceiling_on_tenure_alone() -> None:
    for months, expected in ((1, 380), (6, 880), (11, 1380), (12, 1480)):
        allowance = post_length_allowance(_user(email=None, months_old=months))
        assert allowance.limit == expected, months


def test_tenure_promo_stops_after_the_first_year() -> None:
    """Past 12 months nothing more accrues -- the bonus is a launch promotion."""
    two_years = post_length_allowance(_user(email=None, months_old=24))
    one_year = post_length_allowance(_user(email=None, months_old=12))
    assert two_years.limit == one_year.limit
    assert two_years.tenure_months == TENURE_BONUS_MAX_MONTHS


def test_breakdown_always_sums_to_the_limit() -> None:
    """The UI shows the parts beside the total, so a clipped sum must still add up."""
    for email in (None, "a@example.com"):
        for months in range(0, 15):
            a = post_length_allowance(_user(email=email, months_old=months))
            assert a.base + a.verified_bonus + a.tenure_bonus == a.limit
            assert a.verified_bonus + a.tenure_bonus <= MAX_BONUS


@pytest.mark.parametrize(
    "start,end,expected",
    [
        # A day short of the anniversary is not yet a month.
        (datetime(2026, 1, 15), datetime(2026, 2, 14), 0),
        (datetime(2026, 1, 15), datetime(2026, 2, 15), 1),
        (datetime(2026, 1, 15), datetime(2027, 1, 15), 12),
        # Joined on the 31st: February has no 31st, so the month rolls over on
        # the next date that exists -- never earlier than the anniversary.
        (datetime(2026, 1, 31), datetime(2026, 2, 28), 0),
        (datetime(2026, 1, 31), datetime(2026, 3, 31), 2),
        # A clock skewed backwards must not produce a negative bonus.
        (datetime(2026, 6, 1), datetime(2026, 1, 1), 0),
    ],
)
def test_whole_months_between(start: datetime, end: datetime, expected: int) -> None:
    assert whole_months_between(start, end) == expected


# ------------------------------------------------------------------ the API edge


def test_me_reports_the_allowance_and_its_breakdown() -> None:
    client = TestClient(app)
    register(client, "alice")

    body = client.get("/api/v1/auth/me").json()
    limit = body["post_length"]
    # Registration only *claims* an address; nothing is confirmed yet.
    assert limit["email_verified"] is False
    assert limit["limit"] == 280
    assert limit["global_max"] == 1480


def test_confirming_email_raises_the_limit_the_api_reports() -> None:
    client = TestClient(app)
    register(client, "alice")

    # Promote the claimed address the way a redeemed token would.
    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.username == "alice").one()
        user.email = user.pending_email
        user.pending_email = None
        db.commit()
    finally:
        db.close()

    limit = client.get("/api/v1/auth/me").json()["post_length"]
    assert limit["email_verified"] is True
    assert limit["limit"] == 1280


def test_post_longer_than_the_users_limit_is_rejected() -> None:
    client = TestClient(app)
    register(client, "alice")

    over = client.post("/api/v1/tweets", json={"content": "x" * 281})
    assert over.status_code == 422
    assert "281" in over.json()["detail"]
    assert "280" in over.json()["detail"]

    assert client.post("/api/v1/tweets", json={"content": "x" * 280}).status_code == 201


def test_post_beyond_the_global_ceiling_is_rejected_by_the_schema() -> None:
    client = TestClient(app)
    register(client, "alice")
    response = client.post(
        "/api/v1/tweets", json={"content": "x" * (GLOBAL_MAX_POST_LENGTH + 1)}
    )
    assert response.status_code == 422


def test_a_raised_limit_actually_lets_a_longer_post_through() -> None:
    client = TestClient(app)
    register(client, "alice")

    db = TestingSessionLocal()
    try:
        user = db.query(User).filter(User.username == "alice").one()
        user.email = user.pending_email
        user.pending_email = None
        db.commit()
    finally:
        db.close()

    assert client.post("/api/v1/tweets", json={"content": "x" * 1280}).status_code == 201
    assert client.post("/api/v1/tweets", json={"content": "x" * 1281}).status_code == 422


def test_the_limit_applies_to_comments_replies_and_edits_too() -> None:
    client = TestClient(app)
    register(client, "alice")
    tweet = client.post("/api/v1/tweets", json={"content": "root"}).json()

    over = {"content": "x" * 281}
    within = {"content": "x" * 280}

    assert client.post(f"/api/v1/tweets/{tweet['id']}/comments", json=over).status_code == 422
    comment = client.post(
        f"/api/v1/tweets/{tweet['id']}/comments", json=within
    ).json()

    assert client.post(f"/api/v1/comments/{comment['id']}/comments", json=over).status_code == 422
    assert client.patch(f"/api/v1/comments/{comment['id']}", json=over).status_code == 422
    # Editing is the obvious way around a create-time check, so it is guarded too.
    assert client.patch(f"/api/v1/tweets/{tweet['id']}", json=over).status_code == 422
    assert client.patch(f"/api/v1/tweets/{tweet['id']}", json=within).status_code == 200
