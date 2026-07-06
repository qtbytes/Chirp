# Profile Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Twitter-style user profiles — view any user's profile with Tweets/Replies tabs, edit your own (avatar upload + bio) — with the frontend restructured onto react-router URLs.

**Architecture:** Backend adds nullable `bio`/`avatar_url` columns to `User`, five profile endpoints under `/users`, one `GET /tweets/{tweet_id}` endpoint, and a static `/uploads` mount for avatar files. Frontend replaces state-based view switching with `react-router-dom` routes (`/`, `/tweet/:tweetId`, `/profile/:username`, `/profile/:username/replies`), extracts shared components out of the oversized `App.tsx`, and adds `ProfileView` + `EditProfileModal`.

**Tech Stack:** FastAPI + SQLAlchemy 2 + SQLite (backend, managed with `uv`), React 19 + Vite + TypeScript + react-router-dom v7 (frontend, managed with `npm`), lucide-react icons, plain CSS in `frontend/src/styles.css`.

**Spec:** `docs/superpowers/specs/2026-07-06-profile-page-design.md`

## Global Constraints

- Backend commands run from `backend/`; use `uv` (`uv add`, `uv run pytest`). Python >= 3.12.
- Frontend commands run from `frontend/`; use `npm`. Verify with `npm run typecheck`.
- Bio max length: **160** characters. Avatar upload: **JPEG/PNG/WebP only, max 2 MB**.
- `avatar_url` is stored as a server-relative path (e.g. `/uploads/avatars/3.png?v=1751791000`); the frontend prefixes the backend origin.
- Commit messages follow the repo convention: `feat: ...`, `fix: ...`, `feat(frontend): ...`, `test: ...`.
- The SQLite dev schema auto-adds **nullable** columns on startup (`app/db/dev_schema.py`) — new `User` columns MUST be nullable.
- Do not break existing tests: `uv run pytest` must stay green after every task.
- New backend tests go in `backend/tests/test_profile_api.py`, following the fixture pattern of `backend/tests/test_api_frontend_support.py` (in-memory SQLite + `dependency_overrides`).

---

### Task 1: User model columns + user schemas

**Files:**
- Modify: `backend/app/models/user.py`
- Modify: `backend/app/schemas/user.py`
- Test: `backend/tests/test_profile_api.py` (create)

**Interfaces:**
- Produces: `User.bio: str | None`, `User.avatar_url: str | None`; schemas `UserSummary` (now with `avatar_url`), `UserProfileOut`, `UserUpdate` — used by every later backend task.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_profile_api.py`:

```python
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


def register(client: TestClient, username: str) -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 201
    return response.json()


def test_user_summary_includes_avatar_url() -> None:
    client = TestClient(app)
    body = register(client, "alice")
    assert body["avatar_url"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `backend/`): `uv run pytest tests/test_profile_api.py -v`
Expected: FAIL with `KeyError: 'avatar_url'`

- [ ] **Step 3: Add columns to the model**

In `backend/app/models/user.py`, after the `password_hash` column, add:

```python
    bio: Mapped[str | None] = mapped_column(String(160), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

(`String`, `Mapped`, `mapped_column` are already imported. `Mapped[str | None]` makes the column nullable — required by the dev schema auto-sync.)

- [ ] **Step 4: Update schemas**

Replace the contents of `backend/app/schemas/user.py` with:

```python
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)


class UserSummary(BaseModel):
    id: int
    username: str
    created_at: datetime
    avatar_url: str | None = None

    model_config = ConfigDict(from_attributes=True)


class UserLogin(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)


class UserDiscoveryOut(UserSummary):
    is_following: bool = False
    is_current_user: bool = False


class UserUpdate(BaseModel):
    bio: str | None = Field(default=None, max_length=160)


class UserProfileOut(BaseModel):
    id: int
    username: str
    bio: str | None = None
    avatar_url: str | None = None
    created_at: datetime
    follower_count: int
    following_count: int
    tweet_count: int
    is_following: bool
    is_current_user: bool
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_profile_api.py -v` — Expected: PASS
Run: `uv run pytest` — Expected: all existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/user.py backend/app/schemas/user.py backend/tests/test_profile_api.py
git commit -m "feat: add bio and avatar_url to user model and schemas"
```

---

### Task 2: Count helpers + `GET /users/{username}/profile`

**Files:**
- Modify: `backend/app/repositories/follow_repository.py`
- Modify: `backend/app/repositories/tweet_repository.py`
- Modify: `backend/app/api/routes/users.py`
- Test: `backend/tests/test_profile_api.py`

**Interfaces:**
- Consumes: `UserProfileOut` from Task 1; existing `user_repository.get_user_by_username(db, username)`.
- Produces: `follow_repository.count_followers(db, user_id) -> int`, `follow_repository.count_following(db, user_id) -> int`, `follow_repository.is_following(db, follower_id, followee_id) -> bool`, `tweet_repository.count_tweets_by_author(db, author_id) -> int`, route helper `_build_profile(db, user, current_user_id) -> UserProfileOut` (reused by Tasks 6 and 7), endpoint `GET /api/v1/users/{username}/profile`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_profile_api.py`:

```python
def test_profile_counts_and_follow_state() -> None:
    alice_client = TestClient(app)
    bob_client = TestClient(app)
    register(alice_client, "alice")
    bob = register(bob_client, "bob")

    bob_client.post("/api/v1/tweets", json={"content": "hello"})
    alice_client.post(f"/api/v1/follows/{bob['id']}")

    response = alice_client.get("/api/v1/users/bob/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == bob["id"]
    assert body["username"] == "bob"
    assert body["bio"] is None
    assert body["follower_count"] == 1
    assert body["following_count"] == 0
    assert body["tweet_count"] == 1
    assert body["is_following"] is True
    assert body["is_current_user"] is False

    own = bob_client.get("/api/v1/users/bob/profile").json()
    assert own["is_current_user"] is True
    assert own["is_following"] is False


def test_profile_unknown_username_returns_404() -> None:
    client = TestClient(app)
    register(client, "alice")
    assert client.get("/api/v1/users/ghost/profile").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_profile_api.py -v`
Expected: the two new tests FAIL with status `404 != 200` / `405` (route missing)

- [ ] **Step 3: Add repository count helpers**

In `backend/app/repositories/follow_repository.py`, change the import line to
`from sqlalchemy import delete, func, select` and append:

```python
def count_followers(db: Session, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Follow)
            .where(Follow.followee_id == user_id)
        )
        or 0
    )


def count_following(db: Session, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Follow)
            .where(Follow.follower_id == user_id)
        )
        or 0
    )


def is_following(db: Session, follower_id: int, followee_id: int) -> bool:
    return (
        db.scalar(
            select(Follow).where(
                Follow.follower_id == follower_id,
                Follow.followee_id == followee_id,
            )
        )
        is not None
    )
```

Append to `backend/app/repositories/tweet_repository.py`:

```python
def count_tweets_by_author(db: Session, author_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Tweet)
            .where(Tweet.user_id == author_id)
        )
        or 0
    )
