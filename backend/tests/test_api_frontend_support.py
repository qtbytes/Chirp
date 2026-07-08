from conftest import TestingSessionLocal
from fastapi.testclient import TestClient
from main import app


def test_register_login_me_and_logout() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    assert response.status_code == 201
    assert response.json()["username"] == "alice"
    assert "twitter_session" in response.cookies

    me_response = client.get("/api/v1/auth/me")
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "alice"

    logout_response = client.post("/api/v1/auth/logout")
    assert logout_response.status_code == 204

    failed_me_response = client.get("/api/v1/auth/me")
    assert failed_me_response.status_code == 401

    login_response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "password123"},
    )
    assert login_response.status_code == 200
    assert login_response.json()["username"] == "alice"


def test_register_rejects_reserved_username() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "search", "password": "password123"},
    )
    assert response.status_code == 422


def test_login_rejects_bad_password() -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    client.post("/api/v1/auth/logout")

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "wrongpass"},
    )

    assert response.status_code == 401


def test_user_discovery_includes_follow_state() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    alice.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    bob_response = bob.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123"},
    )
    bob_id = bob_response.json()["id"]

    follow_response = alice.post(f"/api/v1/follows/{bob_id}")
    assert follow_response.status_code == 200

    response = alice.get("/api/v1/users")
    assert response.status_code == 200
    users = {user["username"]: user for user in response.json()}
    assert users["alice"]["is_current_user"] is True
    assert users["bob"]["is_following"] is True


def test_for_you_lists_latest_first_then_score() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    carol = TestClient(app)
    alice.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    bob.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123"},
    )
    carol.post(
        "/api/v1/auth/register",
        json={"username": "carol", "password": "password123"},
    )

    first_tweet = alice.post("/api/v1/tweets", json={"content": "first"}).json()
    latest_tweet = bob.post("/api/v1/tweets", json={"content": "latest"}).json()

    carol.post(f"/api/v1/tweets/{first_tweet['id']}/likes")
    carol.post(
        f"/api/v1/tweets/{first_tweet['id']}/comments",
        json={"content": "reply"},
    )

    response = alice.get("/api/v1/timeline/for-you")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items[:2]] == [latest_tweet["id"], first_tweet["id"]]
    assert response.json()["strategy"] == "for_you"


def test_for_you_uses_score_when_created_at_ties() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    carol = TestClient(app)
    alice.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    bob.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123"},
    )
    carol.post(
        "/api/v1/auth/register",
        json={"username": "carol", "password": "password123"},
    )

    low_score = alice.post("/api/v1/tweets", json={"content": "low"}).json()
    high_score = bob.post("/api/v1/tweets", json={"content": "high"}).json()
    carol.post(f"/api/v1/tweets/{high_score['id']}/likes")

    with TestingSessionLocal() as db:
        from app.models.post import Post

        same_created_at = db.get(Post, low_score["id"]).created_at
        db.get(Post, high_score["id"]).created_at = same_created_at
        db.commit()

    response = alice.get("/api/v1/timeline/for-you")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items[:2]] == [high_score["id"], low_score["id"]]


def test_session_can_create_tweet() -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )

    response = client.post("/api/v1/tweets", json={"content": "hello"})

    assert response.status_code == 201
    assert response.json()["author"]["username"] == "alice"


def test_retweet_action_updates_timeline_count() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    alice.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    bob.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123"},
    )

    tweet = alice.post("/api/v1/tweets", json={"content": "retweet me"}).json()
    response = bob.post(f"/api/v1/tweets/{tweet['id']}/retweets")
    assert response.status_code == 201
    assert response.json()["created"] is True

    duplicate_response = bob.post(f"/api/v1/tweets/{tweet['id']}/retweets")
    assert duplicate_response.status_code == 201
    assert duplicate_response.json()["created"] is False

    timeline_response = bob.get("/api/v1/timeline/for-you")
    assert timeline_response.status_code == 200
    timeline_tweet = next(
        item for item in timeline_response.json()["items"] if item["id"] == tweet["id"]
    )
    assert timeline_tweet["retweet_count"] == 1


def test_tweet_like_toggle_updates_state_and_count() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    alice.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    bob.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123"},
    )
    tweet = alice.post("/api/v1/tweets", json={"content": "like me"}).json()

    liked_response = bob.post(f"/api/v1/tweets/{tweet['id']}/likes/toggle")
    unliked_response = bob.post(f"/api/v1/tweets/{tweet['id']}/likes/toggle")

    assert liked_response.status_code == 200
    assert liked_response.json()["liked"] is True
    assert liked_response.json()["like_count"] == 1
    assert unliked_response.status_code == 200
    assert unliked_response.json()["liked"] is False
    assert unliked_response.json()["like_count"] == 0


