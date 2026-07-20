"""
Account deletion (soft delete + PII scrub).

The account row is tombstoned rather than removed: authored posts stay so the
threads others built on them survive, but every personal edge, the notification
history, the view trail, the PII, sessions, and the avatar are destroyed. The
account can never log in again and no longer surfaces in discovery. DM messages
the account wrote are the one deliberate exception -- "hidden, not deleted" --
so those rows are retained even though the chat becomes unreachable via the API.
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


def _for_you_ids(client: TestClient) -> list[int]:
    return [item["id"] for item in client.get("/api/v1/timeline/for-you").json()["items"]]


def _delete(client: TestClient, password: str = "password123"):
    return client.request(
        "DELETE", "/api/v1/auth/account", json={"password": password}
    )


def _user_row(user_id: int):
    from app.models.user import User

    with TestingSessionLocal() as db:
        return db.get(User, user_id)


# ------------------------------------------------------------------ the endpoint


def test_delete_requires_the_correct_password() -> None:
    alice, alice_id = register("alice")
    assert _delete(alice, "wrong-password").status_code == 403
    # Still usable after a failed attempt.
    assert alice.get("/api/v1/users/alice/profile").status_code == 200


def test_delete_tombstones_the_row_and_scrubs_pii() -> None:
    alice, alice_id = register("alice")
    alice.patch("/api/v1/users/me", json={"display_name": "Alice", "bio": "hi there"})

    assert _delete(alice).status_code == 204

    row = _user_row(alice_id)
    assert row is not None, "soft delete keeps the row"
    assert row.deleted_at is not None
    assert row.username == f"deleted_{alice_id}"
    assert row.email is None
    assert row.pending_email is None
    assert row.display_name is None
    assert row.bio is None
    assert row.avatar_url is None


def test_deleted_account_cannot_log_in_and_frees_its_username() -> None:
    alice, alice_id = register("alice")
    _delete(alice)

    # The original credentials no longer work by any spelling.
    login = TestClient(app).post(
        "/api/v1/auth/login", json={"username": "alice", "password": "password123"}
    )
    assert login.status_code == 401

    # The username is free to claim again.
    reclaim = TestClient(app).post(
        "/api/v1/auth/register",
        json={
            "username": "alice",
            "email": "alice2@example.com",
            "password": "password123",
        },
    )
    assert reclaim.status_code == 201


def test_delete_revokes_the_callers_session() -> None:
    alice, alice_id = register("alice")
    # Authenticated before...
    assert alice.get("/api/v1/notifications").status_code == 200
    _delete(alice)
    # ...and logged out after: the session cookie no longer resolves.
    assert alice.get("/api/v1/notifications").status_code == 401


# ------------------------------------------------------------------- what survives


def test_authored_posts_and_others_replies_survive_as_a_tombstone() -> None:
    alice, alice_id = register("alice")
    bob, _ = register("bob")

    tweet_id = _post(alice, "hot take")
    bob.post(f"/api/v1/tweets/{tweet_id}/comments", json={"content": "disagree"})

    _delete(alice)

    # The tweet is still readable, now authored by a tombstone.
    fetched = bob.get(f"/api/v1/tweets/{tweet_id}")
    assert fetched.status_code == 200
    author = fetched.json()["author"]
    assert author["is_deleted"] is True
    assert author["username"] == f"deleted_{alice_id}"

    # Bob's reply under it survives.
    contents = [
        c["content"] for c in bob.get(f"/api/v1/tweets/{tweet_id}/comments").json()
    ]
    assert "disagree" in contents


# --------------------------------------------------------------- what is cleaned up


def test_delete_removes_follow_edges_both_ways() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")
    alice.post(f"/api/v1/follows/{bob_id}")
    bob.post(f"/api/v1/follows/{alice_id}")

    _delete(alice)

    # Bob no longer follows -- or is followed by -- the deleted account.
    assert bob.get("/api/v1/users/bob/profile").json()["following_count"] == 0
    assert bob.get("/api/v1/users/bob/profile").json()["follower_count"] == 0


def test_delete_removes_likes_so_counts_drop() -> None:
    alice, alice_id = register("alice")
    bob, _ = register("bob")
    tweet_id = _post(bob, "likeable")
    alice.post(f"/api/v1/tweets/{tweet_id}/likes/toggle")
    assert bob.get(f"/api/v1/tweets/{tweet_id}").json()["like_count"] == 1

    _delete(alice)
    assert bob.get(f"/api/v1/tweets/{tweet_id}").json()["like_count"] == 0


def test_delete_clears_notifications_the_account_generated() -> None:
    alice, alice_id = register("alice")
    bob, bob_id = register("bob")
    alice.post(f"/api/v1/follows/{bob_id}")  # bob gets a follow notification
    assert len(bob.get("/api/v1/notifications").json()["items"]) == 1

    _delete(alice)
    assert bob.get("/api/v1/notifications").json()["items"] == []


def test_delete_removes_the_view_trail() -> None:
    from app.models.post_view import PostView

    alice, alice_id = register("alice")
    bob, _ = register("bob")
    tweet_id = _post(bob, "worth a look")
    # Alice opens the post, so a post_views row is written for her.
    alice.post("/api/v1/tweets/views", json={"ids": [tweet_id]})

    def _view_rows() -> int:
        with TestingSessionLocal() as db:
            return db.query(PostView).filter(PostView.user_id == alice_id).count()

    assert _view_rows() == 1, "precondition: alice's view was recorded"

    _delete(alice)
    assert _view_rows() == 0, "the deleted account's view trail is cleared"


def test_delete_retains_the_dm_messages_the_account_wrote() -> None:
    """
    DMs follow a "hidden, not deleted" rule, so account deletion does NOT scrub
    the message rows the way it does likes or follows. The deleted account's chat
    becomes unreachable through the API (the counterpart 404s and the inbox skips
    it), but the rows themselves survive in the database.
    """
    alice, alice_id = register("alice")
    bob, _ = register("bob")

    sent = alice.post("/api/v1/dm/with/bob/messages", json={"content": "hey bob"})
    assert sent.status_code == 201, sent.text

    def _messages_from(user_id: int) -> list[str]:
        from app.models.dm import DmMessage

        with TestingSessionLocal() as db:
            return [
                m.content
                for m in db.query(DmMessage).filter(DmMessage.sender_id == user_id).all()
            ]

    assert _messages_from(alice_id) == ["hey bob"], "precondition: the message exists"

    _delete(alice)

    # The row survives deletion...
    assert _messages_from(alice_id) == ["hey bob"], "DM rows are retained, not scrubbed"

    # ...but the surviving counterpart can no longer reach the chat: the deleted
    # account 404s, and its conversation drops off the inbox.
    assert bob.get(f"/api/v1/dm/with/deleted_{alice_id}").status_code == 404
    assert bob.get("/api/v1/dm/conversations").json()["items"] == []


def test_delete_discards_reports_filed_against_the_account() -> None:
    """
    Deleting an account discards the open reports *targeting* it, not only the
    ones it filed: the account is gone, so a report against it could never be
    actioned -- it would only haunt the moderation queue as a tombstone card.
    """
    from app.models.report import Report
    from sqlalchemy import select

    alice, alice_id = register("alice")
    bob, _ = register("bob")
    assert (
        bob.post(f"/api/v1/reports/users/{alice_id}", json={"reason": "abuse"}).status_code
        == 201
    )

    def _reports_against(user_id: int) -> int:
        with TestingSessionLocal() as db:
            return len(
                list(db.scalars(select(Report).where(Report.reported_user_id == user_id)))
            )

    assert _reports_against(alice_id) == 1, "precondition: the report exists"

    _delete(alice)
    assert _reports_against(alice_id) == 0, "the target's reports go with the account"


def test_deleted_account_disappears_from_discovery() -> None:
    alice, alice_id = register("alice")
    bob, _ = register("bob")

    assert "alice" in {u["username"] for u in bob.get("/api/v1/users").json()}
    _delete(alice)
    usernames = {u["username"] for u in bob.get("/api/v1/users").json()}
    assert "alice" not in usernames
    assert f"deleted_{alice_id}" not in usernames


def test_profile_reports_is_deleted() -> None:
    alice, alice_id = register("alice")
    bob, _ = register("bob")
    _delete(alice)

    profile = bob.get(f"/api/v1/users/deleted_{alice_id}/profile")
    assert profile.status_code == 200
    assert profile.json()["is_deleted"] is True


# ------------------------------------------------------------- feeds vs. profile


def test_deleted_authors_posts_leave_the_for_you_feed() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    alice_tweet = _post(alice, "from alice")

    assert alice_tweet in _for_you_ids(bob), "precondition: bob sees alice"
    _delete(alice)
    assert alice_tweet not in _for_you_ids(bob), "deleted author's post left the feed"


def test_deleted_authors_posts_still_show_on_their_own_profile() -> None:
    """The tombstone keeps its posts: only the *feeds* filter them, not the profile."""
    alice, alice_id = register("alice")
    bob, _ = register("bob")
    _post(alice, "still here")

    _delete(alice)

    items = bob.get(f"/api/v1/users/deleted_{alice_id}/tweets").json()["items"]
    assert len(items) == 1
    assert items[0]["author"]["is_deleted"] is True


def test_write_timeline_excludes_a_deleted_author_from_precomputed_feed() -> None:
    from conftest import TestingSessionLocal
    from app.repositories import feed_repository, tweet_repository, user_repository

    with TestingSessionLocal() as db:
        viewer = user_repository.create_user(db, username="viewer", password_hash="x")
        author = user_repository.create_user(db, username="author", password_hash="x")
        tweet = tweet_repository.create_tweet(db, author_id=author.id, content="hi")
        feed_repository.bulk_insert_feed_items(
            db,
            owner_ids=[viewer.id],
            post_id=tweet.id,
            actor_id=author.id,
            created_at=tweet.created_at,
        )

        before = feed_repository.list_feed_tweets(
            db, owner_id=viewer.id, limit=10, exclude_deleted_authors=True
        )
        assert any(row["tweet"].id == tweet.id for row in before)

        user_repository.soft_delete_user(db, author.id, scrubbed_password_hash="x")
        after = feed_repository.list_feed_tweets(
            db, owner_id=viewer.id, limit=10, exclude_deleted_authors=True
        )
        assert all(
            row["tweet"].id != tweet.id for row in after
        ), "the deleted author's stale feed row is hidden"


# ----------------------------------------------------------------- username shape


def test_deleted_username_shape_is_reserved_at_registration() -> None:
    """Nobody may claim deleted_<n>, so a future deletion's tombstone never collides."""
    taken = TestClient(app).post(
        "/api/v1/auth/register",
        json={
            "username": "deleted_5",
            "email": "squatter@example.com",
            "password": "password123",
        },
    )
    assert taken.status_code == 422

    # A name that merely starts with "deleted_" but isn't the tombstone shape is fine.
    ok = TestClient(app).post(
        "/api/v1/auth/register",
        json={
            "username": "deleted_dreams",
            "email": "dreams@example.com",
            "password": "password123",
        },
    )
    assert ok.status_code == 201
