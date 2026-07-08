import time
from pathlib import Path

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.security import hash_password
from app.db.database import get_db
from app.models.user import User
from app.repositories import (
    engagement_repository,
    follow_repository,
    tweet_repository,
    user_repository,
)
from app.schemas.comment import CommentOut, ProfileRepliesPage, ReplyWithParentOut
from app.schemas.tweet import ProfileTweetsPage, TweetOut
from app.schemas.user import (
    UserCreate,
    UserDiscoveryOut,
    UserProfileOut,
    UserSummary,
    UserUpdate,
)
from app.services.timeline_service import TimelineService, decode_cursor, encode_cursor
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserSummary, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, db: Session = Depends(get_db)) -> UserSummary:
    """
    Create a user.

    Why keep this route simple?
    - The interview focus for this project is timeline/feed design.
    - A lightweight user route lets you create test users quickly.
    """
    try:
        user = user_repository.create_user(
            db,
            username=payload.username,
            password_hash=hash_password(payload.password),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    return UserSummary.model_validate(user)


@router.get("", response_model=list[UserDiscoveryOut])
def list_users(
    query: str | None = Query(default=None, min_length=1, max_length=50),
    limit: int = Query(default=10, ge=1, le=50),
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[UserDiscoveryOut]:
    rows = user_repository.list_users(
        db,
        current_user_id=current_user_id,
        query=query,
        limit=limit,
    )
    return [
        UserDiscoveryOut(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            created_at=user.created_at,
            is_following=is_following,
            is_current_user=user.id == current_user_id,
        )
        for user, is_following in rows
    ]


def _build_profile(db: Session, user: User, current_user_id: int) -> UserProfileOut:
    return UserProfileOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
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

    rows = tweet_repository.list_feed_with_retweets(
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

    items = []
    for row in page_rows:
        comment = row["comment"]
        tweet = row["tweet"]
        retweeter = row.get("retweeted_by")
        c_stats = comment_stats.get(comment.id, empty)
        t_stats = tweet_stats.get(tweet.id, empty)
        items.append(
            ReplyWithParentOut(
                comment=CommentOut(
                    id=comment.id,
                    tweet_id=comment.tweet_id,
                    parent_comment_id=comment.parent_comment_id,
                    content=comment.content,
                    media_urls=comment.media_urls or [],
                    created_at=comment.created_at,
                    author=UserSummary.model_validate(row["comment_author"]),
                    like_count=c_stats["like_count"],
                    comment_count=c_stats["comment_count"],
                    retweet_count=c_stats["retweet_count"],
                    liked_by_me=c_stats["liked_by_me"],
                    retweeted_by=(
                        UserSummary.model_validate(retweeter)
                        if retweeter is not None
                        else None
                    ),
                ),
                parent_tweet=TweetOut(
                    id=tweet.id,
                    content=tweet.content,
                    media_urls=tweet.media_urls or [],
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


@router.patch("/me", response_model=UserProfileOut)
def update_current_user(
    payload: UserUpdate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> UserProfileOut:
    try:
        user = user_repository.update_user_profile(
            db,
            user_id=current_user_id,
            fields=payload.model_dump(exclude_unset=True),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    return _build_profile(db, user, current_user_id)


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

    # Read in bounded chunks so an oversized upload is rejected before the
    # full body is ever held in memory, rather than trusting a client-
    # supplied Content-Length header.
    chunks = bytearray()
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        chunks.extend(chunk)
        if len(chunks) > MAX_AVATAR_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="avatar must be 2 MB or smaller",
            )
    data = bytes(chunks)

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