def test_tweet_stats_endpoint_returns_counts_and_current_user_state() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    alice.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    bob.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123"},
    )
    tweet = alice.post("/api/v1/tweets", json={"content": "stats"}).json()

    bob.post(f"/api/v1/tweets/{tweet['id']}/likes/toggle")
    bob.post(f"/api/v1/tweets/{tweet['id']}/retweets")
    bob.post(
        f"/api/v1/tweets/{tweet['id']}/comments",
        json={"content": "reply"},
    )

    response = bob.get(f"/api/v1/tweets/stats?ids={tweet['id']}")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": tweet["id"],
            "like_count": 1,
            "comment_count": 1,
            "retweet_count": 1,
            "liked_by_me": True,
        }
    ]


def test_comment_stats_endpoint_returns_counts_and_current_user_state() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    alice.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    bob.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123"},
    )
    tweet = alice.post("/api/v1/tweets", json={"content": "comments"}).json()
    comment = bob.post(
        f"/api/v1/tweets/{tweet['id']}/comments",
        json={"content": "first"},
    ).json()

    alice.post(f"/api/v1/comments/{comment['id']}/likes/toggle")
    alice.post(f"/api/v1/comments/{comment['id']}/retweets")
    alice.post(
        f"/api/v1/comments/{comment['id']}/comments",
        json={"content": "reply"},
    )

    response = alice.get(f"/api/v1/comments/stats?ids={comment['id']}")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": comment["id"],
            "like_count": 1,
            "comment_count": 1,
            "retweet_count": 1,
            "liked_by_me": True,
        }
    ]


def test_thread_comments_are_nested_in_preorder() -> None:
    client = TestClient(app)
    client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    tweet = client.post("/api/v1/tweets", json={"content": "root"}).json()

    c1 = client.post(
        f"/api/v1/tweets/{tweet['id']}/comments", json={"content": "c1"}
    ).json()
    c2 = client.post(
        f"/api/v1/tweets/{tweet['id']}/comments", json={"content": "c2"}
    ).json()
    # A reply to c1, created AFTER the later top-level c2.
    client.post(
        f"/api/v1/comments/{c1['id']}/comments", json={"content": "reply-to-c1"}
    )

    listed = client.get(f"/api/v1/tweets/{tweet['id']}/comments").json()
    # Nested pre-order: the reply nests directly under c1, before the later c2,
    # rather than at the chronological end.
    assert [c["content"] for c in listed] == ["c1", "reply-to-c1", "c2"]
    reply_item = next(c for c in listed if c["content"] == "reply-to-c1")
    assert reply_item["parent_comment_id"] == c1["id"]


def test_comment_interactions_update_comment_counts() -> None:
    alice = TestClient(app)
    bob = TestClient(app)
    alice.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "password123"},
    )
    bob.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "password123"},
    )

    tweet = alice.post("/api/v1/tweets", json={"content": "thread"}).json()
    comment = bob.post(
        f"/api/v1/tweets/{tweet['id']}/comments",
        json={"content": "first"},
    ).json()

    like_response = alice.post(f"/api/v1/comments/{comment['id']}/likes/toggle")
    unlike_response = alice.post(f"/api/v1/comments/{comment['id']}/likes/toggle")
    second_like_response = alice.post(f"/api/v1/comments/{comment['id']}/likes/toggle")
    retweet_response = alice.post(f"/api/v1/comments/{comment['id']}/retweets")
    reply_response = alice.post(
        f"/api/v1/comments/{comment['id']}/comments",
        json={"content": "reply"},
    )

    assert like_response.status_code == 200
    assert like_response.json()["liked"] is True
    assert like_response.json()["like_count"] == 1
    assert unlike_response.status_code == 200
    assert unlike_response.json()["liked"] is False
    assert unlike_response.json()["like_count"] == 0
    assert second_like_response.status_code == 200
    assert second_like_response.json()["liked"] is True
    assert second_like_response.json()["like_count"] == 1
    assert retweet_response.status_code == 201
    assert reply_response.status_code == 201
    assert reply_response.json()["parent_comment_id"] == comment["id"]

    comments_response = alice.get(f"/api/v1/tweets/{tweet['id']}/comments")
    assert comments_response.status_code == 200
    comments = {item["id"]: item for item in comments_response.json()}
    assert comments[comment["id"]]["like_count"] == 1
    assert comments[comment["id"]]["liked_by_me"] is True
    assert comments[comment["id"]]["comment_count"] == 1
    assert comments[comment["id"]]["retweet_count"] == 1
    assert [item["id"] for item in comments_response.json()] == [
        comment["id"],
        reply_response.json()["id"],
    ]
