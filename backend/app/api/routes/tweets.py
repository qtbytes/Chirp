from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_id
from app.core.rate_limit import rate_limiter
from app.db.database import get_db
from app.models.post import Post
from app.repositories import post_repository, tweet_repository, user_repository
from app.schemas.tweet import TweetCreate, TweetOut, TweetStatsOut
from app.schemas.user import UserSummary
from app.services.timeline_service import TimelineService, enqueue_feed_fanout_job

router = APIRouter(prefix="/tweets", tags=["tweets"])


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


@router.post(
    "",
    response_model=TweetOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(rate_limiter("post_tweet", max_requests=10, window_seconds=60))
    ],
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

    tweet = tweet_repository.create_tweet(
        db,
        author_id=current_user_id,
        content=payload.content,
        media_urls=payload.media_urls,
    )

    enqueue_feed_fanout_job(
        tweet_id=tweet.id,
        author_id=current_user_id,
    )

    service = TimelineService(db)
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
        media_urls=tweet.media_urls or [],
        created_at=tweet.created_at,
        edited_at=tweet.edited_at,
        author=UserSummary.model_validate(tweet.author),
        like_count=stats["like_count"],
        comment_count=stats["comment_count"],
        retweet_count=stats["retweet_count"],
        liked_by_me=stats["liked_by_me"],
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
        liked_by_me=stats["liked_by_me"],
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
