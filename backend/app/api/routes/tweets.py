from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_id
from app.core.rate_limit import rate_limiter
from app.db.database import get_db
from app.repositories import tweet_repository, user_repository
from app.schemas.tweet import TweetCreate, TweetOut
from app.services.timeline_service import TimelineService, enqueue_feed_fanout_job

router = APIRouter(prefix="/tweets", tags=["tweets"])


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
            "cursor_created_at": tweet.created_at,
            "cursor_id": tweet.id,
        }
    )