```

(`func` and `select` are already imported there.)

- [ ] **Step 4: Add the profile route**

In `backend/app/api/routes/users.py`:

Change imports at the top to:

```python
from app.api.deps import get_current_user_id
from app.core.security import hash_password
from app.db.database import get_db
from app.models.user import User
from app.repositories import follow_repository, tweet_repository, user_repository
from app.schemas.user import (
    UserCreate,
    UserDiscoveryOut,
    UserProfileOut,
    UserSummary,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
```

Append at the end of the file:

```python
def _build_profile(db: Session, user: User, current_user_id: int) -> UserProfileOut:
    return UserProfileOut(
        id=user.id,
        username=user.username,
        bio=user.bio,
        avatar_url=user.avatar_url,
        created_at=user.created_at,
        follower_count=follow_repository.count_followers(db, user.id),
        following_count=follow_repository.count_following(db, user.id),
        tweet_count=tweet_repository.count_tweets_by_author(db, user.id),
        is_following=follow_repository.is_following(db, current_user_id, user.id),
        is_current_user=user.id == current_user_id,
    )


@router.get("/{username}/profile", response_model=UserProfileOut)
def get_user_profile(
    username: str,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> UserProfileOut:
    user = user_repository.get_user_by_username(db, username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )
    return _build_profile(db, user, current_user_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_profile_api.py -v` — Expected: PASS
Run: `uv run pytest` — Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/follow_repository.py backend/app/repositories/tweet_repository.py backend/app/api/routes/users.py backend/tests/test_profile_api.py
git commit -m "feat: user profile endpoint with follower and tweet counts"
```

---

### Task 3: `GET /tweets/{tweet_id}` (single tweet)

The `/tweet/:tweetId` frontend route (Task 8) needs to fetch one tweet by id; no such endpoint exists today.

**Files:**
- Modify: `backend/app/api/routes/tweets.py`
- Test: `backend/tests/test_profile_api.py`

**Interfaces:**
- Consumes: existing `tweet_repository.get_tweet(db, tweet_id)`, `tweet_repository.list_tweet_stats(db, tweet_ids, current_user_id)`, `UserSummary` from Task 1.
- Produces: `GET /api/v1/tweets/{tweet_id}` returning `TweetOut`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_profile_api.py`:

```python
def test_get_single_tweet_with_stats() -> None:
    client = TestClient(app)
    register(client, "alice")
    tweet = client.post("/api/v1/tweets", json={"content": "hello"}).json()
    client.post(f"/api/v1/tweets/{tweet['id']}/likes/toggle")

    response = client.get(f"/api/v1/tweets/{tweet['id']}")
    assert response.status_code == 200
    body = response.json()
    assert body["content"] == "hello"
    assert body["author"]["username"] == "alice"
    assert body["like_count"] == 1
    assert body["liked_by_me"] is True

    assert client.get("/api/v1/tweets/999999").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_profile_api.py::test_get_single_tweet_with_stats -v`
Expected: FAIL (405 or 422 — no GET route for `/tweets/{tweet_id}`)

- [ ] **Step 3: Add the route**

In `backend/app/api/routes/tweets.py`, add to the imports:
`from app.schemas.user import UserSummary`

Append at the **end of the file** (it MUST come after the `GET /stats` route so `/tweets/stats` keeps matching the literal path first):

```python
@router.get("/{tweet_id}", response_model=TweetOut)
def get_tweet(
    tweet_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> TweetOut:
    tweet = tweet_repository.get_tweet(db, tweet_id)
    if tweet is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="tweet not found",
        )

    stats_rows = tweet_repository.list_tweet_stats(
        db,
        tweet_ids=[tweet_id],
        current_user_id=current_user_id,
    )
    stats = stats_rows[0] if stats_rows else {
        "like_count": 0,
        "comment_count": 0,
        "retweet_count": 0,
        "liked_by_me": False,
    }

    return TweetOut(
        id=tweet.id,
        content=tweet.content,
        created_at=tweet.created_at,
        author=UserSummary.model_validate(tweet.author),
        like_count=stats["like_count"],
        comment_count=stats["comment_count"],
        retweet_count=stats["retweet_count"],
        liked_by_me=stats["liked_by_me"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest` — Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/routes/tweets.py backend/tests/test_profile_api.py
git commit -m "feat: get single tweet endpoint"
```

---

### Task 4: `GET /users/{username}/tweets`

**Files:**
- Modify: `backend/app/schemas/tweet.py`
- Modify: `backend/app/api/routes/users.py`
- Test: `backend/tests/test_profile_api.py`

**Interfaces:**
- Consumes: `tweet_repository.list_tweets_by_authors(...)`, `TimelineService.serialize_tweet(row)`, `encode_cursor`/`decode_cursor` from `app.services.timeline_service`.
- Produces: schema `ProfileTweetsPage {items: list[TweetOut], next_cursor: str | None}`; endpoint `GET /api/v1/users/{username}/tweets?cursor&limit`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_profile_api.py`:

```python
def test_user_tweets_newest_first_with_cursor() -> None:
    client = TestClient(app)
    register(client, "alice")
    for index in range(3):
        client.post("/api/v1/tweets", json={"content": f"tweet {index}"})

    page = client.get("/api/v1/users/alice/tweets", params={"limit": 2}).json()
    assert [item["content"] for item in page["items"]] == ["tweet 2", "tweet 1"]
    assert page["next_cursor"]

    page2 = client.get(
        "/api/v1/users/alice/tweets",
        params={"limit": 2, "cursor": page["next_cursor"]},
    ).json()
    assert [item["content"] for item in page2["items"]] == ["tweet 0"]
    assert page2["next_cursor"] is None
```

(Use `params=` — the cursor contains `+00:00` and must be URL-encoded.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_profile_api.py::test_user_tweets_newest_first_with_cursor -v`
Expected: FAIL with 404 (route missing)

- [ ] **Step 3: Add the page schema**

Append to `backend/app/schemas/tweet.py`:

```python
class ProfileTweetsPage(BaseModel):
    items: list[TweetOut]
    next_cursor: str | None = None
```

- [ ] **Step 4: Add the route**

In `backend/app/api/routes/users.py`, add imports:

```python
from app.schemas.tweet import ProfileTweetsPage
from app.services.timeline_service import TimelineService, decode_cursor, encode_cursor
```

Append at the end of the file:

```python
@router.get("/{username}/tweets", response_model=ProfileTweetsPage)
def list_user_tweets(
    username: str,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ProfileTweetsPage:
    user = user_repository.get_user_by_username(db, username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )

    cursor_created_at, cursor_id = decode_cursor(cursor)
    if cursor and (cursor_created_at is None or cursor_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid cursor",
        )

    rows = tweet_repository.list_tweets_by_authors(
        db,
        author_ids=[user.id],
        limit=limit,
        current_user_id=current_user_id,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
    )

    has_next = len(rows) > limit
    page_rows = rows[:limit]
    service = TimelineService(db)
    items = [service.serialize_tweet(row) for row in page_rows]

    next_cursor = None
    if has_next and page_rows:
        last_row = page_rows[-1]
        next_cursor = encode_cursor(
            last_row["cursor_created_at"],
            last_row["cursor_id"],
        )

    return ProfileTweetsPage(items=items, next_cursor=next_cursor)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest` — Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/tweet.py backend/app/api/routes/users.py backend/tests/test_profile_api.py
git commit -m "feat: list user tweets endpoint with cursor pagination"
```

---

### Task 5: `GET /users/{username}/replies`

**Files:**
- Modify: `backend/app/repositories/engagement_repository.py`
- Modify: `backend/app/schemas/comment.py`
- Modify: `backend/app/api/routes/users.py`
- Test: `backend/tests/test_profile_api.py`

**Interfaces:**
- Consumes: `engagement_repository.list_comment_stats(db, comment_ids, current_user_id)`, `tweet_repository.list_tweet_stats(...)`, `CommentOut`, `TweetOut`, `UserSummary`, `decode_cursor`/`encode_cursor`.
- Produces: `engagement_repository.list_replies_by_user(db, user_id, limit, cursor_created_at=None, cursor_id=None) -> list[dict]` where each dict is `{"comment": Comment, "tweet": Tweet, "tweet_author": User, "cursor_created_at": datetime, "cursor_id": int}`; schemas `ReplyWithParentOut {comment: CommentOut, parent_tweet: TweetOut}` and `ProfileRepliesPage {items, next_cursor}`; endpoint `GET /api/v1/users/{username}/replies?cursor&limit`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_profile_api.py`:

```python
def test_user_replies_include_parent_tweet() -> None:
    alice_client = TestClient(app)
    bob_client = TestClient(app)
    register(alice_client, "alice")
    register(bob_client, "bob")

    tweet = bob_client.post("/api/v1/tweets", json={"content": "original"}).json()
    alice_client.post(
        f"/api/v1/tweets/{tweet['id']}/comments",
        json={"content": "my reply"},
    )

    response = alice_client.get("/api/v1/users/alice/replies")
    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["comment"]["content"] == "my reply"
    assert item["comment"]["author"]["username"] == "alice"
    assert item["parent_tweet"]["content"] == "original"
    assert item["parent_tweet"]["author"]["username"] == "bob"
    assert item["parent_tweet"]["comment_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_profile_api.py::test_user_replies_include_parent_tweet -v`
Expected: FAIL with 404 (route missing)

- [ ] **Step 3: Add the repository query**

In `backend/app/repositories/engagement_repository.py`, change the first import line to:

```python
from datetime import datetime

from sqlalchemy import and_, func, or_, select
```

Append at the end of the file:

```python
def list_replies_by_user(
    db: Session,
    user_id: int,
    limit: int,
    cursor_created_at: datetime | None = None,
    cursor_id: int | None = None,
) -> list[dict]:
    """
    Return the user's comments newest-first, each joined with its parent tweet
    and the tweet's author. Fetches limit + 1 rows for has-next detection.
    """
    stmt = (
        select(Comment, Tweet, User)
        .join(Tweet, Tweet.id == Comment.tweet_id)
        .join(User, User.id == Tweet.user_id)
        .where(Comment.user_id == user_id)
        .order_by(Comment.created_at.desc(), Comment.id.desc())
        .limit(limit + 1)
    )

    if cursor_created_at is not None and cursor_id is not None:
        stmt = stmt.where(
            or_(
                Comment.created_at < cursor_created_at,
                and_(
                    Comment.created_at == cursor_created_at,
                    Comment.id < cursor_id,
                ),
            )
        )

    rows = db.execute(stmt).all()
    return [
        {
            "comment": comment,
            "tweet": tweet,
            "tweet_author": tweet_author,
            "cursor_created_at": comment.created_at,
            "cursor_id": comment.id,
        }
        for comment, tweet, tweet_author in rows
    ]
```

- [ ] **Step 4: Add the schemas**

Append to `backend/app/schemas/comment.py`:

```python
class ReplyWithParentOut(BaseModel):
    comment: CommentOut
    parent_tweet: "TweetOut"


class ProfileRepliesPage(BaseModel):
    items: list[ReplyWithParentOut]
    next_cursor: str | None = None


from app.schemas.tweet import TweetOut  # noqa: E402

ReplyWithParentOut.model_rebuild()
```

(The deferred import avoids a circular import if `schemas/tweet.py` ever imports from `schemas/comment.py`; today it doesn't, but the forward-ref pattern keeps the modules independent.)

- [ ] **Step 5: Add the route**

In `backend/app/api/routes/users.py`, add imports:

```python
from app.repositories import engagement_repository
from app.schemas.comment import CommentOut, ProfileRepliesPage, ReplyWithParentOut
from app.schemas.tweet import TweetOut
```

(Merge `engagement_repository` into the existing `from app.repositories import ...` line.)

Append at the end of the file:

```python
@router.get("/{username}/replies", response_model=ProfileRepliesPage)
def list_user_replies(
    username: str,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> ProfileRepliesPage:
    user = user_repository.get_user_by_username(db, username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )

    cursor_created_at, cursor_id = decode_cursor(cursor)
    if cursor and (cursor_created_at is None or cursor_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid cursor",
        )

    rows = engagement_repository.list_replies_by_user(
        db,
        user_id=user.id,
        limit=limit,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
    )

    has_next = len(rows) > limit
    page_rows = rows[:limit]

    comment_ids = [row["comment"].id for row in page_rows]
    tweet_ids = [row["tweet"].id for row in page_rows]
    comment_stats = {
        stats["id"]: stats
        for stats in engagement_repository.list_comment_stats(
            db, comment_ids=comment_ids, current_user_id=current_user_id
        )
    }
    tweet_stats = {
        stats["id"]: stats
        for stats in tweet_repository.list_tweet_stats(
            db, tweet_ids=tweet_ids, current_user_id=current_user_id
        )
    }
    empty = {"like_count": 0, "comment_count": 0, "retweet_count": 0, "liked_by_me": False}
    author_summary = UserSummary.model_validate(user)

    items = []
    for row in page_rows:
        comment = row["comment"]
        tweet = row["tweet"]
        c_stats = comment_stats.get(comment.id, empty)
        t_stats = tweet_stats.get(tweet.id, empty)
        items.append(
            ReplyWithParentOut(
                comment=CommentOut(
                    id=comment.id,
                    tweet_id=comment.tweet_id,
                    parent_comment_id=comment.parent_comment_id,
                    content=comment.content,
                    created_at=comment.created_at,
                    author=author_summary,
                    like_count=c_stats["like_count"],
                    comment_count=c_stats["comment_count"],
                    retweet_count=c_stats["retweet_count"],
                    liked_by_me=c_stats["liked_by_me"],
                ),
                parent_tweet=TweetOut(
                    id=tweet.id,
                    content=tweet.content,
                    created_at=tweet.created_at,
                    author=UserSummary.model_validate(row["tweet_author"]),
                    like_count=t_stats["like_count"],
                    comment_count=t_stats["comment_count"],
                    retweet_count=t_stats["retweet_count"],
                    liked_by_me=t_stats["liked_by_me"],
                ),
            )
        )

    next_cursor = None
    if has_next and page_rows:
        last_row = page_rows[-1]
        next_cursor = encode_cursor(
            last_row["cursor_created_at"],
            last_row["cursor_id"],
        )

    return ProfileRepliesPage(items=items, next_cursor=next_cursor)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest` — Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add backend/app/repositories/engagement_repository.py backend/app/schemas/comment.py backend/app/api/routes/users.py backend/tests/test_profile_api.py
git commit -m "feat: list user replies with parent tweets endpoint"
```

---

### Task 6: `PATCH /users/me` (update bio)

**Files:**
- Modify: `backend/app/repositories/user_repository.py`
- Modify: `backend/app/api/routes/users.py`
- Test: `backend/tests/test_profile_api.py`

**Interfaces:**
- Consumes: `UserUpdate` from Task 1, `_build_profile` from Task 2.
- Produces: `user_repository.update_user_bio(db, user_id, bio) -> User`; endpoint `PATCH /api/v1/users/me` returning `UserProfileOut`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_profile_api.py`:

```python
def test_update_bio() -> None:
    client = TestClient(app)
    register(client, "alice")

    response = client.patch("/api/v1/users/me", json={"bio": "Building things."})
    assert response.status_code == 200
    assert response.json()["bio"] == "Building things."

    profile = client.get("/api/v1/users/alice/profile").json()
    assert profile["bio"] == "Building things."


def test_update_bio_rejects_too_long() -> None:
    client = TestClient(app)
    register(client, "alice")
    response = client.patch("/api/v1/users/me", json={"bio": "x" * 161})
    assert response.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_profile_api.py -v`
Expected: the two new tests FAIL with 405 (no PATCH route)

- [ ] **Step 3: Add the repository function**

Append to `backend/app/repositories/user_repository.py`:

```python
def update_user_bio(db: Session, user_id: int, bio: str | None) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")

    user.bio = bio
    db.commit()
    db.refresh(user)
    return user
```

- [ ] **Step 4: Add the route**

In `backend/app/api/routes/users.py`, add `UserUpdate` to the `app.schemas.user` import. Append at the end of the file:

```python
@router.patch("/me", response_model=UserProfileOut)
def update_current_user(
    payload: UserUpdate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> UserProfileOut:
    try:
        user = user_repository.update_user_bio(
            db,
            user_id=current_user_id,
            bio=payload.bio,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return _build_profile(db, user, current_user_id)
```

(No path conflict: there is no other `PATCH` route under `/users`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest` — Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/repositories/user_repository.py backend/app/api/routes/users.py backend/tests/test_profile_api.py
git commit -m "feat: update profile bio endpoint"
```

---

### Task 7: Avatar upload + static file serving

**Files:**
- Modify: `backend/pyproject.toml` (via `uv add python-multipart`)
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/repositories/user_repository.py`
- Modify: `backend/app/api/routes/users.py`
- Modify: `backend/main.py`
- Create: `backend/uploads/.gitignore`
- Test: `backend/tests/test_profile_api.py`

**Interfaces:**
- Consumes: `_build_profile` from Task 2.
- Produces: `settings.uploads_dir: str`; `user_repository.update_user_avatar(db, user_id, avatar_url) -> User`; endpoint `POST /api/v1/users/me/avatar` (multipart field name `file`) returning `UserProfileOut`; static mount `GET /uploads/...`.

- [ ] **Step 1: Install the multipart dependency**

Run (from `backend/`): `uv add python-multipart`
Expected: `python-multipart` added to `[project.dependencies]` in `pyproject.toml`.

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/test_profile_api.py` (add `import pytest` at the top of the file if you use the fixture form; `monkeypatch` and `tmp_path` are built-in pytest fixtures):

```python
def test_avatar_upload_updates_profile(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    client = TestClient(app)
    register(client, "alice")

    response = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("me.png", b"fake-png-bytes", "image/png")},
    )
    assert response.status_code == 200
    avatar_url = response.json()["avatar_url"]
    assert avatar_url.startswith("/uploads/avatars/")
    assert (tmp_path / "avatars").exists()

    me = client.get("/api/v1/auth/me").json()
    assert me["avatar_url"] == avatar_url


def test_avatar_upload_rejects_bad_type_and_size(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "uploads_dir", str(tmp_path))
    client = TestClient(app)
    register(client, "alice")

    bad_type = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert bad_type.status_code == 415

    too_big = client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("big.png", b"x" * (2 * 1024 * 1024 + 1), "image/png")},
    )
    assert too_big.status_code == 413
```

(The route validates the declared content type and byte size, not image magic bytes — fake bytes are fine here.)

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_profile_api.py -v`
Expected: the two new tests FAIL (405, no POST route)

- [ ] **Step 4: Add the uploads setting**

In `backend/app/core/config.py`, inside `class Settings`, after `dev_auto_sync_sqlite_schema: bool = True`, add:

```python
    uploads_dir: str = str(PROJECT_ROOT / "uploads")
```

- [ ] **Step 5: Add the avatar repository function**

Append to `backend/app/repositories/user_repository.py`:

```python
def update_user_avatar(db: Session, user_id: int, avatar_url: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")

    user.avatar_url = avatar_url
    db.commit()
    db.refresh(user)
    return user
```

- [ ] **Step 6: Add the upload route**

In `backend/app/api/routes/users.py`, add imports:

```python
import time
from pathlib import Path

from app.core.config import settings
from fastapi import UploadFile
```

(Merge `UploadFile` into the existing `fastapi` import line.)

Append at the end of the file:

```python
ALLOWED_AVATAR_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
MAX_AVATAR_BYTES = 2 * 1024 * 1024


@router.post("/me/avatar", response_model=UserProfileOut)
async def upload_avatar(
    file: UploadFile,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> UserProfileOut:
    extension = ALLOWED_AVATAR_TYPES.get(file.content_type or "")
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="avatar must be a JPEG, PNG, or WebP image",
        )

    data = await file.read()
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="avatar must be 2 MB or smaller",
        )

    if user_repository.get_user(db, current_user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )

    avatars_dir = Path(settings.uploads_dir) / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    for old_file in avatars_dir.glob(f"{current_user_id}.*"):
        old_file.unlink(missing_ok=True)
    (avatars_dir / f"{current_user_id}{extension}").write_bytes(data)

    # The ?v= timestamp busts browser caches when the avatar is replaced;
    # StaticFiles ignores the query string when resolving the file.
    avatar_url = (
        f"/uploads/avatars/{current_user_id}{extension}?v={int(time.time())}"
    )
    user = user_repository.update_user_avatar(
        db,
        user_id=current_user_id,
        avatar_url=avatar_url,
    )
    return _build_profile(db, user, current_user_id)
```

- [ ] **Step 7: Mount static uploads**

In `backend/main.py`, add imports:

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles
```

After `app.include_router(api_router)`, add:

```python
uploads_path = Path(settings.uploads_dir)
uploads_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")
```

- [ ] **Step 8: Ignore uploaded files in git**

Create `backend/uploads/.gitignore`:

```
*
!.gitignore
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `uv run pytest` — Expected: all PASS

- [ ] **Step 10: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/app/core/config.py backend/app/repositories/user_repository.py backend/app/api/routes/users.py backend/main.py backend/uploads/.gitignore
git commit -m "feat: avatar upload with static file serving"
```

---

### Task 8: Frontend — react-router restructure

Replace state-based view switching with routes `/` and `/tweet/:tweetId`, and split shared components out of `App.tsx` into `components.tsx`. Behavior must be unchanged apart from URLs.

**Files:**
- Modify: `frontend/package.json` (via `npm install react-router-dom`)
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/components.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Consumes: `GET /tweets/{tweet_id}` from Task 3.
- Produces: `components.tsx` exporting `TweetCard`, `CommentCard`, `mergeTweetStats`, `mergeCommentStats`, `getErrorMessage`, `formatCompactDate`, `parseBackendDate` (same signatures as today's private versions in `App.tsx`); `api.ts` exporting `getTweet(tweetId: number): Promise<Tweet>`; routes `/` and `/tweet/:tweetId`; `AppLayout` provides `{ refreshToken: number }` via outlet context.

- [ ] **Step 1: Install react-router-dom**

Run (from `frontend/`): `npm install react-router-dom`
Expected: `react-router-dom` (v7.x) in `package.json` dependencies.

- [ ] **Step 2: Add the single-tweet API function**

Append to `frontend/src/api.ts`:

```ts
export function getTweet(tweetId: number): Promise<Tweet> {
  return request<Tweet>(`/tweets/${tweetId}`);
}
```

- [ ] **Step 3: Wrap the app in BrowserRouter**

Replace the render call in `frontend/src/main.tsx` so `<App />` is wrapped:

```tsx
import { BrowserRouter } from "react-router-dom";
```

and render `<BrowserRouter><App /></BrowserRouter>` (keep `StrictMode` and the CSS import as-is).

- [ ] **Step 4: Create `frontend/src/components.tsx`**

Move the following from `App.tsx`, unchanged except for imports and `export` keywords: `TweetCard`, `CommentCard`, `mergeTweetStats`, `mergeCommentStats`, `getErrorMessage`, `formatCompactDate`, `parseBackendDate`. The file starts as:

```tsx
import { FormEvent, KeyboardEvent, useEffect, useMemo, useState } from "react";
import { Heart, MessageCircle, Repeat2 } from "lucide-react";
import {
  ApiError,
  createComment,
  replyToComment,
  retweetComment,
  retweetTweet,
  toggleCommentLike,
  toggleTweetLike,
} from "./api";
import type { Comment, CommentStats, Tweet, TweetStats } from "./types";
```

Each moved function gains `export` (e.g. `export function TweetCard(...)`). Their bodies are copied verbatim from the current `App.tsx` (lines 530–686 for `TweetCard`, 904–1044 for `CommentCard`, 1126–1213 for the helpers).

- [ ] **Step 5: Rewrite `App.tsx` around routes**

Restructure `App.tsx` to:

```tsx
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  Navigate,
  Outlet,
  Route,
  Routes,
  useNavigate,
  useOutletContext,
  useParams,
} from "react-router-dom";
```

plus the existing lucide/api/type imports (minus what moved to `components.tsx`), and:

```tsx
import {
  CommentCard,
  TweetCard,
  getErrorMessage,
  mergeCommentStats,
  mergeTweetStats,
  parseBackendDate,
} from "./components";
```

Component structure (`AuthScreen`, `ThemeToggle`, `Composer`, `UserDiscoveryPanel` keep their current bodies):

```tsx
function App() {
  // ... existing boot/theme/currentUser logic unchanged ...
  if (booting) { /* unchanged boot screen */ }
  if (!currentUser) { /* unchanged AuthScreen */ }

  return (
    <Routes>
      <Route
        element={
          <AppLayout
            currentUser={currentUser}
            onLogout={() => setCurrentUser(null)}
            theme={theme}
            onToggleTheme={toggleTheme}
          />
        }
      >
        <Route path="/" element={<HomeView />} />
        <Route path="/tweet/:tweetId" element={<TweetDetailRoute />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
```

`AppLayout` renders the current `app-shell` markup (rail + discovery column) with the center column replaced by an `Outlet`:

```tsx
type LayoutContext = { refreshToken: number };

function AppLayout({ currentUser, onLogout, theme, onToggleTheme }: {
  currentUser: UserSummary;
  onLogout: () => void;
  theme: Theme;
  onToggleTheme: () => void;
}) {
  const [refreshToken, setRefreshToken] = useState(0);

  async function handleLogout() {
    await logout().catch(() => undefined);
    onLogout();
  }

  return (
    <div className="app-shell">
      <aside className="rail">{/* current rail markup, unchanged */}</aside>
      <main id="feed" className="feed-column">
        <Outlet context={{ refreshToken } satisfies LayoutContext} />
      </main>
      <aside id="discover" className="discovery-column">
        <UserDiscoveryPanel onChanged={() => setRefreshToken((value) => value + 1)} />
      </aside>
    </div>
  );
}
```

`HomeView` takes over the feed state from the old `MainApp` (activeTab, page, tweetById, tweetIds, loadingFeed, feedError, `loadFeed`, stats polling effect, `insertPostedTweet`, `patchTweet` — all copied as-is, minus `selectedTweetId`). It renders the current `feed-header` (title + tabs; the header's `ThemeToggle` moves here too — pass `theme`/`onToggleTheme` through outlet context or drop the in-header toggle and rely on the rail one; **drop the in-header toggle**, the rail toggle remains), the `Composer`, and the tweet list. Two changes:

- `useEffect(() => { void loadFeed(); }, [loadFeed, refreshToken]);` where `refreshToken` comes from `useOutletContext<LayoutContext>()`.
- `TweetCard` `onOpen` becomes `() => navigate(`/tweet/${tweet.id}`)` with `const navigate = useNavigate()`.
- The stats-polling effect keys off `tweetIds.join(",")` only (no more `selectedTweetId`).

`TweetDetailRoute` fetches its own tweet and owns patching:

```tsx
function TweetDetailRoute() {
  const { tweetId } = useParams();
  const navigate = useNavigate();
  const numericTweetId = Number(tweetId);
  const [tweet, setTweet] = useState<Tweet | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!Number.isInteger(numericTweetId) || numericTweetId <= 0) {
      setError("Tweet not found.");
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    getTweet(numericTweetId)
      .then((loaded) => { if (!cancelled) setTweet(loaded); })
      .catch((err) => { if (!cancelled) setError(getErrorMessage(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [numericTweetId]);

  const patchTweet = useCallback((_tweetId: number, patch: Partial<Tweet>) => {
    setTweet((current) => (current ? { ...current, ...patch } : current));
  }, []);

  if (loading) {
    return (
      <div className="loading-row">
        <Loader2 className="spin" size={18} aria-hidden="true" />
        <span>Loading</span>
      </div>
    );
  }
  if (error || !tweet) {
    return <div className="status-panel error">{error || "Tweet not found."}</div>;
  }
  return <TweetDetail tweet={tweet} onBack={() => navigate(-1)} onTweetPatch={patchTweet} />;
}
```

`TweetDetail` keeps its current body (comments loading, comment stats polling, actions) — only its data source changed. Add one 5-second `getTweetStats([tweet.id])` polling effect inside `TweetDetailRoute` mirroring the old parent-level sync:

```tsx
  useEffect(() => {
    if (!tweet) return;
    const timer = window.setInterval(async () => {
      try {
        const stats = await getTweetStats([tweet.id]);
        if (stats[0]) {
          setTweet((current) =>
            current ? { ...current, ...stats[0], id: current.id } : current,
          );
        }
      } catch {
        // background sync; ignore failures
      }
    }, 5000);
    return () => window.clearInterval(timer);
  }, [tweet?.id]);
```

- [ ] **Step 6: Typecheck**

Run (from `frontend/`): `npm run typecheck`
Expected: no errors.

- [ ] **Step 7: Manually verify**

Start backend (`uv run uvicorn main:app --reload` from `backend/`) and frontend (`npm run dev` from `frontend/`). In the browser:
- `/` shows the timeline; tabs switch; posting works.
- Clicking a tweet navigates to `/tweet/<id>`; comments load; like/reply work; browser Back returns to the feed.
- Refreshing `/tweet/<id>` directly loads the tweet (Vite dev server serves the SPA fallback by default).
- Following someone in the People panel refreshes the feed.

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/main.tsx frontend/src/App.tsx frontend/src/components.tsx frontend/src/api.ts
git commit -m "feat(frontend): restructure onto react-router routes"
```

---

### Task 9: Frontend — profile types, API client, Avatar component

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/components.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: backend endpoints from Tasks 2, 4, 5, 6, 7.
- Produces: types `UserProfile`, `ProfileTweetsPage`, `ReplyWithParent`, `ProfileRepliesPage`; `UserSummary.avatar_url`; api functions `getUserProfile(username)`, `getUserTweets(username, cursor?)`, `getUserReplies(username, cursor?)`, `updateProfile(bio)`, `uploadAvatar(file)`, `resolveMediaUrl(path)`; component `Avatar({ user, size? })` exported from `components.tsx` — used by Tasks 10–12.

- [ ] **Step 1: Extend types**

In `frontend/src/types.ts`, add `avatar_url: string | null;` to `UserSummary`, and append:

```ts
export type UserProfile = {
  id: number;
  username: string;
  bio: string | null;
  avatar_url: string | null;
  created_at: string;
  follower_count: number;
  following_count: number;
  tweet_count: number;
  is_following: boolean;
  is_current_user: boolean;
};

export type ProfileTweetsPage = {
  items: Tweet[];
  next_cursor: string | null;
};

export type ReplyWithParent = {
  comment: Comment;
  parent_tweet: Tweet;
};

export type ProfileRepliesPage = {
  items: ReplyWithParent[];
  next_cursor: string | null;
};
```

- [ ] **Step 2: Extend the API client**

In `frontend/src/api.ts`:

1. Add to the type import: `ProfileRepliesPage`, `ProfileTweetsPage`, `UserProfile`.
2. Below `API_BASE_URL`, add:

```ts
const BACKEND_ORIGIN = new URL(API_BASE_URL).origin;

export function resolveMediaUrl(path: string | null): string | null {
  if (!path) {
    return null;
  }
  return path.startsWith("http") ? path : `${BACKEND_ORIGIN}${path}`;
}
```

3. In `request()`, don't force a JSON content type for form data (the browser must set the multipart boundary):

```ts
  const isFormData = init.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...init.headers,
    },
  });
```

4. Append:

```ts
export function getUserProfile(username: string): Promise<UserProfile> {
  return request<UserProfile>(`/users/${encodeURIComponent(username)}/profile`);
}

export function getUserTweets(
  username: string,
  cursor?: string | null,
): Promise<ProfileTweetsPage> {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return request<ProfileTweetsPage>(
    `/users/${encodeURIComponent(username)}/tweets?${params.toString()}`,
  );
}

export function getUserReplies(
  username: string,
  cursor?: string | null,
): Promise<ProfileRepliesPage> {
  const params = new URLSearchParams({ limit: "20" });
  if (cursor) {
    params.set("cursor", cursor);
  }
  return request<ProfileRepliesPage>(
    `/users/${encodeURIComponent(username)}/replies?${params.toString()}`,
  );
}

export function updateProfile(bio: string): Promise<UserProfile> {
  return request<UserProfile>("/users/me", {
    method: "PATCH",
    body: JSON.stringify({ bio }),
  });
}

export function uploadAvatar(file: File): Promise<UserProfile> {
  const form = new FormData();
  form.append("file", file);
  return request<UserProfile>("/users/me/avatar", {
    method: "POST",
    body: form,
  });
}
```

- [ ] **Step 3: Add the Avatar component and use it everywhere**

In `frontend/src/components.tsx`, add `resolveMediaUrl` to the `./api` import, `UserSummary` to the type import, and add:

```tsx
export function Avatar({
  user,
  size = "regular",
}: {
  user: UserSummary;
  size?: "small" | "regular" | "large";
}) {
  const src = resolveMediaUrl(user.avatar_url);
  const sizeClass =
    size === "small" ? "avatar small" : size === "large" ? "avatar large" : "avatar";

  if (src) {
    return <img className={`${sizeClass} avatar-image`} src={src} alt="" aria-hidden="true" />;
  }
  return (
    <div className={sizeClass} aria-hidden="true">
      {user.username.slice(0, 1).toUpperCase()}
    </div>
  );
}
```

Replace every initial-letter avatar `<div className="avatar...">...</div>` with `<Avatar ... />`:
- `TweetCard`: `<Avatar user={tweet.author} />`
- `CommentCard`: `<Avatar user={localComment.author} size="small" />`
- In `App.tsx` — `TweetDetail` author block: `<Avatar user={tweet.author} />`; `UserDiscoveryPanel` rows: `<Avatar user={user} size="small" />` (import `Avatar` from `./components`).

- [ ] **Step 4: Add avatar image CSS**

Append to `frontend/src/styles.css`:

```css
.avatar-image {
  object-fit: cover;
  background: var(--soft);
}

.avatar.large {
  width: 128px;
  height: 128px;
  font-size: 42px;
}
```

(The existing `.avatar` rule already sets size/border-radius for the regular and `.small` variants; `img.avatar` inherits them.)

- [ ] **Step 5: Typecheck and verify**

Run: `npm run typecheck` — Expected: no errors.
In the running app, avatars still render as initial letters (no one has uploaded an image yet) and nothing else changed.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/components.tsx frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(frontend): profile api client and shared avatar component"
```

---

### Task 10: Frontend — ProfileView with Tweets/Replies tabs and Follow

**Files:**
- Create: `frontend/src/ProfileView.tsx`
- Modify: `frontend/src/App.tsx` (register routes)
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `getUserProfile`, `getUserTweets`, `getUserReplies`, `followUser`, `unfollowUser`, `ApiError` from `./api`; `Avatar`, `TweetCard`, `CommentCard`, `getErrorMessage`, `parseBackendDate` from `./components`.
- Produces: `ProfileView({ currentUser, onCurrentUserChange })` mounted at `/profile/:username` and `/profile/:username/replies`. `onCurrentUserChange(user: UserSummary)` is called after profile edits (wired fully in Task 11).

- [ ] **Step 1: Create `frontend/src/ProfileView.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, Calendar, Loader2 } from "lucide-react";
import {
  ApiError,
  followUser,
  getUserProfile,
  getUserReplies,
  getUserTweets,
  unfollowUser,
} from "./api";
import type {
  ReplyWithParent,
  Tweet,
  UserProfile,
  UserSummary,
} from "./types";
import {
  Avatar,
  CommentCard,
  TweetCard,
  getErrorMessage,
  parseBackendDate,
} from "./components";

export function ProfileView({
  currentUser,
  onCurrentUserChange,
}: {
  currentUser: UserSummary;
  onCurrentUserChange: (user: UserSummary) => void;
}) {
  const { username = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const activeTab: "tweets" | "replies" = location.pathname.endsWith("/replies")
    ? "replies"
    : "tweets";

  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [followBusy, setFollowBusy] = useState(false);

  const [tweets, setTweets] = useState<Tweet[]>([]);
  const [tweetsCursor, setTweetsCursor] = useState<string | null>(null);
  const [replies, setReplies] = useState<ReplyWithParent[]>([]);
  const [repliesCursor, setRepliesCursor] = useState<string | null>(null);
  const [loadingFeed, setLoadingFeed] = useState(false);
  const [feedError, setFeedError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setProfile(null);
    setNotFound(false);
    setProfileError("");
    getUserProfile(username)
      .then((loaded) => {
        if (!cancelled) setProfile(loaded);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true);
        } else {
          setProfileError(getErrorMessage(err));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [username]);

  const loadTweets = useCallback(
    async (cursor?: string | null, append = false) => {
      setLoadingFeed(true);
      setFeedError("");
      try {
        const page = await getUserTweets(username, cursor);
        setTweets((current) => (append ? [...current, ...page.items] : page.items));
        setTweetsCursor(page.next_cursor);
      } catch (err) {
        setFeedError(getErrorMessage(err));
      } finally {
        setLoadingFeed(false);
      }
    },
    [username],
  );

  const loadReplies = useCallback(
    async (cursor?: string | null, append = false) => {
      setLoadingFeed(true);
      setFeedError("");
      try {
        const page = await getUserReplies(username, cursor);
        setReplies((current) => (append ? [...current, ...page.items] : page.items));
        setRepliesCursor(page.next_cursor);
      } catch (err) {
        setFeedError(getErrorMessage(err));
      } finally {
        setLoadingFeed(false);
      }
    },
    [username],
  );

  useEffect(() => {
    setTweets([]);
    setReplies([]);
    if (notFound) {
      return;
    }
    if (activeTab === "tweets") {
      void loadTweets();
    } else {
      void loadReplies();
    }
  }, [activeTab, loadTweets, loadReplies, notFound]);

  const patchTweet = useCallback((tweetId: number, patch: Partial<Tweet>) => {
    setTweets((current) =>
      current.map((tweet) => (tweet.id === tweetId ? { ...tweet, ...patch } : tweet)),
    );
  }, []);

  const patchReplyParent = useCallback((tweetId: number, patch: Partial<Tweet>) => {
    setReplies((current) =>
      current.map((item) =>
        item.parent_tweet.id === tweetId
          ? { ...item, parent_tweet: { ...item.parent_tweet, ...patch } }
          : item,
      ),
    );
  }, []);

  async function toggleFollow() {
    if (!profile || followBusy) {
      return;
    }
    setFollowBusy(true);
    try {
      if (profile.is_following) {
        await unfollowUser(profile.id);
        setProfile({
          ...profile,
          is_following: false,
          follower_count: Math.max(0, profile.follower_count - 1),
        });
      } else {
        await followUser(profile.id);
        setProfile({
          ...profile,
          is_following: true,
          follower_count: profile.follower_count + 1,
        });
      }
    } catch (err) {
      setProfileError(getErrorMessage(err));
    } finally {
      setFollowBusy(false);
    }
  }

  if (notFound) {
    return (
      <section className="profile-view">
        <div className="detail-toolbar">
          <button className="icon-button" onClick={() => navigate(-1)} aria-label="Back">
            <ArrowLeft size={20} aria-hidden="true" />
          </button>
          <h2>Profile</h2>
        </div>
        <div className="status-panel">
          <strong>This account doesn&apos;t exist</strong>
          <p>Try searching for another.</p>
        </div>
      </section>
    );
  }

  if (!profile) {
    return (
      <div className="loading-row">
        <Loader2 className="spin" size={18} aria-hidden="true" />
        <span>Loading profile</span>
      </div>
    );
  }

  const joinedDate = new Intl.DateTimeFormat(undefined, {
    month: "long",
    year: "numeric",
  }).format(parseBackendDate(profile.created_at));

  return (
    <section className="profile-view" aria-label={`Profile of ${profile.username}`}>
      <div className="detail-toolbar">
        <button className="icon-button" onClick={() => navigate(-1)} aria-label="Back">
          <ArrowLeft size={20} aria-hidden="true" />
        </button>
        <div className="profile-toolbar-copy">
          <h2>@{profile.username}</h2>
          <span>{profile.tweet_count} Tweets</span>
        </div>
      </div>

      <div className="profile-banner" aria-hidden="true" />
      <header className="profile-header">
        <div className="profile-header-top">
          <Avatar user={profile} size="large" />
          {profile.is_current_user ? (
            <button className="outline-button" disabled>
              Edit profile
            </button>
          ) : (
            <button
              className={profile.is_following ? "outline-button following" : "primary-button compact"}
              onClick={() => void toggleFollow()}
              disabled={followBusy}
            >
              {profile.is_following ? "Following" : "Follow"}
            </button>
          )}
        </div>
        <h1 className="profile-name">@{profile.username}</h1>
        {profile.bio ? <p className="profile-bio">{profile.bio}</p> : null}
        <p className="profile-meta">
          <Calendar size={16} aria-hidden="true" />
          <span>Joined {joinedDate}</span>
        </p>
        <p className="profile-stats">
          <span>
            <strong>{profile.following_count}</strong> Following
          </span>
          <span>
            <strong>{profile.follower_count}</strong> Followers
          </span>
        </p>
        {profileError ? <p className="form-error">{profileError}</p> : null}
      </header>

      <div className="tab-list" role="tablist" aria-label="Profile content">
        <Link
          to={`/profile/${username}`}
          className={activeTab === "tweets" ? "tab active" : "tab"}
          role="tab"
          aria-selected={activeTab === "tweets"}
        >
          Tweets
        </Link>
        <Link
          to={`/profile/${username}/replies`}
          className={activeTab === "replies" ? "tab active" : "tab"}
          role="tab"
          aria-selected={activeTab === "replies"}
        >
          Replies
        </Link>
      </div>

      {feedError ? <div className="status-panel error">{feedError}</div> : null}

      {activeTab === "tweets" ? (
        <>
          {!loadingFeed && tweets.length === 0 && !feedError ? (
            <div className="status-panel">No tweets yet.</div>
          ) : null}
          <section className="tweet-list">
            {tweets.map((tweet) => (
              <TweetCard
                key={tweet.id}
                tweet={tweet}
                onOpen={() => navigate(`/tweet/${tweet.id}`)}
                onTweetPatch={patchTweet}
              />
            ))}
          </section>
          {tweetsCursor ? (
            <button
              className="load-more"
              onClick={() => void loadTweets(tweetsCursor, true)}
              disabled={loadingFeed}
            >
              Load more
            </button>
          ) : null}
        </>
      ) : (
        <>
          {!loadingFeed && replies.length === 0 && !feedError ? (
            <div className="status-panel">No replies yet.</div>
          ) : null}
          <section className="tweet-list">
            {replies.map((item) => (
              <div className="reply-thread" key={item.comment.id}>
                <TweetCard
                  tweet={item.parent_tweet}
                  onOpen={() => navigate(`/tweet/${item.parent_tweet.id}`)}
                  onTweetPatch={patchReplyParent}
                />
                <div className="reply-thread-item">
                  <p className="replying-to">
                    Replying to{" "}
                    <Link to={`/profile/${item.parent_tweet.author.username}`}>
                      @{item.parent_tweet.author.username}
                    </Link>
                  </p>
                  <CommentCard
                    comment={item.comment}
                    onChanged={() => void loadReplies()}
                    onReplyCreated={() => patchReplyParent(item.parent_tweet.id, {
                      comment_count: item.parent_tweet.comment_count + 1,
                    })}
                  />
                </div>
              </div>
            ))}
          </section>
          {repliesCursor ? (
            <button
              className="load-more"
              onClick={() => void loadReplies(repliesCursor, true)}
              disabled={loadingFeed}
            >
              Load more
            </button>
          ) : null}
        </>
      )}

      {loadingFeed ? (
        <div className="loading-row">
          <Loader2 className="spin" size={18} aria-hidden="true" />
          <span>Loading</span>
        </div>
      ) : null}
    </section>
  );
}
```

Note: the `Avatar` component takes a `UserSummary`; `UserProfile` is structurally compatible (`id`, `username`, `created_at`, `avatar_url`), so `<Avatar user={profile} ... />` typechecks. The "Edit profile" button is rendered disabled here; Task 11 wires it up. `onCurrentUserChange` and `currentUser` are unused until Task 11 — prefix them with underscores or reference them in Task 11; to keep typecheck green in this task, destructure but mark used: add `void currentUser; void onCurrentUserChange;` at the top of the component body (removed in Task 11).

- [ ] **Step 2: Register the routes**

In `frontend/src/App.tsx`, add `import { ProfileView } from "./ProfileView";` and inside the layout `<Route>` add:

```tsx
<Route
  path="/profile/:username"
  element={<ProfileView currentUser={currentUser} onCurrentUserChange={setCurrentUser} />}
/>
<Route
  path="/profile/:username/replies"
  element={<ProfileView currentUser={currentUser} onCurrentUserChange={setCurrentUser} />}
/>
```

- [ ] **Step 3: Add profile CSS**

Append to `frontend/src/styles.css`:

```css
.profile-view {
  display: flex;
  flex-direction: column;
}

.profile-toolbar-copy h2 {
  margin: 0;
  font-size: 18px;
}

.profile-toolbar-copy span {
  color: var(--muted);
  font-size: 13px;
}

.profile-banner {
  height: 140px;
  background: linear-gradient(120deg, var(--accent), var(--accent-strong));
}

.profile-header {
  padding: 0 16px 12px;
  border-bottom: 1px solid var(--line);
}

.profile-header-top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  margin-top: -64px;
  margin-bottom: 12px;
}

.profile-header-top .avatar.large {
  border: 4px solid var(--panel);
}

.profile-name {
  margin: 0 0 4px;
  font-size: 20px;
}

.profile-bio {
  margin: 8px 0;
  white-space: pre-wrap;
}

.profile-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--muted);
  font-size: 14px;
  margin: 8px 0;
}

.profile-stats {
  display: flex;
  gap: 18px;
  margin: 8px 0 0;
  font-size: 14px;
  color: var(--muted);
}

.profile-stats strong {
  color: var(--ink);
}

.outline-button {
  border: 1px solid var(--line);
  background: transparent;
  color: var(--ink);
  border-radius: 999px;
  padding: 8px 16px;
  font-weight: 700;
  cursor: pointer;
}

.outline-button:hover:not(:disabled) {
  background: var(--hover);
}

.outline-button.following:hover {
  border-color: var(--danger);
  color: var(--danger);
}

.reply-thread {
  border-bottom: 1px solid var(--line);
}

.reply-thread .tweet-card {
  border-bottom: none;
}

.reply-thread-item {
  padding: 0 16px 12px 40px;
  position: relative;
}

.reply-thread-item::before {
  content: "";
  position: absolute;
  left: 34px;
  top: -12px;
  bottom: 12px;
  width: 2px;
  background: var(--line);
}

.replying-to {
  margin: 0 0 4px;
  font-size: 13px;
  color: var(--muted);
}

.replying-to a {
  color: var(--accent);
  text-decoration: none;
}
```

(If `.tab` is styled only for `button` elements, ensure the selector also applies to the `Link` anchors: add `a.tab { text-decoration: none; display: inline-flex; align-items: center; }` if tabs look off.)

- [ ] **Step 4: Typecheck and verify**

Run: `npm run typecheck` — Expected: no errors.
In the app: open `/profile/<your username>` (type the URL) — header shows counts and join date, Tweets tab lists your tweets, Replies tab shows your replies under their parent tweets, Follow works on another user's profile, `/profile/nosuchuser` shows the doesn't-exist panel.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/ProfileView.tsx frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(frontend): profile page with tweets and replies tabs"
```

---

### Task 11: Frontend — EditProfileModal (avatar upload + bio)

**Files:**
- Create: `frontend/src/EditProfileModal.tsx`
- Modify: `frontend/src/ProfileView.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `updateProfile(bio)`, `uploadAvatar(file)`, `resolveMediaUrl` from `./api`; `UserProfile` type.
- Produces: `EditProfileModal({ profile, onClose, onSaved })` where `onSaved(profile: UserProfile)` receives the updated profile.

- [ ] **Step 1: Create `frontend/src/EditProfileModal.tsx`**

```tsx
import { FormEvent, useEffect, useRef, useState } from "react";
import { Camera, X } from "lucide-react";
import { resolveMediaUrl, updateProfile, uploadAvatar } from "./api";
import type { UserProfile } from "./types";
import { getErrorMessage } from "./components";

export function EditProfileModal({
  profile,
  onClose,
  onSaved,
}: {
  profile: UserProfile;
  onClose: () => void;
  onSaved: (profile: UserProfile) => void;
}) {
  const [bio, setBio] = useState(profile.bio ?? "");
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  const avatarSrc = previewUrl ?? resolveMediaUrl(profile.avatar_url);
  const remaining = 160 - bio.length;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      let updated = profile;
      if (file) {
        updated = await uploadAvatar(file);
      }
      if (bio !== (profile.bio ?? "")) {
        updated = await updateProfile(bio);
      }
      onSaved(updated);
      onClose();
    } catch (err) {
      setError(getErrorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-profile-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <X size={20} aria-hidden="true" />
          </button>
          <h2 id="edit-profile-title">Edit profile</h2>
          <button
            className="primary-button compact"
            form="edit-profile-form"
            disabled={saving}
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </header>
        <form id="edit-profile-form" onSubmit={handleSubmit} className="modal-body">
          <div className="edit-avatar">
            {avatarSrc ? (
              <img className="avatar large avatar-image" src={avatarSrc} alt="Avatar preview" />
            ) : (
              <div className="avatar large" aria-hidden="true">
                {profile.username.slice(0, 1).toUpperCase()}
              </div>
            )}
            <button
              type="button"
              className="icon-button edit-avatar-button"
              onClick={() => fileInputRef.current?.click()}
              aria-label="Choose profile picture"
            >
              <Camera size={20} aria-hidden="true" />
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              hidden
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </div>
          <label className="edit-bio">
            <span>Bio</span>
            <textarea
              value={bio}
              onChange={(event) => setBio(event.target.value)}
              maxLength={160}
              rows={3}
              placeholder="Describe yourself"
            />
            <span className={remaining < 20 ? "counter warn" : "counter"}>{remaining}</span>
          </label>
          {error ? <p className="form-error">{error}</p> : null}
        </form>
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Wire it into ProfileView**

In `frontend/src/ProfileView.tsx`:

1. Add `import { EditProfileModal } from "./EditProfileModal";`
2. Add state: `const [editing, setEditing] = useState(false);`
3. Remove the `void currentUser; void onCurrentUserChange;` placeholder from Task 10.
4. Replace the disabled Edit button with:

```tsx
<button className="outline-button" onClick={() => setEditing(true)}>
  Edit profile
</button>
```

5. Before the closing `</section>` of the profile view, render:

```tsx
{editing && profile.is_current_user ? (
  <EditProfileModal
    profile={profile}
    onClose={() => setEditing(false)}
    onSaved={(updated) => {
      setProfile(updated);
      if (updated.id === currentUser.id) {
        onCurrentUserChange({
          ...currentUser,
          avatar_url: updated.avatar_url,
        });
      }
    }}
  />
) : null}
```

- [ ] **Step 3: Add modal CSS**

Append to `frontend/src/styles.css`:

```css
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(91, 112, 131, 0.4);
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 8vh;
  z-index: 30;
}

.modal {
  background: var(--panel);
  border-radius: 16px;
  width: min(560px, calc(100vw - 32px));
  box-shadow: var(--shadow);
  overflow: hidden;
}

.modal-header {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 10px 14px;
}

.modal-header h2 {
  margin: 0;
  font-size: 18px;
  flex: 1;
}

.modal-body {
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.edit-avatar {
  position: relative;
  width: 128px;
}

.edit-avatar-button {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  background: rgba(15, 20, 25, 0.6);
  color: #ffffff;
}

.edit-bio {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.edit-bio span:first-child {
  font-size: 13px;
  color: var(--muted);
}

.edit-bio textarea {
  resize: vertical;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--input);
  color: var(--ink);
  padding: 10px 12px;
  font: inherit;
}

.edit-bio .counter {
  align-self: flex-end;
}
```

- [ ] **Step 4: Typecheck and verify**

Run: `npm run typecheck` — Expected: no errors.
In the app: on your own profile, Edit profile opens the modal; picking an image shows the preview; Save uploads it and the header avatar, tweet-card avatars, and (after Task 12) the rail avatar show the image; bio saves and renders; a >2 MB or non-image file shows the backend error in the modal.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/EditProfileModal.tsx frontend/src/ProfileView.tsx frontend/src/styles.css
git commit -m "feat(frontend): edit profile modal with avatar upload"
```

---

### Task 12: Frontend — navigate to profiles from avatars/usernames

**Files:**
- Modify: `frontend/src/components.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: routes from Task 10; `Link` from react-router-dom.
- Produces: profile navigation from every avatar/username in tweet cards, comment cards, tweet detail, the People panel, and the rail user block.

- [ ] **Step 1: Link authors in components.tsx**

Add `import { Link } from "react-router-dom";` to `frontend/src/components.tsx`.

In `TweetCard`, replace the avatar div and author `<strong>`:

```tsx
      <Link
        to={`/profile/${tweet.author.username}`}
        className="author-link"
        onClick={(event) => event.stopPropagation()}
        aria-label={`View profile of ${tweet.author.username}`}
      >
        <Avatar user={tweet.author} />
      </Link>
```

and in the header:

```tsx
          <Link
            to={`/profile/${tweet.author.username}`}
            className="author-link"
            onClick={(event) => event.stopPropagation()}
          >
            <strong>@{tweet.author.username}</strong>
          </Link>
```

In `CommentCard`, wrap the avatar and the author `<strong>` the same way using `localComment.author.username`.

- [ ] **Step 2: Link authors in App.tsx**

- `TweetDetail` author block: wrap `<Avatar user={tweet.author} />` and `<strong>@{tweet.author.username}</strong>` each in `<Link to={`/profile/${tweet.author.username}`} className="author-link">`.
- `UserDiscoveryPanel` rows: wrap the avatar + `.user-copy` block in `<Link to={`/profile/${user.username}`} className="author-link user-row-link">`.
- Rail user block in `AppLayout`: wrap the `@username` block in `<Link to={`/profile/${currentUser.username}`} className="author-link">` and render `<Avatar user={currentUser} size="small" />` next to it so your own avatar shows in the rail.

- [ ] **Step 3: Add link CSS**

Append to `frontend/src/styles.css`:

```css
.author-link {
  color: inherit;
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.author-link:hover strong {
  text-decoration: underline;
}

.user-row-link {
  flex: 1;
  min-width: 0;
}
```

- [ ] **Step 4: Typecheck and verify**

Run: `npm run typecheck` — Expected: no errors.
In the app: clicking any avatar or @username (feed card, comment, tweet detail, People panel, rail) navigates to that profile; clicking elsewhere on a tweet card still opens the tweet detail.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components.tsx frontend/src/App.tsx frontend/src/styles.css
git commit -m "feat(frontend): navigate to profiles from avatars and usernames"
```

---

### Task 13: End-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend test suite**

Run (from `backend/`): `uv run pytest -v`
Expected: all tests PASS.

- [ ] **Step 2: Frontend typecheck + build**

Run (from `frontend/`): `npm run typecheck && npm run build`
Expected: no errors.

- [ ] **Step 3: Drive the app end-to-end**

With backend and frontend running, in a browser:

1. Register user `carol`, post two tweets.
2. Open own profile via the rail — banner, avatar initial, join date, `0 Following · 0 Followers`, 2 Tweets.
3. Edit profile: upload a PNG avatar, set bio "hello world", Save — header, rail, and tweet cards show the image; bio renders.
4. Re-open the edit modal and pick an image larger than 2 MB — the backend's "avatar must be 2 MB or smaller" error shows inline in the modal.
5. Log out; register `dave`. Open a tweet of carol's, reply to it.
6. Visit `/profile/carol`: Follow button follows (count increments, button flips to Following); avatar image visible.
7. Visit `/profile/dave/replies`: the reply renders beneath carol's tweet with "Replying to @carol"; liking the parent tweet from there works.
8. Refresh on `/profile/carol` — page loads directly; browser Back/Forward navigates correctly.

- [ ] **Step 4: Update the design doc status and finish**

If everything passes, the feature is complete. Use the superpowers:finishing-a-development-branch skill (or ask the user) for merge/PR next steps.
