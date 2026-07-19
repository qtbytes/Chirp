"""
The moderation queue: the consumer side of ``POST /reports``.

Access is gated by ``users.is_moderator``, which only the operator can grant
(deploy/set_moderator.py). A non-moderator gets **404, never 403**, for the
same reason a block does: a 403 would confirm the surface exists.

Actions judge the *post* and close all of its open reports together --
``dismiss`` (nothing wrong) or ``takedown`` (hide the post). A takedown is
reversible (``restore``) because the post row survives; cached first-page
timelines may show a taken-down post until their short TTL expires.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user_id
from app.db.database import get_db
from app.models.post import Post
from app.models.user import User
from app.repositories import report_repository
from app.schemas.report import (
    ModerationActionOut,
    ModerationPostOut,
    ModerationQueueItem,
    ModerationQueuePage,
    ModerationReportOut,
)
from app.schemas.user import UserSummary
from app.services.timeline_service import decode_cursor, encode_cursor

router = APIRouter(prefix="/moderation", tags=["moderation"])


def get_current_moderator(
    current_user_id: int = Depends(get_current_user_id),
    db: Session = Depends(get_db),
) -> User:
    """The caller as a moderator, or 404 -- the surface stays undisclosed."""
    user = db.get(User, current_user_id)
    if user is None or user.is_deleted or not user.is_moderator:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Not Found"
        )
    return user


def _serialize_item(row: dict) -> ModerationQueueItem:
    post: Post = row["post"]
    return ModerationQueueItem(
        post=ModerationPostOut(
            id=post.id,
            content=post.content,
            media_urls=post.media_urls or [],
            created_at=post.created_at,
            author=UserSummary.model_validate(post.author),
            is_reply=post.reply_to_id is not None,
            thread_id=post.root_id or post.id,
            taken_down=post.is_taken_down,
        ),
        report_count=row["report_count"],
        latest_report_at=row["latest_at"],
        reports=[
            ModerationReportOut(
                id=report.id,
                reporter=UserSummary.model_validate(reporter),
                reason=report.reason,
                details=report.details,
                created_at=report.created_at,
                status=report.status,
            )
            for report, reporter in row["reports"]
        ],
    )


@router.get("/reports", response_model=ModerationQueuePage)
def list_report_queue(
    status_filter: Literal["open", "resolved"] = Query(default="open", alias="status"),
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    _moderator: User = Depends(get_current_moderator),
    db: Session = Depends(get_db),
) -> ModerationQueuePage:
    """
    Reported posts, grouped by post, newest report first.

    ``status=open`` is the working queue; ``status=resolved`` shows judged
    posts -- where a wrong takedown can be found and restored.
    """
    cursor_latest_at, cursor_post_id = decode_cursor(cursor)
    if cursor and (cursor_latest_at is None or cursor_post_id is None):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="invalid cursor"
        )

    rows = report_repository.list_report_queue(
        db,
        status=status_filter,
        limit=limit,
        cursor_latest_at=cursor_latest_at,
        cursor_post_id=cursor_post_id,
    )

    has_next = len(rows) > limit
    page_rows = rows[:limit]
    next_cursor = None
    if has_next and page_rows:
        last = page_rows[-1]
        next_cursor = encode_cursor(last["latest_at"], last["post"].id)

    return ModerationQueuePage(
        items=[_serialize_item(row) for row in page_rows],
        next_cursor=next_cursor,
    )


def _get_post_or_404(db: Session, post_id: int) -> Post:
    post = db.get(Post, post_id)
    if post is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="post not found"
        )
    return post


@router.post("/posts/{post_id}/dismiss", response_model=ModerationActionOut)
def dismiss_reports(
    post_id: int,
    moderator: User = Depends(get_current_moderator),
    db: Session = Depends(get_db),
) -> ModerationActionOut:
    """Nothing wrong with the post: close its open reports, change nothing else."""
    post = _get_post_or_404(db, post_id)
    resolved = report_repository.dismiss_reports(db, post.id, moderator.id)
    return ModerationActionOut(
        post_id=post.id, taken_down=post.is_taken_down, resolved_reports=resolved
    )


@router.post("/posts/{post_id}/takedown", response_model=ModerationActionOut)
def take_down_post(
    post_id: int,
    moderator: User = Depends(get_current_moderator),
    db: Session = Depends(get_db),
) -> ModerationActionOut:
    """Hide the post everywhere and close its open reports as actioned."""
    post = _get_post_or_404(db, post_id)
    resolved = report_repository.take_down_post(db, post, moderator.id)
    return ModerationActionOut(
        post_id=post.id, taken_down=True, resolved_reports=resolved
    )


@router.post("/posts/{post_id}/restore", response_model=ModerationActionOut)
def restore_post(
    post_id: int,
    _moderator: User = Depends(get_current_moderator),
    db: Session = Depends(get_db),
) -> ModerationActionOut:
    """Reverse a takedown. Already-resolved reports stay resolved."""
    post = _get_post_or_404(db, post_id)
    report_repository.restore_post(db, post)
    return ModerationActionOut(
        post_id=post.id, taken_down=False, resolved_reports=0
    )
