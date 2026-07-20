"""
Pinning a post to a profile.

A user may pin one of their own top-level tweets; it rides at the top of their
profile (returned as ``pinned_tweet``) and is excluded from the chronological
list so it never shows twice. Pinning replaces any previous pin. Only the author
may pin, only a top-level tweet, and the pin respects the tweet's audience for
whoever is viewing.
"""

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


def _post(client: TestClient, content: str, **extra) -> int:
    return client.post(
        "/api/v1/tweets", json={"content": content, **extra}
    ).json()["id"]


def _pin(client: TestClient, tweet_id: int):
    return client.put(f"/api/v1/tweets/{tweet_id}/pin")


def _unpin(client: TestClient, tweet_id: int):
    return client.delete(f"/api/v1/tweets/{tweet_id}/pin")


def _profile_tweets(client: TestClient, username: str, cursor: str | None = None):
    params = {"cursor": cursor} if cursor else {}
    return client.get(f"/api/v1/users/{username}/tweets", params=params).json()


def test_pinning_surfaces_the_tweet_and_removes_it_from_the_list() -> None:
    alice, _ = register("alice")
    older = _post(alice, "an older tweet")
    pinned = _post(alice, "pin me")

    assert _pin(alice, pinned).status_code == 204

    page = _profile_tweets(alice, "alice")
    assert page["pinned_tweet"] is not None
    assert page["pinned_tweet"]["id"] == pinned
    # The pinned tweet is not duplicated in the chronological list.
    list_ids = [item["id"] for item in page["items"]]
    assert pinned not in list_ids
    assert older in list_ids


def test_pinned_tweet_only_appears_on_the_first_page() -> None:
    alice, _ = register("alice")
    pinned = _post(alice, "pinned")
    for n in range(3):
        _post(alice, f"filler {n}")
    _pin(alice, pinned)

    first = _profile_tweets(alice, "alice")
    assert first["pinned_tweet"] is not None
    # Page by a tiny limit so a second page exists.
    first = alice.get(
        "/api/v1/users/alice/tweets", params={"limit": 1}
    ).json()
    assert first["pinned_tweet"] is not None
    assert first["next_cursor"] is not None

    second = alice.get(
        "/api/v1/users/alice/tweets",
        params={"limit": 1, "cursor": first["next_cursor"]},
    ).json()
    assert second["pinned_tweet"] is None
    # ...and it never shows in the list on later pages either.
    assert all(item["id"] != pinned for item in second["items"])


def test_pinning_replaces_the_previous_pin() -> None:
    alice, _ = register("alice")
    first = _post(alice, "first pin")
    second = _post(alice, "second pin")

    _pin(alice, first)
    _pin(alice, second)

    page = _profile_tweets(alice, "alice")
    assert page["pinned_tweet"]["id"] == second
    # The formerly pinned tweet is back in the chronological list.
    assert first in [item["id"] for item in page["items"]]


def test_unpinning_clears_it() -> None:
    alice, _ = register("alice")
    tweet_id = _post(alice, "pin then unpin")
    _pin(alice, tweet_id)
    assert _profile_tweets(alice, "alice")["pinned_tweet"] is not None

    assert _unpin(alice, tweet_id).status_code == 204
    page = _profile_tweets(alice, "alice")
    assert page["pinned_tweet"] is None
    assert tweet_id in [item["id"] for item in page["items"]]


def test_unpin_is_scoped_and_idempotent() -> None:
    alice, _ = register("alice")
    pinned = _post(alice, "the real pin")
    other = _post(alice, "not pinned")
    _pin(alice, pinned)

    # Unpinning a tweet that isn't the current pin must not clear the real one.
    assert _unpin(alice, other).status_code == 204
    assert _profile_tweets(alice, "alice")["pinned_tweet"]["id"] == pinned


def test_cannot_pin_someone_elses_tweet() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    bob_tweet = _post(bob, "bob's tweet")

    assert _pin(alice, bob_tweet).status_code == 403
    assert _profile_tweets(alice, "alice")["pinned_tweet"] is None


def test_cannot_pin_a_reply() -> None:
    alice, _ = register("alice")
    tweet_id = _post(alice, "root")
    reply = alice.post(
        f"/api/v1/tweets/{tweet_id}/comments", json={"content": "a reply"}
    ).json()["id"]

    assert _pin(alice, reply).status_code == 404


def test_pin_unknown_tweet_is_404() -> None:
    alice, _ = register("alice")
    assert _pin(alice, 999999).status_code == 404


def test_deleting_the_pinned_tweet_clears_the_pin() -> None:
    alice, _ = register("alice")
    tweet_id = _post(alice, "doomed pin")
    _pin(alice, tweet_id)

    assert alice.delete(f"/api/v1/tweets/{tweet_id}").status_code == 204
    # No dangling pin: the profile shows no pinned tweet.
    assert _profile_tweets(alice, "alice")["pinned_tweet"] is None


def test_others_see_your_pinned_tweet() -> None:
    alice, _ = register("alice")
    bob, _ = register("bob")
    pinned = _post(alice, "look at this")
    _pin(alice, pinned)

    page = _profile_tweets(bob, "alice")
    assert page["pinned_tweet"] is not None
    assert page["pinned_tweet"]["id"] == pinned


def test_a_followers_only_pin_is_hidden_from_a_non_follower() -> None:
    alice, _ = register("alice")
    stranger, _ = register("stranger")
    pinned = _post(alice, "followers only", visibility="followers")
    _pin(alice, pinned)

    # The author still sees their own pin...
    assert _profile_tweets(alice, "alice")["pinned_tweet"]["id"] == pinned
    # ...but a non-follower does not.
    assert _profile_tweets(stranger, "alice")["pinned_tweet"] is None
