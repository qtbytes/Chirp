"""
Content search (FTS5), plus the #hashtag / @mention extraction that runs on
every post write.

The search index is built by the SQLite triggers in app/db/fts.py, which the
test database gets via the ``after_create`` metadata event (see conftest).
"""

from app.db.database import get_db
from app.models.post_hashtag import PostHashtag
from app.models.post_mention import PostMention
from app.repositories import user_repository
from fastapi.testclient import TestClient
from main import app


def _register(username: str) -> tuple[TestClient, int]:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201
    return client, response.json()["id"]


def _db():
    """A session on the test engine (the dependency override conftest installs)."""
    return next(app.dependency_overrides[get_db]())


def _post(client: TestClient, content: str) -> int:
    response = client.post("/api/v1/tweets", json={"content": content})
    assert response.status_code == 201
    return response.json()["id"]


def _search(client: TestClient, query: str, **params) -> dict:
    response = client.get("/api/v1/search", params={"q": query, **params})
    assert response.status_code == 200
    return response.json()


# --- search ----------------------------------------------------------------


def test_search_matches_post_content_and_ranks_by_relevance() -> None:
    alice, _ = _register("alice")
    dense = _post(alice, "python python python tips")
    sparse = _post(alice, "just a little python note")
    _post(alice, "completely unrelated cooking")

    data = _search(alice, "python")
    ids = [item["id"] for item in data["items"]]

    assert set(ids) == {dense, sparse}, "only the python posts match"
    assert ids[0] == dense, "the densest match ranks first (BM25)"


def test_search_paginates_by_cursor_without_gaps_or_dupes() -> None:
    alice, _ = _register("alice")
    posted = {_post(alice, f"alpha entry number {n}") for n in range(5)}

    seen: list[int] = []
    cursor = None
    for _ in range(10):  # generous upper bound; the loop breaks on exhaustion
        page = _search(alice, "alpha", limit=2, **({"cursor": cursor} if cursor else {}))
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert cursor is None, "pagination terminated"
    assert len(seen) == len(set(seen)), "no post repeated across pages"
    assert set(seen) == posted, "every matching post was returned exactly once"


def test_search_includes_replies_and_links_to_the_thread() -> None:
    alice, _ = _register("alice")
    tweet_id = _post(alice, "the original starfruit tweet")
    reply = alice.post(
        f"/api/v1/tweets/{tweet_id}/comments",
        json={"content": "a starfruit reply"},
    )
    assert reply.status_code == 201
    reply_id = reply.json()["id"]

    items = _search(alice, "starfruit")["items"]
    by_id = {item["id"]: item for item in items}

    assert tweet_id in by_id and reply_id in by_id, "both the tweet and the reply match"
    assert by_id[reply_id]["is_reply"] is True
    assert by_id[reply_id]["thread_id"] == tweet_id, "a reply links to its thread root"
    assert by_id[tweet_id]["is_reply"] is False
    assert by_id[tweet_id]["thread_id"] == tweet_id


def test_search_excludes_blocked_and_deleted_authors() -> None:
    alice, alice_id = _register("alice")
    bob, bob_id = _register("bob")
    carol, carol_id = _register("carol")

    bob_post = _post(bob, "shared keyword mango from bob")
    carol_post = _post(carol, "shared keyword mango from carol")

    # alice blocks bob -> his post drops out of her search.
    assert alice.post(f"/api/v1/blocks/{bob_id}").status_code in (200, 201)
    ids = {item["id"] for item in _search(alice, "mango")["items"]}
    assert bob_post not in ids
    assert carol_post in ids

    # carol deletes her account -> her post drops out too.
    db = _db()
    try:
        user_repository.soft_delete_user(db, carol_id, scrubbed_password_hash="x")
    finally:
        db.close()
    ids = {item["id"] for item in _search(alice, "mango")["items"]}
    assert carol_post not in ids


def test_blank_or_punctuation_only_query_returns_empty() -> None:
    alice, _ = _register("alice")
    _post(alice, "something searchable here")

    assert _search(alice, "!!! ??? ...")["items"] == []


# --- extraction on write ---------------------------------------------------


def test_write_extracts_hashtags_and_mentions_and_notifies() -> None:
    alice, _ = _register("alice")
    bob, bob_id = _register("bob")

    tweet_id = _post(alice, "hello @bob loving #FastAPI and #fastapi #Search")

    db = _db()
    try:
        tags = {row.tag for row in db.query(PostHashtag).filter_by(post_id=tweet_id)}
        mentioned = {
            row.mentioned_user_id
            for row in db.query(PostMention).filter_by(post_id=tweet_id)
        }
    finally:
        db.close()

    assert tags == {"fastapi", "search"}, "hashtags lowercased and de-duplicated"
    assert mentioned == {bob_id}

    # bob is notified of the mention, linked to the tweet.
    items = bob.get("/api/v1/notifications").json()["items"]
    mention = next(item for item in items if item["type"] == "mention")
    assert mention["actor"]["username"] == "alice"
    assert mention["tweet_id"] == tweet_id


def test_self_mention_does_not_notify() -> None:
    alice, _ = _register("alice")
    _post(alice, "talking to @alice myself")
    assert alice.get("/api/v1/notifications/unread-count").json()["count"] == 0


def test_edit_resyncs_entities_and_notifies_only_new_mentions() -> None:
    alice, _ = _register("alice")
    bob, bob_id = _register("bob")
    carol, carol_id = _register("carol")

    tweet_id = _post(alice, "first pass #alpha @bob")
    # Edit: drop #alpha for #beta, keep @bob, add @carol.
    edit = alice.patch(
        f"/api/v1/tweets/{tweet_id}",
        json={"content": "second pass #beta @bob @carol"},
    )
    assert edit.status_code == 200

    db = _db()
    try:
        tags = {row.tag for row in db.query(PostHashtag).filter_by(post_id=tweet_id)}
        mentioned = {
            row.mentioned_user_id
            for row in db.query(PostMention).filter_by(post_id=tweet_id)
        }
    finally:
        db.close()

    assert tags == {"beta"}, "old hashtag cleared, new one indexed"
    assert mentioned == {bob_id, carol_id}

    # bob was already mentioned before the edit -> still exactly one notification
    # (add_notification collapses on recipient+actor+type+post). carol is new.
    assert bob.get("/api/v1/notifications/unread-count").json()["count"] == 1
    assert carol.get("/api/v1/notifications/unread-count").json()["count"] == 1


def test_delete_cleans_up_entity_rows() -> None:
    alice, _ = _register("alice")
    _register("bob")

    tweet_id = _post(alice, "ephemeral #tag @bob content")
    assert alice.delete(f"/api/v1/tweets/{tweet_id}").status_code == 204

    db = _db()
    try:
        assert db.query(PostHashtag).filter_by(post_id=tweet_id).count() == 0
        assert db.query(PostMention).filter_by(post_id=tweet_id).count() == 0
    finally:
        db.close()

    # and it is gone from the search index (the AFTER DELETE trigger retracted it).
    assert _search(alice, "ephemeral")["items"] == []
