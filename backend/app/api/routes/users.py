import time
from pathlib import Path

from app.api.deps import get_current_user_id
from app.core.config import settings
from app.db.database import get_db
from app.models.user import User
from app.repositories import (
    block_repository,
    engagement_repository,
    follow_repository,
    mute_repository,
    tweet_repository,
    user_repository,
)
from app.schemas.comment import CommentOut, ProfileRepliesPage, ReplyWithParentOut
from app.schemas.follow import FollowListPage
from app.schemas.tweet import ProfileTweetsPage, TweetOut
from app.schemas.user import (
    UserDiscoveryOut,
    UserProfileOut,
    UserSummary,
    UserUpdate,
)
from app.services.serializers import serialize_quoted_post
from app.services.timeline_service import TimelineService, decode_cursor, encode_cursor
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/users", tags=["users"])


# There is deliberately no POST /users. It used to exist as a "lightweight route
# to create test users quickly", which made it an unauthenticated, unthrottled
# duplicate of POST /auth/register -- a way around that endpoint's rate limit,
# and now a way to create accounts that never receive a verification mail.
# Register through /auth/register.


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
        exclude_user_ids=block_repository.hidden_user_ids(db, current_user_id),
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
    is_current_user = user.id == current_user_id

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
        is_current_user=is_current_user,
        # Whether *you* have blocked them, so the UI can offer "Unblock". Their
        # having blocked you is deliberately not surfaced as a flag -- its effect
        # (their content simply isn't there) is the only signal.
        is_blocked=block_repository.is_blocking(db, current_user_id, user.id),
        is_muted=mute_repository.is_muting(db, current_user_id, user.id),
        # Profiles are world-readable; an address is the owner's business alone.
        email=user.email if is_current_user else None,
        pending_email=user.pending_email if is_current_user else None,
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

    # A block hides the profile's posts in both directions: you cannot read the
    # tweets of someone you blocked, nor of someone who blocked you.
    if block_repository.blocks_between(db, current_user_id, user.id):
        return ProfileTweetsPage(items=[], next_cursor=None)

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

    if block_repository.blocks_between(db, current_user_id, user.id):
        return ProfileRepliesPage(items=[], next_cursor=None)

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
    comment_stats = {
        stats["id"]: stats
        for stats in engagement_repository.list_comment_stats(
            db, comment_ids=comment_ids, current_user_id=current_user_id
        )
    }

    # The parent may be a top-level tweet or another comment; each has its own
    # count semantics, so stat it with the matching method.
    parent_posts = {row["tweet"].id: row["tweet"] for row in page_rows}
    tweet_parent_ids = [pid for pid, p in parent_posts.items() if p.reply_to_id is None]
    comment_parent_ids = [
        pid for pid, p in parent_posts.items() if p.reply_to_id is not None
    ]
    parent_stats: dict[int, dict] = {}
    for stats in tweet_repository.list_tweet_stats(
        db, tweet_ids=tweet_parent_ids, current_user_id=current_user_id
    ):
        parent_stats[stats["id"]] = stats
    for stats in engagement_repository.list_comment_stats(
        db, comment_ids=comment_parent_ids, current_user_id=current_user_id
    ):
        parent_stats[stats["id"]] = stats

    empty = {"like_count": 0, "comment_count": 0, "retweet_count": 0, "liked_by_me": False}

    items = []
    for row in page_rows:
        comment = row["comment"]
        tweet = row["tweet"]
        c_stats = comment_stats.get(comment.id, empty)
        t_stats = parent_stats.get(tweet.id, empty)
        items.append(
            ReplyWithParentOut(
                comment=CommentOut(
                    id=comment.id,
                    tweet_id=comment.tweet_id,
                    parent_comment_id=comment.parent_comment_id,
                    content=comment.content,
                    media_urls=comment.media_urls or [],
                    created_at=comment.created_at,
                    edited_at=comment.edited_at,
                    author=UserSummary.model_validate(row["comment_author"]),
                    like_count=c_stats["like_count"],
                    comment_count=c_stats["comment_count"],
                    retweet_count=c_stats["retweet_count"],
                    liked_by_me=c_stats["liked_by_me"],
                ),
                parent_tweet=TweetOut(
                    id=tweet.id,
                    content=tweet.content,
                    media_urls=tweet.media_urls or [],
                    created_at=tweet.created_at,
                    edited_at=tweet.edited_at,
                    author=UserSummary.model_validate(row["tweet_author"]),
                    like_count=t_stats["like_count"],
                    comment_count=t_stats["comment_count"],
                    liked_by_me=t_stats["liked_by_me"],
                    retweet_count=t_stats["retweet_count"],
                    quoted_post=serialize_quoted_post(tweet.quoted_post),
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


def _follow_list_page(
    db: Session,
    username: str,
    lister,
    limit: int,
    cursor: str | None,
    current_user_id: int,
) -> FollowListPage:
    """Shared body for the followers and following lists; only ``lister`` differs."""
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

    rows = lister(
        db,
        user_id=user.id,
        current_user_id=current_user_id,
        limit=limit,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
        exclude_user_ids=block_repository.hidden_user_ids(db, current_user_id),
    )

    has_next = len(rows) > limit
    page_rows = rows[:limit]

    items = [
        UserDiscoveryOut(
            id=row["user"].id,
            username=row["user"].username,
            display_name=row["user"].display_name,
            avatar_url=row["user"].avatar_url,
            created_at=row["user"].created_at,
            is_following=row["is_following"],
            is_current_user=row["user"].id == current_user_id,
        )
        for row in page_rows
    ]

    next_cursor = None
    if has_next and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last["follow_created_at"], last["user"].id)

    return FollowListPage(items=items, next_cursor=next_cursor)


@router.get("/{username}/followers", response_model=FollowListPage)
def list_user_followers(
    username: str,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> FollowListPage:
    return _follow_list_page(
        db, username, follow_repository.list_followers, limit, cursor, current_user_id
    )


@router.get("/{username}/following", response_model=FollowListPage)
def list_user_following(
    username: str,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> FollowListPage:
    return _follow_list_page(
        db, username, follow_repository.list_following, limit, cursor, current_user_id
    )


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
