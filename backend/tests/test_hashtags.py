"""
The hashtag feed: GET /hashtags/{tag}/posts.

Unlike /search (an FTS text match), this keys on the structured post_hashtags
rows the write path records, so it returns exactly the top-level posts that used
the tag -- newest-first, with the timelines' cursor and visibility rules.
"""

from app.db.database import get_db
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
    return next(app.dependency_overrides[get_db]())


def _post(client: TestClient, content: str) -> int:
    response = client.post("/api/v1/tweets", json={"content": content})
    assert response.status_code == 201
    return response.json()["id"]


def _feed(client: TestClient, tag: str, **params) -> dict:
    response = client.get(f"/api/v1/hashtags/{tag}/posts", params=params)
    assert response.status_code == 200
    return response.json()


def test_feed_returns_only_exact_tag_top_level_posts_newest_first() -> None:
    alice, _ = _register("alice")
    first = _post(alice, "first #python post")
    second = _post(alice, "second #Python post")  # tag match is case-insensitive
    _post(alice, "mentions python as a plain word, no tag")  # FTS would match; feed must not
    _post(alice, "a #django post")  # different tag

    # a reply carrying the tag is excluded -- the feed is top-level posts.
    reply = alice.post(f"/api/v1/tweets/{first}/comments", json={"content": "reply #python"})
    assert reply.status_code == 201

    ids = [item["id"] for item in _feed(alice, "python")["items"]]
    assert ids == [second, first], "exactly the tagged tweets, newest-first"


def test_feed_normalizes_hash_prefix_and_case() -> None:
    alice, _ = _register("alice")
    tweet_id = _post(alice, "tagged #FastAPI here")

    # A leading %23 (#) and any casing resolve to the same stored tag.
    assert [i["id"] for i in _feed(alice, "%23fastapi")["items"]] == [tweet_id]
    assert [i["id"] for i in _feed(alice, "FASTAPI")["items"]] == [tweet_id]


def test_feed_paginates_by_cursor() -> None:
    alice, _ = _register("alice")
    posted = {_post(alice, f"#alpha entry {n}") for n in range(5)}

    seen: list[int] = []
    cursor = None
    for _ in range(10):
        page = _feed(alice, "alpha", limit=2, **({"cursor": cursor} if cursor else {}))
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert cursor is None
    assert len(seen) == len(set(seen)), "no post repeated across pages"
    assert set(seen) == posted


def test_feed_excludes_blocked_and_deleted_authors() -> None:
    alice, _ = _register("alice")
    bob, bob_id = _register("bob")
    carol, carol_id = _register("carol")

    bob_post = _post(bob, "bob's #mango")
    carol_post = _post(carol, "carol's #mango")

    assert alice.post(f"/api/v1/blocks/{bob_id}").status_code in (200, 201)
    ids = {item["id"] for item in _feed(alice, "mango")["items"]}
    assert bob_post not in ids and carol_post in ids

    db = _db()
    try:
        user_repository.soft_delete_user(db, carol_id, scrubbed_password_hash="x")
    finally:
        db.close()
    ids = {item["id"] for item in _feed(alice, "mango")["items"]}
    assert carol_post not in ids


def test_unknown_tag_is_empty_and_blank_tag_is_rejected() -> None:
    alice, _ = _register("alice")
    _post(alice, "something #real")

    assert _feed(alice, "doesnotexist")["items"] == []
    # A tag that normalizes to empty (just "#") is a bad request.
    assert alice.get("/api/v1/hashtags/%23/posts").status_code == 400
