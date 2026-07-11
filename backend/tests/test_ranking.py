"""
"For you" ranking.

Two layers: the pure scorer (numbers -> score, no DB or clock), and the feed
end to end (decay, affinity, and stable ranked pagination through the API).
"""

from datetime import datetime, timedelta, timezone

import pytest
from app.core.config import settings
from app.models.post import Post
from app.services.ranking import RankingWeights, score_tweet, weights_from_settings
from conftest import TestingSessionLocal
from fastapi.testclient import TestClient
from main import app

# ------------------------------------------------------------- the pure scorer

W = RankingWeights(
    base=1.0,
    like=3.0,
    retweet=4.0,
    comment=5.0,
    half_life_seconds=3600.0,
    follow_boost=1.5,
    like_affinity=0.1,
    like_affinity_cap=5,
)


def score(**overrides) -> float:
    args = dict(
        like_count=0,
        retweet_count=0,
        comment_count=0,
        age_seconds=0.0,
        follows_author=False,
        viewer_likes_on_author=0,
        weights=W,
    )
    args.update(overrides)
    return score_tweet(**args)


def test_engagement_raises_the_score_by_its_weights() -> None:
    assert score(like_count=1) > score()
    # comment is weighted heavier than like, which is heavier than nothing.
    assert score(comment_count=1) > score(like_count=1) > score()


def test_engagement_halves_at_one_half_life() -> None:
    fresh = score(like_count=10)
    aged = score(like_count=10, age_seconds=W.half_life_seconds)
    assert aged == pytest.approx(0.5 * fresh)


def test_an_older_engaged_post_can_outscore_a_fresh_empty_one() -> None:
    # The property a created_at|id sort can never express.
    engaged_but_old = score(like_count=10, age_seconds=W.half_life_seconds)
    fresh_but_empty = score()
    assert engaged_but_old > fresh_but_empty


def test_following_the_author_lifts_the_score() -> None:
    assert score(like_count=1, follows_author=True) == pytest.approx(
        (1 + W.follow_boost) * score(like_count=1)
    )


def test_like_affinity_lifts_the_score_but_is_capped() -> None:
    assert score(like_count=1, viewer_likes_on_author=3) > score(like_count=1)
    # Past the cap, more history adds nothing, so one author cannot run away.
    assert score(like_count=1, viewer_likes_on_author=1000) == score(
        like_count=1, viewer_likes_on_author=W.like_affinity_cap
    )


def test_a_future_timestamp_is_clamped_not_amplified() -> None:
    assert score(like_count=5, age_seconds=-9999.0) == score(like_count=5, age_seconds=0.0)


def test_weights_come_from_settings() -> None:
    weights = weights_from_settings()
    assert weights.like == settings.ranking_like_weight
    assert weights.half_life_seconds == settings.ranking_half_life_hours * 3600.0
    assert weights.like_affinity_cap == settings.ranking_like_affinity_cap


# ------------------------------------------------------------- the feed, e2e


def register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _backdate(post_id: int, hours: float) -> None:
    with TestingSessionLocal() as db:
        post = db.get(Post, post_id)
        post.created_at = datetime.now(timezone.utc) - timedelta(hours=hours)
        db.commit()


def _for_you_ids(client: TestClient, **params) -> list[int]:
    response = client.get("/api/v1/timeline/for-you", params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_for_you_lifts_authors_the_viewer_follows() -> None:
    alice, bob, carol = TestClient(app), TestClient(app), TestClient(app)
    register(alice, "alice")
    b = register(bob, "bob")
    register(carol, "carol")

    alice.post(f"/api/v1/follows/{b['id']}")

    # Two equally fresh, equally empty posts. The only difference the ranker can
    # see is that alice follows bob, so his must come first.
    bob_tweet = bob.post("/api/v1/tweets", json={"content": "from someone you follow"}).json()
    carol_tweet = carol.post("/api/v1/tweets", json={"content": "from a stranger"}).json()

    order = _for_you_ids(alice)
    assert order.index(bob_tweet["id"]) < order.index(carol_tweet["id"])


def test_for_you_surfaces_an_old_but_engaged_post_over_a_fresh_empty_one() -> None:
    viewer, bob, carol, dave = (
        TestClient(app),
        TestClient(app),
        TestClient(app),
        TestClient(app),
    )
    register(viewer, "viewer")  # follows nobody, likes nobody: affinity is neutral
    register(bob, "bob")
    register(carol, "carol")
    register(dave, "dave")

    loved = bob.post("/api/v1/tweets", json={"content": "a day old, still loved"}).json()
    carol.post(f"/api/v1/tweets/{loved['id']}/likes")
    dave.post(f"/api/v1/tweets/{loved['id']}/likes")
    _backdate(loved["id"], hours=24)

    fresh = carol.post("/api/v1/tweets", json={"content": "brand new, ignored"}).json()

    order = _for_you_ids(viewer)
    assert order.index(loved["id"]) < order.index(fresh["id"]), (
        "decayed engagement should still beat a fresh post nobody touched"
    )


def test_for_you_paginates_a_stable_ranked_order() -> None:
    viewer, bob, carol, dave = (
        TestClient(app),
        TestClient(app),
        TestClient(app),
        TestClient(app),
    )
    register(viewer, "viewer")
    register(bob, "bob")
    register(carol, "carol")
    register(dave, "dave")

    # A spread of engagement so scores are distinct and the order is meaningful.
    authors = [bob, carol, dave, bob, carol, dave]
    likers = [carol, dave, bob]
    for index, author in enumerate(authors):
        tweet = author.post("/api/v1/tweets", json={"content": f"post {index}"}).json()
        for liker in likers[: index % 4]:
            liker.post(f"/api/v1/tweets/{tweet['id']}/likes")

    full = _for_you_ids(viewer, limit=50)

    paged: list[int] = []
    cursor: str | None = None
    for _ in range(20):  # guard against a non-terminating cursor
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = viewer.get("/api/v1/timeline/for-you", params=params)
        assert response.status_code == 200
        body = response.json()
        paged.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            break

    assert paged == full, "paging must reproduce the single-page ranked order"
    assert len(paged) == len(set(paged)), "no post appears twice across pages"


def test_for_you_rejects_a_malformed_cursor() -> None:
    viewer = TestClient(app)
    register(viewer, "viewer")
    assert (
        viewer.get("/api/v1/timeline/for-you", params={"cursor": "garbage"}).status_code
        == 400
    )
