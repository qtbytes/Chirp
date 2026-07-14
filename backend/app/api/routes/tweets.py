from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import insert, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_id
from app.core.rate_limit import rate_limiter
from app.db.database import get_db
from app.models.post import Post
from app.models.post_view import PostView
from app.repositories import (
    block_repository,
    post_repository,
    tweet_repository,
    user_repository,
)
from app.repositories.visibility import can_view_post, can_view_thread
from app.schemas.tweet import TweetCreate, TweetOut, TweetStatsOut
from app.schemas.user import UserSummary
from app.services.serializers import serialize_quoted_post
from app.services.timeline_service import TimelineService, enqueue_feed_fanout_job

router = APIRouter(prefix="/tweets", tags=["tweets"])

# A user's repeat views of the same post inside this window are collapsed into
# one; after it passes, viewing the post again counts as a fresh view.
VIEW_DEDUP_WINDOW = timedelta(minutes=30)


@router.get("/stats", response_model=list[TweetStatsOut])
def list_tweet_stats(
    ids: str = Query(..., min_length=1),
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[TweetStatsOut]:
    try:
        tweet_ids = [int(value) for value in ids.split(",") if value.strip()]
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids must be comma-separated integers",
        ) from exc

    if not tweet_ids or any(tweet_id <= 0 for tweet_id in tweet_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids must contain positive integers",
        )
    if len(tweet_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ids supports at most 100 tweets",
        )

    return [
        TweetStatsOut(**stats)
        for stats in tweet_repository.list_tweet_stats(
            db,
            tweet_ids=tweet_ids,
            current_user_id=current_user_id,
        )
    ]


class _RecordViewsRequest(BaseModel):
    ids: list[int]


class PostViewCountOut(BaseModel):
    id: int
    view_count: int


