"""
The moderation queue -- the consumer side of reporting.

Access is the operator-granted ``is_moderator`` flag; anyone else gets 404 so
the surface stays undisclosed. Moderators judge the *post*: dismissing or
taking it down closes every open report about it together. A takedown hides
the post from every read path but keeps the row, so it is reversible and a
taken-down tweet's thread stays reachable through a tombstone.
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


def make_moderator(user_id: int) -> None:
    """The operator path (deploy/set_moderator.py), minus the CLI."""
    from app.models.user import User

    with TestingSessionLocal() as db:
        db.get(User, user_id).is_moderator = True
        db.commit()


def register_moderator(username: str = "mod") -> tuple[TestClient, int]:
    client, user_id = register(username)
    make_moderator(user_id)
    return client, user_id


def _post(client: TestClient, content: str) -> int:
    return client.post("/api/v1/tweets", json={"content": content}).json()["id"]


def _report(client: TestClient, post_id: int, reason: str = "spam") -> None:
    response = client.post(
        f"/api/v1/reports/posts/{post_id}", json={"reason": reason}
    )
    assert response.status_code == 201, response.text


# --- access ----------------------------------------------------------------


def test_non_moderator_gets_404_everywhere() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    tweet_id = _post(bob, "hello")

    assert alice.get("/api/v1/moderation/reports").status_code == 404
    assert alice.post(f"/api/v1/moderation/posts/{tweet_id}/dismiss").status_code == 404
    assert alice.post(f"/api/v1/moderation/posts/{tweet_id}/takedown").status_code == 404
    assert alice.post(f"/api/v1/moderation/posts/{tweet_id}/restore").status_code == 404


def test_own_profile_shows_the_flag_but_nobody_elses_does() -> None:
    mod, _ = register_moderator("modesty")
    alice, _ = register("alice")

    assert mod.get("/api/v1/users/modesty/profile").json()["is_moderator"] is True
    # The roster is not browsable: another viewer sees False regardless.
    assert alice.get("/api/v1/users/modesty/profile").json()["is_moderator"] is False


# --- the queue -------------------------------------------------------------


def test_queue_groups_reports_by_post() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    carol, _ = register("carol")
    mod, _ = register_moderator()

    tweet_id = _post(carol, "widely reported")
    _report(alice, tweet_id, "spam")
    _report(bob, tweet_id, "hate")

    page = mod.get("/api/v1/moderation/reports").json()
    assert len(page["items"]) == 1, "two reports on one post are one queue item"
    item = page["items"][0]
    assert item["post"]["id"] == tweet_id
    assert item["report_count"] == 2
    assert {r["reason"] for r in item["reports"]} == {"spam", "hate"}
    assert {r["reporter"]["username"] for r in item["reports"]} == {"alice", "bob"}


def test_queue_ignores_the_moderators_own_blocks() -> None:
    alice, _ = register("alice")
    bob, bob_id = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "reported across a block")
    _report(alice, tweet_id)
    mod.post(f"/api/v1/blocks/{bob_id}")

    page = mod.get("/api/v1/moderation/reports").json()
    assert [item["post"]["id"] for item in page["items"]] == [tweet_id]


def test_queue_paginates_by_latest_report() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_ids = [_post(bob, f"post {n}") for n in range(3)]
    for tweet_id in tweet_ids:
        _report(alice, tweet_id)

    first = mod.get("/api/v1/moderation/reports", params={"limit": 2}).json()
    assert len(first["items"]) == 2
    assert first["next_cursor"] is not None

    second = mod.get(
        "/api/v1/moderation/reports",
        params={"limit": 2, "cursor": first["next_cursor"]},
    ).json()
    seen = [item["post"]["id"] for item in first["items"] + second["items"]]
    assert sorted(seen) == sorted(tweet_ids)
    assert len(seen) == len(set(seen)), "pages must not overlap"


# --- dismiss ---------------------------------------------------------------


def test_dismiss_closes_the_reports_and_changes_nothing_else() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "innocent")
    _report(alice, tweet_id)

    body = mod.post(f"/api/v1/moderation/posts/{tweet_id}/dismiss").json()
    assert body["resolved_reports"] == 1
    assert body["taken_down"] is False

    assert mod.get("/api/v1/moderation/reports").json()["items"] == []
    # The post is untouched for everyone.
    assert alice.get(f"/api/v1/tweets/{tweet_id}").json()["taken_down"] is False

    resolved = mod.get(
        "/api/v1/moderation/reports", params={"status": "resolved"}
    ).json()
    assert [item["post"]["id"] for item in resolved["items"]] == [tweet_id]
    assert resolved["items"][0]["reports"][0]["status"] == "dismissed"


# --- takedown --------------------------------------------------------------


def test_takedown_hides_the_tweet_from_feeds_and_tombstones_its_detail() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")
    mod, _ = register_moderator()

    alice.post(f"/api/v1/follows/{bob_id}")
    tweet_id = _post(bob, "the offending tweet")
    keeper_id = _post(bob, "a fine tweet")
    _report(alice, tweet_id)

    body = mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown").json()
    assert body["taken_down"] is True
    assert body["resolved_reports"] == 1

    # Gone from the home timeline; the author's other post survives.
    feed = alice.get(
        "/api/v1/timeline/home", params={"strategy": "read", "limit": 20}
    ).json()
    feed_ids = [item["id"] for item in feed["items"]]
    assert tweet_id not in feed_ids
    assert keeper_id in feed_ids

    # Gone from the author's profile feed too.
    profile = alice.get("/api/v1/users/bob/tweets").json()
    assert tweet_id not in [item["id"] for item in profile["items"]]

    # The detail endpoint answers with a tombstone, not the content.
    detail = alice.get(f"/api/v1/tweets/{tweet_id}").json()
    assert detail["taken_down"] is True
    assert detail["content"] == ""

    # No further interaction with a removed post.
    assert alice.post(f"/api/v1/tweets/{tweet_id}/likes").status_code == 404
    assert (
        alice.post(
            f"/api/v1/tweets/{tweet_id}/comments", json={"content": "hi"}
        ).status_code
        == 404
    )
    assert (
        alice.post(
            "/api/v1/tweets", json={"content": "q", "quoted_post_id": tweet_id}
        ).status_code
        == 404
    )
    assert (
        alice.post(
            f"/api/v1/reports/posts/{tweet_id}", json={"reason": "spam"}
        ).status_code
        == 404
    )


def test_takedown_of_a_comment_drops_it_from_the_thread_with_its_subtree() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(alice, "root")
    bad = bob.post(
        f"/api/v1/tweets/{tweet_id}/comments", json={"content": "abusive"}
    ).json()
    child = alice.post(
        f"/api/v1/comments/{bad['id']}/comments", json={"content": "reply to it"}
    ).json()
    _report(alice, bad["id"], "abuse")

    mod.post(f"/api/v1/moderation/posts/{bad['id']}/takedown")

    comments = alice.get(f"/api/v1/tweets/{tweet_id}/comments").json()
    listed = [comment["id"] for comment in comments]
    assert bad["id"] not in listed
    assert child["id"] not in listed, "the subtree goes with its parent"
    assert alice.get(f"/api/v1/comments/{bad['id']}").status_code == 404


def test_quote_embeds_of_a_taken_down_post_render_as_no_embed() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    quoted_id = _post(bob, "soon to be removed")
    quote = alice.post(
        "/api/v1/tweets", json={"content": "look", "quoted_post_id": quoted_id}
    ).json()
    _report(alice, quoted_id)

    mod.post(f"/api/v1/moderation/posts/{quoted_id}/takedown")

    detail = alice.get(f"/api/v1/tweets/{quote['id']}").json()
    assert detail["quoted_post"] is None, "a removed quote renders like a deleted one"


def test_takedown_is_idempotent_and_restore_reverses_it() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "borderline")
    _report(alice, tweet_id)

    assert (
        mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown").json()[
            "resolved_reports"
        ]
        == 1
    )
    # A second takedown resolves nothing further and stays 200.
    assert (
        mod.post(f"/api/v1/moderation/posts/{tweet_id}/takedown").json()[
            "resolved_reports"
        ]
        == 0
    )

    body = mod.post(f"/api/v1/moderation/posts/{tweet_id}/restore").json()
    assert body["taken_down"] is False

    detail = alice.get(f"/api/v1/tweets/{tweet_id}").json()
    assert detail["taken_down"] is False
    assert detail["content"] == "borderline"
    # The judgement stays recorded: reports do not reopen on restore.
    assert mod.get("/api/v1/moderation/reports").json()["items"] == []


def test_re_reporting_after_a_judgement_reopens_the_queue_item() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    mod, _ = register_moderator()

    tweet_id = _post(bob, "still at it")
    _report(alice, tweet_id)
    mod.post(f"/api/v1/moderation/posts/{tweet_id}/dismiss")
    assert mod.get("/api/v1/moderation/reports").json()["items"] == []

    # The reporter insists: the amended report reopens, not stays buried.
    _report(alice, tweet_id, "abuse")
    reopened = mod.get("/api/v1/moderation/reports").json()
    assert [item["post"]["id"] for item in reopened["items"]] == [tweet_id]
    assert reopened["items"][0]["reports"][0]["reason"] == "abuse"


def test_unknown_post_is_404_for_actions() -> None:
    mod, _ = register_moderator()
    assert mod.post("/api/v1/moderation/posts/999999/takedown").status_code == 404
