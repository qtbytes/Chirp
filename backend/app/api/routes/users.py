from app.api.deps import get_current_user_id
from app.core.security import hash_password
from app.db.database import get_db
from app.models.user import User
from app.repositories import follow_repository, tweet_repository, user_repository
from app.schemas.tweet import ProfileTweetsPage
from app.schemas.user import (
    UserCreate,
    UserDiscoveryOut,
    UserProfileOut,
    UserSummary,
)
from app.services.timeline_service import TimelineService, decode_cursor, encode_cursor
from fastapi import APIRouter, Depends, HTTPException, Query, status
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
