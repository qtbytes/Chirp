from app.api.deps import get_current_user_id
from app.core.config import settings
from app.core.rate_limit import rate_limiter
from app.db.database import get_db
from app.repositories import block_repository, search_repository
from app.schemas.search import SearchPage, SearchPostOut
from app.schemas.user import UserSummary
from app.services.serializers import serialize_quoted_post
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

router = APIRouter(prefix="/search", tags=["search"])


@router.get(
    "",
    response_model=SearchPage,
    dependencies=[Depends(rate_limiter("search"))],
)
def search_posts(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(default=settings.timeline_page_size, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> SearchPage:
    """
    Full-text search over post content (tweets and replies), most-relevant first.

    Uses the same page-and-cursor shape as the timelines, but the cursor keys on
    BM25 relevance ``(score, id)`` rather than time -- see search_repository.
    """
    cursor_score, cursor_id = search_repository.decode_search_cursor(cursor)
    if cursor and (cursor_score is None or cursor_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid cursor",
        )

    match = search_repository.build_match(q)
    if match is None:
        # Nothing searchable in the query (e.g. only punctuation).
        return SearchPage(items=[], next_cursor=None)

    rows = search_repository.search_posts(
        db,
        match=match,
        limit=limit,
        cursor_score=cursor_score,
        cursor_id=cursor_id,
        current_user_id=current_user_id,
        exclude_author_ids=block_repository.hidden_user_ids(db, current_user_id),
    )

    has_next = len(rows) > limit
    page_rows = rows[:limit]

    items = [
        SearchPostOut(
            id=row["post"].id,
            content=row["post"].content,
            media_urls=row["post"].media_urls or [],
            created_at=row["post"].created_at,
            edited_at=row["post"].edited_at,
            author=UserSummary.model_validate(row["post"].author),
            like_count=row["like_count"],
            comment_count=row["comment_count"],
            retweet_count=row["retweet_count"],
            liked_by_me=row["liked_by_me"],
            quoted_post=serialize_quoted_post(row["post"].quoted_post),
            is_reply=row["is_reply"],
            thread_id=row["thread_id"],
        )
        for row in page_rows
    ]

    next_cursor = None
    if has_next and page_rows:
        last = page_rows[-1]
        next_cursor = search_repository.encode_search_cursor(
            last["cursor_score"], last["cursor_id"]
        )

    return SearchPage(items=items, next_cursor=next_cursor)
