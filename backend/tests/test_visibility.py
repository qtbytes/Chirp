"""
Per-tweet audience visibility: ``public`` / ``followers`` / ``private``.

Visibility is a property of a tweet's thread root and is enforced on every read
surface -- home and "for you" timelines, a profile, a single tweet, search, the
hashtag feed -- plus quote embeds, replies, trending and notifications. These
tests drive the HTTP API with three real sessions (author, a follower, a
stranger) so the gate is exercised end to end.
"""

from app.core.config import settings
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
    assert response.status_code == 201, response.text
    return client, response.json()["id"]


def _follow(follower: TestClient, followee_id: int) -> None:
    response = follower.post(f"/api/v1/follows/{followee_id}")
    assert response.status_code == 200, response.text


def _post(client: TestClient, content: str, visibility: str = "public") -> dict:
    response = client.post(
        "/api/v1/tweets", json={"content": content, "visibility": visibility}
    )
    assert response.status_code == 201, response.text
    return response.json()


def _ids(items: list[dict]) -> set[int]:
    return {item["id"] for item in items}


def _edit(client: TestClient, tweet_id: int, content: str, visibility: str | None = None):
    body: dict = {"content": content}
    if visibility is not None:
        body["visibility"] = visibility
    return client.patch(f"/api/v1/tweets/{tweet_id}", json=body)


# --------------------------------------------------------------- the stored value


def test_created_tweet_echoes_its_visibility_and_defaults_to_public() -> None:
    alice, _ = _register("alice")
    assert _post(alice, "hi")["visibility"] == "public"
    assert _post(alice, "close friends", "followers")["visibility"] == "followers"
    assert _post(alice, "diary", "private")["visibility"] == "private"

    # An unknown audience is not stored as an unenforceable value; it falls back.
    response = alice.post(
        "/api/v1/tweets", json={"content": "x", "visibility": "nonsense"}
    )
    assert response.status_code == 422  # schema rejects it outright


# --------------------------------------------------------------- single tweet GET


def test_single_tweet_visibility_by_viewer() -> None:
    alice, alice_id = _register("alice")
    follower, _ = _register("follower")
    stranger, _ = _register("stranger")
    _follow(follower, alice_id)

    public = _post(alice, "everyone")["id"]
    followers = _post(alice, "my followers", "followers")["id"]
    private = _post(alice, "just me", "private")["id"]

    def status(client: TestClient, tweet_id: int) -> int:
        return client.get(f"/api/v1/tweets/{tweet_id}").status_code

    # The author sees all three of their own.
    for tweet_id in (public, followers, private):
        assert status(alice, tweet_id) == 200

    # A follower sees public + followers, but not the private one.
    assert status(follower, public) == 200
    assert status(follower, followers) == 200
    assert status(follower, private) == 404

    # A stranger sees only the public one; the rest are 404 (not disclosed).
    assert status(stranger, public) == 200
    assert status(stranger, followers) == 404
    assert status(stranger, private) == 404


# ------------------------------------------------------------------- the feeds


def test_profile_tweets_are_filtered_for_the_viewer() -> None:
    alice, alice_id = _register("alice")
    follower, _ = _register("follower")
    stranger, _ = _register("stranger")
    _follow(follower, alice_id)

    public = _post(alice, "everyone")["id"]
    followers = _post(alice, "followers only", "followers")["id"]
    private = _post(alice, "only me", "private")["id"]

    def profile_ids(client: TestClient) -> set[int]:
        response = client.get("/api/v1/users/alice/tweets")
        assert response.status_code == 200
        return _ids(response.json()["items"])

    assert profile_ids(alice) == {public, followers, private}
    assert profile_ids(follower) == {public, followers}
    assert profile_ids(stranger) == {public}


def test_home_timeline_respects_visibility() -> None:
    alice, alice_id = _register("alice")
    follower, _ = _register("follower")
    _follow(follower, alice_id)

    public = _post(alice, "everyone")["id"]
    followers = _post(alice, "followers only", "followers")["id"]
    _post(alice, "only me", "private")

    response = follower.get("/api/v1/timeline/home")
    assert response.status_code == 200
    # The follower's home shows alice's public + followers tweets, never private.
    assert _ids(response.json()["items"]) == {public, followers}


def test_for_you_timeline_respects_visibility() -> None:
    alice, alice_id = _register("alice")
    stranger, _ = _register("stranger")

    public = _post(alice, "everyone")["id"]
    _post(alice, "followers only", "followers")
    _post(alice, "only me", "private")

    # A stranger's "for you" pool only surfaces the public tweet.
    response = stranger.get("/api/v1/timeline/for-you")
    assert response.status_code == 200
    assert _ids(response.json()["items"]) == {public}


