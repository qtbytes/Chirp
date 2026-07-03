from collections.abc import Generator

from app.core.config import settings
from app.db.database import Base, get_db
from main import app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    class_=Session,
)


def override_get_db() -> Generator[Session, None, None]:
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
settings.rate_limit_enabled = False


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


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


def test_for_you_scores_global_tweets() -> None:
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

    plain_tweet = alice.post("/api/v1/tweets", json={"content": "plain"}).json()
    scored_tweet = bob.post("/api/v1/tweets", json={"content": "scored"}).json()

    carol.post(f"/api/v1/tweets/{scored_tweet['id']}/likes")
    carol.post(
        f"/api/v1/tweets/{scored_tweet['id']}/comments",
        json={"content": "reply"},
    )

    response = alice.get("/api/v1/timeline/for-you")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["id"] for item in items[:2]] == [scored_tweet["id"], plain_tweet["id"]]
    assert response.json()["strategy"] == "for_you"


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

    like_response = alice.post(f"/api/v1/comments/{comment['id']}/likes")
    retweet_response = alice.post(f"/api/v1/comments/{comment['id']}/retweets")
    reply_response = alice.post(
        f"/api/v1/comments/{comment['id']}/comments",
        json={"content": "reply"},
    )

    assert like_response.status_code == 201
    assert retweet_response.status_code == 201
    assert reply_response.status_code == 201
    assert reply_response.json()["parent_comment_id"] == comment["id"]

    comments_response = alice.get(f"/api/v1/tweets/{tweet['id']}/comments")
    assert comments_response.status_code == 200
    comments = {item["id"]: item for item in comments_response.json()}
    assert comments[comment["id"]]["like_count"] == 1
    assert comments[comment["id"]]["comment_count"] == 1
    assert comments[comment["id"]]["retweet_count"] == 1
