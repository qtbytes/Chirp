from typing import Literal

from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.repositories import block_repository, hashtag_repository, tweet_repository
from app.schemas.tweet import HashtagPostsPage, TrendingHashtagOut
from app.services import trending_service
from app.services.timeline_service import TimelineService, decode_cursor, encode_cursor
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/hashtags", tags=["hashtags"])


def _normalize_tag(raw: str) -> str:
    """Match how tags are stored (lowercase, no leading ``#``)."""
    return raw.strip().lstrip("#").lower()


@router.get("", response_model=list[TrendingHashtagOut])
def suggest_hashtags(
    query: str = Query(default="", max_length=140),
    limit: int = Query(default=8, ge=1, le=20),
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[TrendingHashtagOut]:
    """
    Prefix suggestions for the composer's ``#`` typeahead, most-used first.

    ``query`` is normalised the same way tags are stored, so ``#Te``, ``te``
    and ``TE`` all suggest ``#test``. An empty query suggests the most-used
    tags overall.
    """
    return [
        TrendingHashtagOut(**row)
        for row in hashtag_repository.search_tags(
            db,
            prefix=_normalize_tag(query),
            limit=limit,
        )
    ]


@router.get("/trending", response_model=list[TrendingHashtagOut])
def list_trending_hashtags(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> list[TrendingHashtagOut]:
    """The most-used hashtags in the recent window (global, cached)."""
    return [
        TrendingHashtagOut(**row) for row in trending_service.get_trending(db)
    ]


@router.get("/{tag}/posts", response_model=HashtagPostsPage)
def list_hashtag_posts(
    tag: str,
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    sort: Literal["recent", "top"] = "recent",
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> HashtagPostsPage:
    """
    The hashtag feed: top-level posts tagged ``#{tag}``.

    Membership comes from the ``post_hashtags`` rows recorded on write, not from
    a text search, so only posts that actually used the tag appear.

    ``sort=recent`` (default) is newest-first with the timelines'
    ``(created_at, id)`` cursor. ``sort=top`` reuses the "for you" ranker over a
    tag-scoped pool -- engagement (likes, retweets, comments, views) decayed by
    age, lifted by the viewer's affinity -- and pages on its rank cursor. The
    cursor is opaque and sort-specific, so keep ``sort`` fixed across one
    pagination run.
    """
    normalized = _normalize_tag(tag)
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid hashtag",
        )

    if sort == "top":
        service = TimelineService(db, viewer_id=current_user_id)
        try:
            page = service.get_for_you_timeline(
                limit=limit,
                cursor=cursor,
                user_id=current_user_id,
                tag=normalized,
            )
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid cursor",
            )
        return HashtagPostsPage(items=page.items, next_cursor=page.next_cursor)

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