# ---------------------------------------------------------------------- search


def test_search_respects_visibility() -> None:
    alice, alice_id = _register("alice")
    follower, _ = _register("follower")
    stranger, _ = _register("stranger")
    _follow(follower, alice_id)

    public = _post(alice, "quokka everyone")["id"]
    followers = _post(alice, "quokka followers", "followers")["id"]
    private = _post(alice, "quokka private", "private")["id"]

    def found(client: TestClient, sort: str) -> set[int]:
        response = client.get("/api/v1/search", params={"q": "quokka", "sort": sort})
        assert response.status_code == 200
        return _ids(response.json()["items"])

    for sort in ("relevance", "recent"):
        assert found(alice, sort) == {public, followers, private}  # own, all of it
        assert found(follower, sort) == {public, followers}
        assert found(stranger, sort) == {public}


def test_search_gates_a_reply_on_its_root_tweet() -> None:
    # A reply has no audience of its own; searching its text must respect the
    # restricted root it lives under.
    alice, alice_id = _register("alice")
    stranger, _ = _register("stranger")

    root = _post(alice, "root", "followers")["id"]
    reply = alice.post(
        f"/api/v1/tweets/{root}/comments", json={"content": "narwhal reply"}
    )
    assert reply.status_code == 201

    # The author finds their reply; the stranger (who can't see the root) does not.
    assert _ids(
        alice.get("/api/v1/search", params={"q": "narwhal"}).json()["items"]
    ) == {reply.json()["id"]}
    assert stranger.get("/api/v1/search", params={"q": "narwhal"}).json()["items"] == []


# -------------------------------------------------------------------- hashtag feed


def test_hashtag_feed_respects_visibility() -> None:
    alice, alice_id = _register("alice")
    stranger, _ = _register("stranger")

    public = _post(alice, "public #wombat")["id"]
    _post(alice, "followers #wombat", "followers")
    _post(alice, "private #wombat", "private")

    response = stranger.get("/api/v1/hashtags/wombat/posts")
    assert response.status_code == 200
    assert _ids(response.json()["items"]) == {public}


def test_trending_counts_public_tweets_only() -> None:
    settings_min = settings.trending_min_posts
    settings.trending_min_posts = 1
    try:
        alice, _ = _register("alice")
        _post(alice, "public #zebra")
        _post(alice, "followers #zebra", "followers")
        _post(alice, "private #zebra", "private")

        response = alice.get("/api/v1/hashtags/trending")
        assert response.status_code == 200
        row = next(r for r in response.json() if r["tag"] == "zebra")
        # Only the single public tweet feeds the trending count.
        assert row["post_count"] == 1
    finally:
        settings.trending_min_posts = settings_min


# ---------------------------------------------------------------------- replies


def test_cannot_reply_to_a_thread_you_cannot_see() -> None:
    alice, alice_id = _register("alice")
    stranger, _ = _register("stranger")
    tweet = _post(alice, "followers only", "followers")["id"]

    # A stranger can't reach the thread, so replying is "not found" too.
    response = stranger.post(
        f"/api/v1/tweets/{tweet}/comments", json={"content": "hi"}
    )
    assert response.status_code == 404

    # Listing the thread's comments is likewise gated.
    assert stranger.get(f"/api/v1/tweets/{tweet}/comments").status_code == 404


def test_reply_feed_hides_replies_into_restricted_threads() -> None:
    alice, alice_id = _register("alice")
    bob, bob_id = _register("bob")
    stranger, _ = _register("stranger")
    _follow(bob, alice_id)

    followers_tweet = _post(alice, "followers only", "followers")["id"]
    public_tweet = _post(alice, "everyone")["id"]
    # bob follows alice, so bob may reply to both.
    r1 = bob.post(
        f"/api/v1/tweets/{followers_tweet}/comments", json={"content": "secret reply"}
    )
    r2 = bob.post(
        f"/api/v1/tweets/{public_tweet}/comments", json={"content": "open reply"}
    )
    assert r1.status_code == 201 and r2.status_code == 201

    # A stranger browsing bob's replies sees only the reply into the public thread.
    response = stranger.get("/api/v1/users/bob/replies")
    assert response.status_code == 200
    reply_ids = {item["comment"]["id"] for item in response.json()["items"]}
    assert reply_ids == {r2.json()["id"]}


# ----------------------------------------------------------------------- quotes


