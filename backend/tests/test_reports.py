"""
Reporting. A report is a moderator signal, not a personal filter: it records
(reporter, post, reason) and changes nothing about what the reporter sees. One
report per (reporter, post) -- re-reporting amends the reason. You can't report
your own post, nor a post hidden from you by a block.
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


def _reports_for(post_id: int) -> list:
    from app.models.report import Report
    from sqlalchemy import select

    with TestingSessionLocal() as db:
        return list(db.scalars(select(Report).where(Report.post_id == post_id)))


def test_report_records_reason_and_reporter() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    tweet_id = _post(bob, "spammy link")

    response = alice.post(
        f"/api/v1/reports/posts/{tweet_id}",
        json={"reason": "spam", "details": "buy now scam"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["post_id"] == tweet_id
    assert body["reason"] == "spam"

    reports = _reports_for(tweet_id)
    assert len(reports) == 1
    assert reports[0].reason == "spam"
    assert reports[0].details == "buy now scam"


def test_report_is_idempotent_and_amends_the_reason() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    tweet_id = _post(bob, "questionable")

    alice.post(f"/api/v1/reports/posts/{tweet_id}", json={"reason": "spam"})
    alice.post(f"/api/v1/reports/posts/{tweet_id}", json={"reason": "abuse"})

    reports = _reports_for(tweet_id)
    assert len(reports) == 1, "re-reporting must not stack rows"
    assert reports[0].reason == "abuse", "the latest reason wins"


def test_two_reporters_each_get_a_row() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    carol, _ = register("carol")
    tweet_id = _post(carol, "bad")

    alice.post(f"/api/v1/reports/posts/{tweet_id}", json={"reason": "spam"})
    bob.post(f"/api/v1/reports/posts/{tweet_id}", json={"reason": "hate"})

    assert len(_reports_for(tweet_id)) == 2


def test_cannot_report_your_own_post() -> None:
    alice, _ = register("alice")
    tweet_id = _post(alice, "mine")

    response = alice.post(
        f"/api/v1/reports/posts/{tweet_id}", json={"reason": "spam"}
    )
    assert response.status_code == 400
    assert _reports_for(tweet_id) == []


def test_report_unknown_post_is_404() -> None:
    alice, _ = register("alice")
    assert (
        alice.post("/api/v1/reports/posts/999999", json={"reason": "spam"}).status_code
        == 404
    )


def test_report_rejects_an_unknown_reason() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    tweet_id = _post(bob, "hi")

    response = alice.post(
        f"/api/v1/reports/posts/{tweet_id}", json={"reason": "because"}
    )
    assert response.status_code == 422


def test_a_blocked_authors_post_cannot_be_reported() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    tweet_id = _post(bob, "hi")

    alice.post(f"/api/v1/blocks/{bob_id}")
    # Hidden from the reporter, so 404 -- the block is never disclosed.
    assert (
        alice.post(f"/api/v1/reports/posts/{tweet_id}", json={"reason": "spam"}).status_code
        == 404
    )
    assert _reports_for(tweet_id) == []


def test_reporting_does_not_hide_the_post_from_the_reporter() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    tweet_id = _post(bob, "still visible")

    alice.post(f"/api/v1/reports/posts/{tweet_id}", json={"reason": "spam"})
    # A report is not a mute: the post is still fetchable by the reporter.
    assert alice.get(f"/api/v1/tweets/{tweet_id}").status_code == 200
