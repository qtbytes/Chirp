from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.repositories import block_repository, tweet_repository
from app.schemas.tweet import HashtagPostsPage
from app.services.timeline_service import TimelineService, decode_cursor, encode_cursor
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/hashtags", tags=["hashtags"])


def _normalize_tag(raw: str) -> str:
    """Match how tags are stored (lowercase, no leading ``#``)."""
    return raw.strip().lstrip("#").lower()


@router.get("/{tag}/posts", response_model=HashtagPostsPage)
def list_hashtag_posts(
    tag: str,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> HashtagPostsPage:
    """
    The hashtag feed: top-level posts tagged ``#{tag}``, newest-first.

    Membership comes from the ``post_hashtags`` rows recorded on write, not from
    a text search, so only posts that actually used the tag appear. Same
    cursor-pagination and block/deleted-author visibility as the timelines.
    """
    normalized = _normalize_tag(tag)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid hashtag",
        )

    cursor_created_at, cursor_id = decode_cursor(cursor)
    if cursor and (cursor_created_at is None or cursor_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid cursor",
        )

    rows = tweet_repository.list_tweets_by_hashtag(
        db,
        tag=normalized,
        limit=limit,
        current_user_id=current_user_id,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
        exclude_author_ids=block_repository.hidden_user_ids(db, current_user_id),
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

    return HashtagPostsPage(items=items, next_cursor=next_cursor)