def test_cannot_quote_a_tweet_you_cannot_see() -> None:
    alice, alice_id = _register("alice")
    stranger, _ = _register("stranger")
    tweet = _post(alice, "followers only", "followers")["id"]

    response = stranger.post(
        "/api/v1/tweets", json={"content": "look at this", "quoted_post_id": tweet}
    )
    assert response.status_code == 404


def test_quote_embed_is_hidden_from_viewers_who_cannot_see_the_quoted_tweet() -> None:
    alice, alice_id = _register("alice")
    bob, bob_id = _register("bob")
    carol, _ = _register("carol")
    _follow(bob, alice_id)  # bob may see alice's followers-only tweet
    _follow(carol, bob_id)  # carol follows bob but NOT alice

    restricted = _post(alice, "followers only", "followers")["id"]
    quote = bob.post(
        "/api/v1/tweets",
        json={"content": "check this out", "quoted_post_id": restricted},
    )
    assert quote.status_code == 201
    quote_id = quote.json()["id"]

    # bob authored the quote and can see the embed.
    assert bob.get(f"/api/v1/tweets/{quote_id}").json()["quoted_post"] is not None

    # carol can see bob's quote but not the embedded followers-only tweet.
    carol_view = carol.get(f"/api/v1/tweets/{quote_id}")
    assert carol_view.status_code == 200
    assert carol_view.json()["quoted_post"] is None


# ------------------------------------------------------------------ notifications


def _notifications(client: TestClient) -> list[dict]:
    response = client.get("/api/v1/notifications")
    assert response.status_code == 200
    return response.json()["items"]


def test_private_tweet_mention_notifies_nobody() -> None:
    alice, _ = _register("alice")
    bob, _ = _register("bob")

    _post(alice, "psst @bob", "private")

    assert [n for n in _notifications(bob) if n["type"] == "mention"] == []


def test_followers_only_mention_is_filtered_until_you_follow() -> None:
    alice, alice_id = _register("alice")
    bob, bob_id = _register("bob")

    _post(alice, "hey @bob", "followers")

    # bob does not follow alice yet: the mention exists but is filtered from his
    # list because he can't see the followers-only tweet it points at.
    assert [n for n in _notifications(bob) if n["type"] == "mention"] == []

    # Once bob follows alice, the same notification becomes visible.
    _follow(bob, alice_id)
    mentions = [n for n in _notifications(bob) if n["type"] == "mention"]
    assert len(mentions) == 1
    assert mentions[0]["actor"]["username"] == "alice"


def test_public_tweet_mention_notifies_normally() -> None:
    alice, _ = _register("alice")
    bob, _ = _register("bob")

    _post(alice, "hello @bob")
    mentions = [n for n in _notifications(bob) if n["type"] == "mention"]
    assert len(mentions) == 1


# ------------------------------------------------------------------ editing


def test_editing_visibility_changes_who_can_see() -> None:
    alice, _ = _register("alice")
    stranger, _ = _register("stranger")
    tweet = _post(alice, "hello world")["id"]

    assert stranger.get(f"/api/v1/tweets/{tweet}").status_code == 200

    # Narrow it: the stranger loses access, and the edit echoes the new audience.
    narrowed = _edit(alice, tweet, "hello world", "followers")
    assert narrowed.status_code == 200
    assert narrowed.json()["visibility"] == "followers"
    assert stranger.get(f"/api/v1/tweets/{tweet}").status_code == 404

    # Widen it again: the stranger can see it once more.
    widened = _edit(alice, tweet, "hello world", "public")
    assert widened.status_code == 200
    assert stranger.get(f"/api/v1/tweets/{tweet}").status_code == 200


def test_content_only_edit_preserves_visibility() -> None:
    # The footgun guard: editing text without sending an audience must not reset
    # a restricted tweet back to public.
    alice, _ = _register("alice")
    stranger, _ = _register("stranger")
    tweet = _post(alice, "first", "followers")["id"]

    edited = _edit(alice, tweet, "first, revised")  # no visibility field
    assert edited.status_code == 200
    assert edited.json()["visibility"] == "followers"
    assert stranger.get(f"/api/v1/tweets/{tweet}").status_code == 404


def test_making_a_private_mention_public_notifies_the_mentioned_user() -> None:
    alice, _ = _register("alice")
    bob, _ = _register("bob")

    tweet = _post(alice, "hey @bob", "private")["id"]
    assert [n for n in _notifications(bob) if n["type"] == "mention"] == []

    # Re-syncing entities on the edit fires the mention now that bob can see it.
    assert _edit(alice, tweet, "hey @bob", "public").status_code == 200
    mentions = [n for n in _notifications(bob) if n["type"] == "mention"]
    assert len(mentions) == 1