@router.post("/views", response_model=list[PostViewCountOut])
def record_views(
    payload: _RecordViewsRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[PostViewCountOut]:
    """Record one view per post and return the authoritative counts.

    Views count engagements only (detail opens, clicks) -- the frontend does
    not report feed/list renders. Repeats by the same user are collapsed
    inside VIEW_DEDUP_WINDOW: the first engagement writes a post_views row,
    and further reports for that (user, post) pair are ignored while a row
    that recent exists -- so hammering the like button can't inflate the
    count, but coming back to the post later counts as a fresh view,
    Twitter-style.

    The response carries the resulting view_count for every requested post
    that exists, so the client can display the persisted value instead of an
    optimistic guess (which would be wrong whenever the view was a repeat).
    """
    if not payload.ids:
        return []
    requested = list({pid for pid in payload.ids if pid > 0})[:100]
    if not requested:
        return []
    post_ids = db.scalars(select(Post.id).where(Post.id.in_(requested))).all()
    if not post_ids:
        return []
    window_start = datetime.now(timezone.utc) - VIEW_DEDUP_WINDOW
    already_viewed = set(
        db.scalars(
            select(PostView.post_id).where(
                PostView.user_id == current_user_id,
                PostView.post_id.in_(post_ids),
                PostView.created_at >= window_start,
            )
        )
    )
    new_ids = [pid for pid in post_ids if pid not in already_viewed]
    if new_ids:
        now = datetime.now(timezone.utc)
        db.execute(
            insert(PostView),
            [
                {"user_id": current_user_id, "post_id": pid, "created_at": now}
                for pid in new_ids
            ],
        )
        db.execute(
            update(Post)
            .where(Post.id.in_(new_ids))
            .values(view_count=Post.view_count + 1)
        )
        db.commit()
    rows = db.execute(
        select(Post.id, Post.view_count).where(Post.id.in_(post_ids))
    ).all()
    return [
        PostViewCountOut(id=post_id, view_count=view_count)
        for post_id, view_count in rows
    ]


@router.post(
    "",
    response_model=TweetOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter("post_tweet"))],
)
def create_tweet(
    payload: TweetCreate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> TweetOut:
    """
    Create a tweet.

    Interview points:
    - Writing a tweet should stay fast.
    - Fan-out on write is enqueued to RQ so it runs outside the request path.
    - Rate limiting protects the posting API under high concurrency.
    """
    if user_repository.get_user(db, current_user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user not found",
        )

    try:
        tweet = tweet_repository.create_tweet(
            db,
            author_id=current_user_id,
            content=payload.content,
            media_urls=payload.media_urls,
            quoted_post_id=payload.quoted_post_id,
            visibility=payload.visibility,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    enqueue_feed_fanout_job(
        tweet_id=tweet.id,
        author_id=current_user_id,
    )

    service = TimelineService(db, viewer_id=current_user_id)
    return service.serialize_tweet(
        {
            "tweet": tweet,
            "like_count": 0,
            "comment_count": 0,
            "retweet_count": 0,
            "liked_by_me": False,
            "cursor_created_at": tweet.created_at,
            "cursor_id": tweet.id,
        }
    )


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

    # A blocked (or blocking) author's tweet is not viewable -- 404, not 403, so
    # the block is not disclosed.
    if block_repository.blocks_between(db, current_user_id, tweet.author.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="tweet not found",
        )

    # A followers-only or private thread the viewer can't see is 404 too -- the
    # audience is enforced on the thread's root.
    if not can_view_thread(db, current_user_id, tweet):
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
        "view_count": 0,
        "liked_by_me": False,
    }

    return TweetOut(
        id=tweet.id,
        content=tweet.content,
        media_urls=tweet.media_urls or [],
        created_at=tweet.created_at,
        edited_at=tweet.edited_at,
        author=UserSummary.model_validate(tweet.author),
        like_count=stats["like_count"],
        comment_count=stats["comment_count"],
        retweet_count=stats["retweet_count"],
        view_count=stats["view_count"],
        liked_by_me=stats["liked_by_me"],
        quoted_post=(
            serialize_quoted_post(tweet.quoted_post)
            if can_view_post(db, current_user_id, tweet.quoted_post)
            else None
        ),
        visibility=tweet.visibility,
    )


@router.patch("/{tweet_id}", response_model=TweetOut)
def edit_tweet(
    tweet_id: int,
    payload: TweetCreate,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> TweetOut:
    """Edit a tweet's content/media. Only the author may edit."""
    existing = db.get(Post, tweet_id)
    if existing is not None and existing.reply_to_id is not None:
        # A reply is a comment; edit it via /comments.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="tweet not found"
        )
    try:
        tweet = post_repository.update_post(
            db,
            post_id=tweet_id,
            user_id=current_user_id,
            content=payload.content,
            media_urls=payload.media_urls,
            visibility=payload.visibility,
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="tweet not found"
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="you can only edit your own tweets",
        )

    stats_rows = tweet_repository.list_tweet_stats(
        db, tweet_ids=[tweet_id], current_user_id=current_user_id
    )
    stats = stats_rows[0] if stats_rows else {
        "like_count": 0,
        "comment_count": 0,
        "retweet_count": 0,
        "view_count": 0,
        "liked_by_me": False,
    }
    return TweetOut(
        id=tweet.id,
        content=tweet.content,
        media_urls=tweet.media_urls or [],
        created_at=tweet.created_at,
        edited_at=tweet.edited_at,
        author=UserSummary.model_validate(tweet.author),
        like_count=stats["like_count"],
        comment_count=stats["comment_count"],
        retweet_count=stats["retweet_count"],
        view_count=stats["view_count"],
        liked_by_me=stats["liked_by_me"],
        quoted_post=(
            serialize_quoted_post(tweet.quoted_post)
            if can_view_post(db, current_user_id, tweet.quoted_post)
            else None
        ),
        visibility=tweet.visibility,
    )


@router.delete("/{tweet_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tweet(
    tweet_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> None:
    """Delete a tweet and its whole thread. Only the author may delete."""
    existing = db.get(Post, tweet_id)
    if existing is not None and existing.reply_to_id is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="tweet not found"
        )
    try:
        post_repository.delete_post(
            db, post_id=tweet_id, user_id=current_user_id
        )
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="tweet not found"
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="you can only delete your own tweets",
        )
