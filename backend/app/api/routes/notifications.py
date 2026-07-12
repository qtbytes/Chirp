from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.db.redis_client import get_redis_client
from app.repositories import block_repository, notification_repository
from app.schemas.notification import NotificationOut, NotificationPage, UnreadCountOut
from app.services import events
from app.services.timeline_service import decode_cursor, encode_cursor
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPage)
def list_notifications(
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> NotificationPage:
    cursor_created_at, cursor_id = decode_cursor(cursor)
    if cursor and (cursor_created_at is None or cursor_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid cursor",
        )

    rows = notification_repository.list_notifications(
        db,
        user_id=current_user_id,
        limit=limit,
        cursor_created_at=cursor_created_at,
        cursor_id=cursor_id,
        exclude_actor_ids=block_repository.hidden_user_ids(db, current_user_id),
    )

    has_next = len(rows) > limit
    page_rows = rows[:limit]
    items = [NotificationOut.model_validate(row) for row in page_rows]

    next_cursor = None
    if has_next and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last["created_at"], last["id"])

    return NotificationPage(items=items, next_cursor=next_cursor)


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> UnreadCountOut:
    return UnreadCountOut(
        count=notification_repository.count_unread(db, user_id=current_user_id)
    )


@router.get("/stream")
def stream_notifications(
    current_user_id: int = Depends(get_current_user_id),
) -> StreamingResponse:
    """
    A Server-Sent Events stream of the caller's notification nudges.

    Requires Redis (the pub/sub backbone); without it we answer 503 so the client
    falls back to polling rather than holding a stream that can never deliver. The
    events carry no data of consequence -- each is a signal to re-read the
    authoritative unread count -- so a browser that cannot use SSE loses nothing
    but immediacy.
    """
    try:
        get_redis_client()
    except RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live updates require Redis, which is unavailable.",
        ) from exc
    return StreamingResponse(
        events.stream_user_events(current_user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Tell nginx not to buffer the stream, which would defeat SSE.
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{notification_id}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_one_read(
    notification_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> None:
    """Mark a single notification read. 404 if it is not the caller's."""
    marked = notification_repository.mark_read(
        db, user_id=current_user_id, notification_id=notification_id
    )
    if not marked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="notification not found",
        )


@router.post("/mark-read", status_code=status.HTTP_200_OK)
def mark_read(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> dict:
    updated = notification_repository.mark_all_read(db, user_id=current_user_id)
    return {"updated": updated}
